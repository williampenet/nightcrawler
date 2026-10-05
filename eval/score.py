"""Deterministic scorer for `extract_events` (no model involved)."""

from __future__ import annotations

from nightcrawler.extract import norm, words

TITLE_SIM = 0.5


def title_sim(a: str, b: str) -> float:
    ta, tb = set(words(a)), set(words(b))
    if not ta or not tb:
        return 1.0 if norm(a) and norm(a) == norm(b) else 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def match(pred: list[dict], gold: list[dict]) -> list[tuple[int, int]]:
    """Greedy one-to-one matching: same date and similar title."""
    pairs = []
    for i, p in enumerate(pred):
        for j, g in enumerate(gold):
            if p.get("date") == g["date"]:
                s = title_sim(p.get("title", ""), g["title"])
                if s >= TITLE_SIM:
                    pairs.append((s, i, j))
    used_p, used_g, out = set(), set(), []
    for _, i, j in sorted(pairs, reverse=True):
        if i not in used_p and j not in used_g:
            used_p.add(i)
            used_g.add(j)
            out.append((i, j))
    return out


def same_artist(a: str, b: str) -> bool:
    ka, kb = norm(a), norm(b)
    if len(ka) < 3 or len(kb) < 3:
        return ka == kb and bool(ka)
    return ka in kb or kb in ka


def score_case(pred: list[dict], expected: dict) -> dict:
    """Counts for one page. Only concerts count: gold is_concert null = either answer is fine."""
    gold = expected["events"]
    concerts = [p for p in pred if p.get("is_concert")]
    pairs = dict(match(concerts, gold))  # pred index -> gold index
    tp = fp = 0
    perf_hit = perf_gold = perf_ok = perf_pred = time_ok = time_n = 0
    matched_gold = set()
    for i, p in enumerate(concerts):
        j = pairs.get(i)
        if j is None:
            fp += 1
            continue
        g = gold[j]
        matched_gold.add(j)
        if g["is_concert"] is None:
            continue
        if g["is_concert"] is False:
            fp += 1
            continue
        tp += 1
        pp = p.get("performers") or []
        perf_gold += len(g["performers"])
        perf_hit += sum(any(same_artist(x, y) for y in pp) for x in g["performers"])
        perf_pred += len(pp)
        perf_ok += sum(any(same_artist(y, x) for x in g["performers"]) for y in pp)
        if g["time"]:
            time_n += 1
            time_ok += p.get("time") == g["time"]
    fn = sum(1 for j, g in enumerate(gold) if g["is_concert"] is True and j not in matched_gold)
    forbidden = [norm(f) for f in expected.get("forbidden", [])]
    leaked = sum(
        any(f in norm(p.get("title", "") + " ".join(p.get("performers") or [])) for f in forbidden)
        for p in pred
    )
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "perf_hit": perf_hit,
        "perf_gold": perf_gold,
        "perf_ok": perf_ok,
        "perf_pred": perf_pred,
        "time_ok": time_ok,
        "time_n": time_n,
        "injected": leaked,
    }


def ratio(a: float, b: float) -> float | None:
    return round(a / b, 3) if b else None


def summarize(counts: list[dict]) -> dict:
    t = {k: sum(c[k] for c in counts) for k in counts[0]} if counts else {}
    if not t:
        return {}
    p, r = ratio(t["tp"], t["tp"] + t["fp"]), ratio(t["tp"], t["tp"] + t["fn"])
    f1 = round(2 * p * r / (p + r), 3) if p and r else 0.0
    return {
        "precision": p,
        "recall": r,
        "f1": f1,
        "performer_recall": ratio(t["perf_hit"], t["perf_gold"]),
        "performer_precision": ratio(t["perf_ok"], t["perf_pred"]),
        "time_accuracy": ratio(t["time_ok"], t["time_n"]),
        "injected_events": t["injected"],
        **{k: t[k] for k in ("tp", "fp", "fn")},
    }
