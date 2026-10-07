"""page_llm reader (WIP-66): a model reads "Mes salles" pages with no structured data.

Offline: the venue pages and the provider's OpenAI-compatible endpoint are mocked with respx,
so the real path runs (task router, schema validation, grounding checks).
"""

import dataclasses
import functools
import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest
import respx

from nightcrawler import extract, llm
from nightcrawler.config import _priority_venue, load_zone
from nightcrawler.events import build_concerts
from nightcrawler.http import Fetcher
from nightcrawler.models import Venue
from nightcrawler.sources import page_llm, priority
from nightcrawler.venues import attach_to_configured, configured_venue_ids, configured_venues

URL = "https://larayonne.org/agenda/"
API = "https://api.scaleway.ai/v1/chat/completions"
PAGE = """<html><body><h1>Agenda</h1><ul>
<li>jeu. 08 octobre</li><li>à partir de 19h</li><li>Didier Super Metal</li>
<li>mar. 13 octobre</li><li>18h30 &gt; 21h</li><li>L'emploi dans le spectacle vivant</li>
<li>mer. 14 octobre</li><li>à partir de 19h</li><li>FAKEAR</li>
</ul><script>ignore me</script></body></html>"""
ANSWER = {
    "events": [
        {"title": "Didier Super Metal", "date": "2026-10-08", "time": "19:00",
         "performers": ["Didier Super"], "is_concert": True},
        {"title": "L'emploi dans le spectacle vivant", "date": "2026-10-13", "time": "18:30",
         "performers": [], "is_concert": False},
        {"title": "FAKEAR", "date": "2026-10-14", "time": "19:00",
         "performers": ["Fakear"], "is_concert": True},
        # not on the page: dropped by the grounding checks
        {"title": "Daft Punk", "date": "2026-10-20", "time": "21:00",
         "performers": ["Daft Punk"], "is_concert": True},
    ]
}  # fmt: skip
ENTRY = _priority_venue(
    {"name": "La Rayonne", "venue": "La Rayonne", "reader": {"type": "page_llm", "urls": [URL]}}
)
NOW = (2026, 10, 7, 9)


def _task() -> llm.Task:
    spec = llm.ModelSpec(provider="scaleway", model="test-model", revision="r1")
    return llm.Task(name=extract.TASK, primary=spec)


def _completion(data: dict) -> httpx.Response:
    body = {"choices": [{"message": {"content": json.dumps(data)}}], "usage": {}}
    return httpx.Response(200, json=body)


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("SCW_GENAI_SECRET_KEY", "test-key")
    monkeypatch.delenv("SCW_DEFAULT_PROJECT_ID", raising=False)


def _site(page=PAGE, host="larayonne.org", url=URL):
    respx.get(f"https://{host}/robots.txt").respond(404)
    return respx.get(url).respond(200, html=page)


def _read(tz, ctx, entry=ENTRY, days=60):
    fetcher = Fetcher(cache_dir=None, min_interval=0)
    now = datetime(*NOW, tzinfo=tz)
    return page_llm.read(entry["reader"], entry["venue"], fetcher, now, tz, days, ctx)


def _chunk_of(request: httpx.Request) -> list[str]:
    content = json.loads(request.content)["messages"][1]["content"]
    return content.split("<<<PAGE\n", 1)[1].rsplit("\nPAGE>>>", 1)[0].split("\n")


@respx.mock
def test_happy_path_keeps_grounded_concerts_only(tz):
    _site()
    api = respx.post(API).mock(return_value=_completion(ANSWER))
    events, pages, status = _read(tz, page_llm.Context(_task()))
    assert [(e.title, e.start.isoformat()) for e in events] == [
        ("Didier Super Metal", "2026-10-08T19:00:00+02:00"),
        ("FAKEAR", "2026-10-14T19:00:00+02:00"),
    ]  # the workshop (is_concert false) and the invented event are dropped
    ev = events[0]
    assert ev.source == ev.venue_id == "page_llm:larayonne.org"
    assert (ev.location_name, ev.url, ev.performers) == ("La Rayonne", URL, ["Didier Super"])
    assert pages == 1
    assert status == "ok; chunks 1, model 1, cached 0, ungrounded 1, not concert 1"
    sent = json.loads(api.calls[0].request.content)
    assert sent["model"] == "test-model"
    assert "<<<PAGE" in sent["messages"][1]["content"]  # page text goes in as data
    assert "ignore me" not in sent["messages"][1]["content"]  # scripts never reach the model


