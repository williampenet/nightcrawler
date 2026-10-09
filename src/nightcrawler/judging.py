"""Judging step of the pipeline (ADR-0006, ADR-0007, WIP-84).

After artist enrichment, when the event store answered: every published concert is judged against
William's written taste by the routed `judge_taste` model, unless its input (judge.input_hash) is
unchanged since the stored judgement. Verdicts go to the store only; the report gets counts. A
store or model failure never fails the run: the step is skipped and counted.
"""

from __future__ import annotations

import logging
import threading
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from . import judge, llm
from .models import Concert
from .store import verdicts

log = logging.getLogger(__name__)

DEFAULT_CALLS_PER_RUN = 600
WORKERS = 4
STOP_AFTER = 10  # failed calls in a row (unknown model, outage): the rest is left for next run


@dataclass
class Context:
    task: llm.Task | None
    calls: int = DEFAULT_CALLS_PER_RUN

    @classmethod
    def from_config(cls, models_path, calls: int = DEFAULT_CALLS_PER_RUN) -> Context:
        try:
            task = llm.load_tasks(models_path).get(judge.TASK)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("judging: no usable model config (%s)", type(exc).__name__)
            task = None
        return cls(task, calls)


def current_ratings(
    ratings: dict[str, tuple[str | None, Any]], concerts: list[Concert]
) -> dict[str, str]:
    """{published concert id: liked | disliked}: the latest rating among the concert's id and
    aliases (a current id wins over another concert's alias, as on the page); an unlike removes
    the rating. Ratings of concerts no longer published are not used (as in the eval)."""
    ids: dict[str, str] = {c.id: c.id for c in concerts}
    for c in concerts:
        for a in c.aliases:
            ids.setdefault(a, c.id)
    latest: dict[str, tuple[Any, str | None]] = {}
    for rid, (label, at) in ratings.items():
        cid = ids.get(rid)
        if cid is not None and (cid not in latest or at > latest[cid][0]):
            latest[cid] = (at, label)
    return {cid: label for cid, (_, label) in latest.items() if label}


def concert_input(c: Concert, description: str | None) -> dict:
    d = c.to_dict()
    if description:
        d["description"] = description
    return d


def run(
    database_url: str,
    concerts: list[Concert],
    artists: dict[str, Any],
    ctx: Context | None,
    connect: Callable | None = None,
    run_task: Callable = llm.run_task,
) -> dict:
    """Judges the concerts and saves the judgements; returns counts only (published report)."""
    if ctx is None or ctx.task is None:
        return {"status": "skipped: judge_taste not routed"}
    ok, why = ctx.task.primary.available()
    if not ok:
        return {"status": f"skipped: {why}"}
    if connect is None:
        import psycopg

        from .store.sync import CONNECT

        def connect(url):
            return psycopg.connect(url, autocommit=True, **CONNECT)

    ids = [c.id for c in concerts]
    try:
        with connect(database_url) as conn:
            inputs = verdicts.load_inputs(conn, ids)
    except Exception as exc:  # libpq messages name hosts: the type only
        return {"status": f"error: store read ({type(exc).__name__})"}
    taste = inputs.profile.get("taste_text") if isinstance(inputs.profile, dict) else None
    if not (isinstance(taste, str) and taste.strip()):
        return {"status": "skipped: the written taste is empty"}

    art = {k: (a.to_dict() if hasattr(a, "to_dict") else a) for k, a in artists.items()}
    by_id = {c.id: c for c in concerts}
    labels = current_ratings(inputs.ratings, concerts)
    rated = [
        (concert_input(by_id[i], inputs.descriptions.get(i)), lab) for i, lab in labels.items()
    ]
    todo, refresh, cached = [], [], 0
    for c in sorted(concerts, key=lambda c: c.start):  # soonest first: the cap hits the farthest
        target = concert_input(c, inputs.descriptions.get(c.id))
        examples = judge.pick_nearest(target, rated, art)
        messages = judge.messages_for(target, art, inputs.profile, examples)
        h = judge.input_hash(ctx.task, messages)
        stored = inputs.stored.get(c.id)
        if stored and stored["input_hash"] == h:
            cached += 1
            section = judge.section(stored)
            if section != stored["section"]:  # the rule changed: no new call needed
                refresh.append((c.id, section))
            continue
        todo.append((c, messages, h))

    capped = max(len(todo) - ctx.calls, 0)
    todo = todo[: ctx.calls]
    lock = threading.Lock()
    state = {"failed_in_a_row": 0}
    rejected: list[str] = []  # concerts whose new reason was unfaithful (WIP-90)
    failures: Counter = Counter()
    tokens = Counter()

    def one(item):
        c, messages, h = item
        with lock:
            if state["failed_in_a_row"] >= STOP_AFTER:
                failures["aborted"] += 1
                return None
        try:
            a = run_task(ctx.task, messages, judge.SCHEMA, judge.check_for(messages))
        except llm.ModelError:
            code = "transport"
            a = None
        except Exception as exc:  # one call never loses the run's other judgements
            log.warning("judging: call failed (%s)", type(exc).__name__)
            code = "error"
            a = None
        else:
            if a.data is not None and not a.errors:
                code = None
            elif a.data is not None and judge.UNFAITHFUL in a.errors:
                # left unjudged, its stored verdict (older prompt or inputs) deleted below: the
                # page shows it in « Pour toi », « pas encore jugé » (WIP-90)
                code = "unfaithful"
                rejected.append(c.id)
            else:
                code = a.reason or "check"
        with lock:
            if a is not None:
                tokens["in"] += a.tokens_in or 0
                tokens["out"] += a.tokens_out or 0
            if code:
                failures[code] += 1
                # an unfaithful reason is about one concert's data, not a broken model or an
                # outage: it never stops the run (schema and transport failures still do)
                if code != "unfaithful":
                    state["failed_in_a_row"] += 1
                return None
            state["failed_in_a_row"] = 0
        return {
            "concert_id": c.id,
            "input_hash": h,
            "verdict": a.data["verdict"],
            "confidence": int(a.data["confidence"]),
            "reason": a.data["reason"].strip()[:240],
            "section": judge.section(a.data),
            "model": ctx.task.primary.model,
            "starts_at": c.start,
        }

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        rows = [r for r in pool.map(one, todo) if r]
    try:
        with connect(database_url) as conn:
            verdicts.save(conn, rows, delete=[cid for cid in rejected if cid in inputs.known])
            verdicts.update_sections(conn, refresh)
    except Exception as exc:
        return {"status": f"error: store write ({type(exc).__name__})", "unsaved": len(rows)}
    report = {
        "status": "ok",
        "concerts": len(concerts),
        "judged": len(rows),
        "cached": cached,
        "capped": capped,
        "sections_refreshed": len(refresh),
        "unjudged_deleted": len([cid for cid in rejected if cid in inputs.known]),
        "failed": dict(failures),
        "ratings_used": dict(Counter(labels.values())),
        "tokens_in": tokens["in"],
        "tokens_out": tokens["out"],
    }
    return report