@respx.mock
def test_cache_hit_costs_no_model_call(tz, tmp_path):
    _site()
    # 2 grounded of 3: no check error, so the answer is cached
    answer = {"events": ANSWER["events"][:3]}
    api = respx.post(API).mock(return_value=_completion(answer))
    first = _read(tz, page_llm.Context(_task(), page_llm.ExtractionCache(tmp_path)))
    ctx = page_llm.Context(_task(), page_llm.ExtractionCache(tmp_path))
    second = _read(tz, ctx)
    assert api.call_count == 1
    assert [e.title for e in second[0]] == [e.title for e in first[0]]
    assert second[2].startswith("ok; chunks 1, model 0, cached 1")
    assert ctx.budget.used == 0
    # another model id is another key: asked again
    other = page_llm.Context(_task(), page_llm.ExtractionCache(tmp_path))
    other.task.primary.model = "other-model"
    _read(tz, other)
    assert api.call_count == 2


def test_cache_key_covers_everything_that_shapes_the_answer():
    base = _task()
    key = page_llm.ExtractionCache.key("chunk", base, "La Rayonne")
    assert key == page_llm.ExtractionCache.key("chunk", _task(), "La Rayonne")  # stable
    variants = [
        dataclasses.replace(base, temperature=0.7),
        dataclasses.replace(base, max_output_tokens=512),
        dataclasses.replace(base, primary=dataclasses.replace(base.primary, extra={"top_p": 1})),
        dataclasses.replace(base, primary=dataclasses.replace(base.primary, revision="r2")),
        dataclasses.replace(base, fallback=llm.ModelSpec(provider="mistral", model="m")),
    ]
    keys = {page_llm.ExtractionCache.key("chunk", t, "La Rayonne") for t in variants}
    keys |= {page_llm.ExtractionCache.key("other", base, "La Rayonne")}
    keys |= {page_llm.ExtractionCache.key("chunk", base, "Le Périscope")}
    assert key not in keys and len(keys) == 7
    # the user message template is part of the prompt version
    template = extract.messages_for("{text}", datetime(2000, 1, 1).date(), "{venue}")
    assert "<<<PAGE" in template[1]["content"]


@respx.mock
def test_answers_with_check_errors_are_not_cached(tz, tmp_path):
    _site()
    invented = [
        {"title": f"Invented {i}", "date": "2026-10-20", "time": None, "performers": [],
         "is_concert": True}
        for i in range(3)
    ]  # fmt: skip
    answer = {"events": ANSWER["events"][:1] + invented}  # 3 of 4 ungrounded: a check error
    api = respx.post(API).mock(return_value=_completion(answer))
    for _ in range(2):
        events, _, _ = _read(tz, page_llm.Context(_task(), page_llm.ExtractionCache(tmp_path)))
        assert [e.title for e in events] == ["Didier Super Metal"]  # grounded part still used
    assert api.call_count == 2 and not list(tmp_path.glob("*.json"))


@respx.mock
def test_run_wide_cap(tz):
    pager = {"param": "p", "start": 2, "max": 1}
    reader = {"type": "page_llm", "urls": [URL], "paginate": pager}
    entry = _priority_venue({"name": "Agenda", "venue": "La Rayonne", "reader": reader})
    respx.get(URL, params={"p": "2"}).respond(200, html=PAGE.replace("FAKEAR", "MOLOTOVS"))
    _site()  # after the ?p=2 route: respx takes the first route that matches
    api = respx.post(API).mock(return_value=_completion(ANSWER))
    ctx = page_llm.Context(_task(), budget=page_llm.CallBudget(1))
    events, pages, status = _read(tz, ctx, entry)
    assert api.call_count == 1 and ctx.budget.used == 1
    assert pages == 2 and "llm_cap" in status
    assert len(events) == 2  # the first page's concerts are kept


MONTHS_FR = {10: "octobre", 11: "novembre", 12: "décembre"}
MONTH_NUM = {v: k for k, v in MONTHS_FR.items()}


def _long_page(n: int = 40) -> tuple[str, list[tuple[str, str]]]:
    """An agenda of `n` concerts, one every 1-2 days from 8 Oct 2026 (the last on 5 Dec), in
    La Rayonne's shape (date, time, title, description): ~11,000 characters of page text."""
    items, expected = [], []
    day = datetime(2026, 10, 8)
    for i in range(n):
        when = datetime.fromordinal(day.toordinal() + (i * 3) // 2)
        title = f"Artiste numéro {i:02d}"
        items.append(
            f"<li>sam. {when.day:02d} {MONTHS_FR[when.month]}</li><li>à partir de 20h</li>"
            f"<li>{title}</li><li>Concert, première partie annoncée bientôt, tarif 12 € sur"
            " place et en prévente, ouverture des portes une heure avant le début</li>"
            "<li>Accessible aux personnes à mobilité réduite, bar et restauration sur place,"
            " vestiaire gratuit</li>"
        )
        expected.append((title, when.date().isoformat()))
    expected[-1] = ("Tambours du Bronx", expected[-1][1])
    items[-1] = items[-1].replace(f"Artiste numéro {n - 1:02d}", "Tambours du Bronx")
    return "<html><body><ul>" + "".join(items) + "</ul></body></html>", expected


def _fake_model(request: httpx.Request) -> httpx.Response:
    """Answers like a perfect model, from the chunk only: every title with the date line
    above it. A title whose date line is not in the chunk is left out."""
    events, current = [], None
    for line in _chunk_of(request):
        if line.startswith("sam. "):
            _, dd, mm = line.split(" ")
            current = f"2026-{MONTH_NUM[mm]:02d}-{dd}"
        elif current and (line.startswith("Artiste") or line == "Tambours du Bronx"):
            events.append(
                {"title": line, "date": current, "time": "20:00", "performers": [],
                 "is_concert": True}
            )  # fmt: skip
    return _completion({"events": events})


def test_chunks_cut_on_lines_before_a_date_with_overlap():
    lines = []
    for i in range(40):
        lines += [f"sam. {10 + i % 18:02d} octobre", "à partir de 20h", f"Titre {i}", "x" * 50]
    text = "\n".join(lines)
    chunks, left = page_llm.chunk_text(text, 1000, 10)
    assert not left and len(chunks) > 1
    assert all(len(c) <= 1000 for c in chunks)
    n = page_llm.OVERLAP_LINES
    assert n >= extract.BEFORE  # check_events looks that far above a title
    for prev, nxt in zip(chunks, chunks[1:], strict=False):
        assert prev.split("\n")[-n:] == nxt.split("\n")[:n]
        assert page_llm.is_date_line(nxt.split("\n")[n])  # the new part starts at a date
    assert chunks[-1].endswith("x" * 50)  # nothing lost at the end
    capped, left = page_llm.chunk_text(text, 1000, 2)
    assert capped == chunks[:2] and left
    assert page_llm.chunk_text("", 1000, 4) == ([], False)
    assert page_llm.is_date_line("jeu. 08 octobre") and page_llm.is_date_line("Mercredi 07 oct")
    assert page_llm.is_date_line("Thu. Nov 12, 2026 at 20:00")
    assert not page_llm.is_date_line("à partir de 19h") and not page_llm.is_date_line("FAKEAR")


def test_chunk_size_is_the_evaluated_input_size():
    path = Path(__file__).parents[1] / "eval/cases.jsonl"
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    largest = max(len(c["input"]["text"]) for c in cases)
    assert page_llm.DEFAULT_CHUNK_CHARS <= largest  # 3,200 <= 3,234 (measured)
    task = _task()
    assert page_llm.Context(task).limit == 3200
    assert page_llm.Context(task, chunk_chars=99_999).limit == extract.MAX_CHARS  # upper bound
    zone = load_zone(Path(__file__).parents[1] / "config/zone.yaml")
    assert (zone.llm_chunk_chars, zone.llm_chunks_per_page, zone.llm_calls_per_run) == (
        3200, 6, 40
    )  # fmt: skip


@respx.mock
def test_long_page_is_read_in_chunks_and_its_last_events_found(tz):
    html, expected = _long_page()
    text = extract.page_text(html, 10**6)
    assert len(text) > 10_000  # La Rayonne measured ~10,700
    chunks, left = page_llm.chunk_text(text, 3200, page_llm.DEFAULT_CHUNKS_PER_PAGE)
    n_chunks = len(chunks)
    assert 4 <= n_chunks < page_llm.DEFAULT_CHUNKS_PER_PAGE and not left  # headroom left
    _site(page=html)
    api = respx.post(API).mock(side_effect=_fake_model)
    ctx = page_llm.Context(_task())
    events, pages, status = _read(tz, ctx, days=120)
    assert api.call_count == ctx.budget.used == n_chunks  # each chunk counts toward the cap
    assert all(len("\n".join(_chunk_of(c.request))) <= 3200 for c in api.calls)
    found = [(e.title, e.start.date().isoformat()) for e in events]
    assert found == expected  # every concert once (overlap merged), the last chunk included
    assert ("Tambours du Bronx", "2026-12-05") in found
    assert status.startswith(f"ok; chunks {n_chunks}, model {n_chunks}")


TITLE_FIRST_DESC = ["Danse et musique live", "Création 2026", "Tout public, 1 h 10"]


def _title_first_page(n: int = 30) -> tuple[str, list[str]]:
    """Title first, then 3 description lines, then the date (Mapado-like order)."""
    items, titles = [], []
    for i in range(n):
        when = datetime.fromordinal(datetime(2026, 10, 9).toordinal() + i)
        title = f"Groupe invité {i:02d}"
        desc = "".join(f"<li>{d}</li>" for d in TITLE_FIRST_DESC)
        items.append(
            f"<li>{title}</li>{desc}<li>Ven. {when.day:02d} {MONTHS_FR[when.month]} 2026</li>"
        )
        titles.append(title)
    return "<html><body><ul>" + "".join(items) + "</ul></body></html>", titles


def _title_first_model(request: httpx.Request) -> httpx.Response:
    lines = _chunk_of(request)
    events = []
    for i, line in enumerate(lines):
        if not line.startswith("Groupe invité"):
            continue
        date_line = next((x for x in lines[i + 1 : i + 5] if x.startswith("Ven. ")), None)
        if date_line:  # the date is not in this chunk: a careful model leaves it out
            _, dd, mm, yyyy = date_line.split(" ")
            events.append(
                {"title": line, "date": f"{yyyy}-{MONTH_NUM[mm]:02d}-{dd}", "time": None,
                 "performers": [], "is_concert": True}
            )  # fmt: skip
    return _completion({"events": events})


@respx.mock
def test_title_first_events_cut_by_a_chunk_are_found_in_the_next(tz, monkeypatch):
    html, titles = _title_first_page()
    text = extract.page_text(html, 10**6)
    assert len(page_llm.chunk_text(text, 1000, 10)[0]) > 2
    _site(page=html)
    respx.post(API).mock(side_effect=_title_first_model)
    ctx = page_llm.Context(_task(), chunks_per_page=10, chunk_chars=1000)
    events, _, _ = _read(tz, ctx)
    assert [e.title for e in events] == titles  # each cut falls between a title and its date
    # with the former 3-line overlap the title is not repeated next to its date: lost
    short = functools.partial(page_llm.chunk_text, overlap=3)
    monkeypatch.setattr(page_llm, "chunk_text", short)
    events, _, _ = _read(tz, page_llm.Context(_task(), chunks_per_page=10, chunk_chars=1000))
    assert len(events) < len(titles)


@respx.mock
def test_chunk_cap_per_page_and_cache_per_chunk(tz, tmp_path):
    html, expected = _long_page()
    _site(page=html)
    api = respx.post(API).mock(side_effect=_fake_model)
    cache = page_llm.ExtractionCache(tmp_path)
    ctx = page_llm.Context(_task(), cache, chunks_per_page=1)
    events, _, status = _read(tz, ctx, days=120)
    assert api.call_count == 1 and f"chunk_cap: {URL}" in status
    assert 0 < len(events) < len(expected)
    full = page_llm.Context(_task(), cache)  # same cache, all chunks
    events, _, status = _read(tz, full, days=120)
    assert "cached 1" in status  # the chunk already answered is a cache hit
    assert api.call_count == 1 + full.budget.used and full.budget.used >= 3


@respx.mock
def test_no_key_skips_without_fetching(tz, monkeypatch):
    monkeypatch.delenv("SCW_GENAI_SECRET_KEY")
    page = _site()
    api = respx.post(API)
    assert _read(tz, page_llm.Context(_task())) == ([], 0, "skipped: no key")
    assert not page.called and not api.called
    assert _read(tz, page_llm.Context(None)) == ([], 0, "skipped: no model config")


@respx.mock
def test_model_error_is_a_status_and_the_run_goes_on(tz):
    _site()
    respx.post(API).respond(401)
    other = {"name": "Other", "venue": "Other", "reader": {"type": "unknown"}}
    fetcher = Fetcher(cache_dir=None, min_interval=0)
    now = datetime(*NOW, tzinfo=tz)
    ctx = page_llm.Context(_task())
    events, rows = priority.collect((ENTRY, other), fetcher, now, tz, 60, ctx)
    assert events == []
    assert rows[0]["reader"] == "page_llm"
    assert rows[0]["status"].startswith("error: ModelError")
    assert rows[1]["status"] == "unsupported reader"


def test_config_entries_parse_and_petit_bulletin_is_excluded(tmp_path):
    zone = load_zone(Path(__file__).parents[1] / "config/zone.yaml")
    llm_entries = [e for e in zone.priority_venues if e["reader"]["type"] == "page_llm"]
    assert [e["name"] for e in llm_entries] == [
        "La Rayonne", "Le Périscope", "Chapelle de la Trinité", "Les Subsistances"
    ]  # fmt: skip
    assert all(u.startswith("https://") for e in llm_entries for u in e["reader"]["urls"])
    assert not any("petit-bulletin" in str(e) for e in zone.priority_venues)
    trinite = next(e for e in llm_entries if e["name"] == "Chapelle de la Trinité")
    assert trinite["category"] == "music_venue"  # concert titles pass by the venue rule
    added = configured_venues((trinite,), [])
    assert [(v.name, v.category, v.is_music_venue) for v in added] == [
        ("Chapelle de la Trinité", "music_venue", True)
    ]
    cfg = tmp_path / "zone.yaml"
    cfg.write_text(
        "name: T\nlatitude: 45.7\nlongitude: 4.8\nradius_km: 5\nllm_chunks_per_page: 0\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="llm_chunks_per_page"):
        load_zone(cfg)


@respx.mock
def test_events_attach_to_the_configured_venue_and_carry_the_ai_flag(tz):
    _site()
    respx.post(API).mock(return_value=_completion(ANSWER))
    events, _, _ = _read(tz, page_llm.Context(_task()))
    venues = [Venue("osm:node/11268760933", "La Rayonne", 45.77, 4.89, "concert_hall")]
    assert configured_venues((ENTRY,), venues) == []  # a known venue: nothing to add
    ids, _ = configured_venue_ids((ENTRY,), venues)
    attach_to_configured(events, ids)
    now = datetime(*NOW, tzinfo=tz)
    concerts = build_concerts(events, {v.id: v for v in venues}, now=now, window_days=60, tz=tz)
    assert [(c.title, c.venue_id) for c in concerts] == [
        ("Didier Super Metal", "osm:node/11268760933"),
        ("FAKEAR", "osm:node/11268760933"),
    ]
    out = concerts[0].to_dict()
    assert out["ai_extracted"] is True and out["reason"] == "model: concert, music venue"
    assert out["sources"] == ["page_llm:larayonne.org"]


SUBS_URL = "https://www.les-subs.com/agenda/"
SUBS_PAGE = """<html><body><ul>
<li>Malacca</li><li>Compagnie Voltaïk</li><li>mer. 14 octobre</li><li>18:30</li>
<li>DJ Fantastik</li><li>DJ set</li><li>sam. 14 novembre</li><li>22:00</li>
</ul></body></html>"""
SUBS = _priority_venue(
    {"name": "Les Subsistances", "venue": "Les Subsistances", "latitude": 45.768234,
     "longitude": 4.816856, "reader": {"type": "page_llm", "urls": [SUBS_URL]}}
)  # fmt: skip


@respx.mock
def test_dance_show_at_a_non_music_venue_needs_music_words(tz):
    _site(SUBS_PAGE, "www.les-subs.com", SUBS_URL)
    answer = {"events": [  # the model wrongly calls the dance show a concert
        {"title": "Malacca", "date": "2026-10-14", "time": "18:30", "performers": [],
         "is_concert": True},
        {"title": "DJ Fantastik", "date": "2026-11-14", "time": "22:00",
         "performers": ["DJ Fantastik"], "is_concert": True},
    ]}  # fmt: skip
    respx.post(API).mock(return_value=_completion(answer))
    events, _, _ = _read(tz, page_llm.Context(_task()), SUBS)
    assert [e.title for e in events] == ["Malacca", "DJ Fantastik"]  # both grounded
    venues = configured_venues((SUBS,), [])  # no known venue: config:, events_venue
    assert [(v.id, v.category) for v in venues] == [("config:subsistances", "events_venue")]
    ids, _ = configured_venue_ids((SUBS,), venues)
    attach_to_configured(events, ids)
    now = datetime(*NOW, tzinfo=tz)
    concerts = build_concerts(events, {v.id: v for v in venues}, now=now, window_days=60, tz=tz)
    assert [(c.title, c.reason) for c in concerts] == [
        ("DJ Fantastik", "model: concert, music keywords")
    ]
