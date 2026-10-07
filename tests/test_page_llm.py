"""page_llm reader (WIP-66): a model reads "Mes salles" pages with no structured data.

Offline: the venue pages and the provider's OpenAI-compatible endpoint are mocked with respx,
so the real path runs (task router, schema validation, grounding checks).
"""

import copy
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


def _read(tz, ctx, entry=ENTRY):
    fetcher = Fetcher(cache_dir=None, min_interval=0)
    now = datetime(2026, 10, 7, 9, tzinfo=tz)
    return page_llm.read(entry["reader"], entry["venue"], fetcher, now, tz, 60, ctx)


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
    assert status == "ok; model 1, cached 0, ungrounded 1, not concert 1"
    sent = json.loads(api.calls[0].request.content)
    assert sent["model"] == "test-model"
    assert "<<<PAGE" in sent["messages"][1]["content"]  # page text goes in as data
    assert "ignore me" not in sent["messages"][1]["content"]  # scripts never reach the model


@respx.mock
def test_cache_hit_costs_no_model_call(tz, tmp_path):
    _site()
    api = respx.post(API).mock(return_value=_completion(ANSWER))
    first = _read(tz, page_llm.Context(_task(), page_llm.ExtractionCache(tmp_path)))
    ctx = page_llm.Context(_task(), page_llm.ExtractionCache(tmp_path))
    second = _read(tz, ctx)
    assert api.call_count == 1
    assert [e.title for e in second[0]] == [e.title for e in first[0]]
    assert second[2].startswith("ok; model 0, cached 1")
    assert ctx.budget.used == 0
    # another model id is another key: asked again
    other = page_llm.Context(_task(), page_llm.ExtractionCache(tmp_path))
    other.task.primary.model = "other-model"
    _read(tz, other)
    assert api.call_count == 2


@respx.mock
def test_run_wide_cap(tz):
    pager = {"param": "p", "start": 2, "max": 1}
    reader = {"type": "page_llm", "urls": [URL], "paginate": pager}
    entry = _priority_venue({"name": "Agenda", "venue": "La Rayonne", "reader": reader})
    respx.get(URL, params={"p": "2"}).respond(200, html=PAGE.replace("FAKEAR", "MOLOTOVS"))
    _site()  # after the ?p=2 route: respx takes the first route that matches
    api = respx.post(API).mock(return_value=_completion(ANSWER))
    ctx = page_llm.Context(_task(), budget=page_llm.PageBudget(1))
    events, pages, status = _read(tz, ctx, entry)
    assert api.call_count == 1 and ctx.budget.used == 1
    assert pages == 2 and "llm_cap" in status
    assert len(events) == 2  # the first page's concerts are kept


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
    now = datetime(2026, 10, 7, 9, tzinfo=tz)
    ctx = page_llm.Context(_task())
    events, rows = priority.collect((ENTRY, other), fetcher, now, tz, 60, ctx)
    assert events == []
    assert rows[0]["reader"] == "page_llm"
    assert rows[0]["status"].startswith("error: ModelError")
    assert rows[1]["status"] == "unsupported reader"


def test_config_entries_parse_and_attach_to_their_venue():
    zone = load_zone(Path(__file__).parents[1] / "config/zone.yaml")
    llm_entries = [e for e in zone.priority_venues if e["reader"]["type"] == "page_llm"]
    assert [e["name"] for e in llm_entries] == [
        "La Rayonne", "Le Périscope", "Chapelle de la Trinité", "Les Subsistances", "Petit Bulletin"
    ]  # fmt: skip
    assert all(u.startswith("https://") for e in llm_entries for u in e["reader"]["urls"])
    assert zone.llm_pages_per_run == 40
    with pytest.raises(ValueError, match="attach"):
        _priority_venue({"name": "x", "reader": {"type": "page_llm", "urls": [URL], "attach": "x"}})


@respx.mock
def test_events_attach_to_the_configured_venue_and_carry_the_ai_flag(tz):
    _site()
    respx.post(API).mock(return_value=_completion(ANSWER))
    events, _, _ = _read(tz, page_llm.Context(_task()))
    venues = [Venue("osm:node/11268760933", "La Rayonne", 45.77, 4.89, "events_venue")]
    assert configured_venues((ENTRY,), venues) == []  # a known venue: nothing to add
    ids, _ = configured_venue_ids((ENTRY,), venues)
    attach_to_configured(events, ids)
    now = datetime(2026, 10, 7, 9, tzinfo=tz)
    concerts = build_concerts(events, {v.id: v for v in venues}, now=now, window_days=60, tz=tz)
    assert [(c.title, c.venue_id) for c in concerts] == [
        ("Didier Super Metal", "osm:node/11268760933"),
        ("FAKEAR", "osm:node/11268760933"),
    ]
    out = concerts[0].to_dict()
    assert out["ai_extracted"] is True and out["reason"] == "model: concert"
    assert out["sources"] == ["page_llm:larayonne.org"]


AGG_URL = "https://www.petit-bulletin.fr/agenda-recherche.html"
AGG_PAGE = """<html><body><ul>
<li>Wu Lyf</li><li>Rock &amp; Pop</li><li>L'Épicerie Moderne</li>
<li>Mardi 6 octobre 2026 à 19h30</li>
<li>Magic Mardi</li><li>Musique Electronique</li><li>Le Sucre</li>
<li>Mardi 6 octobre 2026 de 18h à 1h</li>
</ul></body></html>"""
AGG = _priority_venue(
    {"name": "Petit Bulletin",
     "reader": {"type": "page_llm", "attach": "by_location", "urls": [AGG_URL]}}
)  # fmt: skip


@respx.mock
def test_aggregator_is_skipped_while_the_schema_has_no_location(tz):
    page = _site(AGG_PAGE, "www.petit-bulletin.fr", AGG_URL)
    assert not page_llm.has_location_field()
    events, pages, status = _read(tz, page_llm.Context(_task()), AGG)
    assert (events, pages, status) == ([], 0, "skipped: extraction schema has no location field")
    assert not page.called
    assert configured_venues((AGG,), []) == []  # an aggregator is not a venue


@respx.mock
def test_aggregator_events_go_through_venue_attribution(tz, monkeypatch):
    schema = copy.deepcopy(extract.SCHEMA)  # the proposed follow-up schema, for this test only
    item = schema["properties"]["events"]["items"]
    item["properties"]["location"] = {"type": ["string", "null"], "maxLength": 120}
    item["required"].append("location")
    monkeypatch.setattr(extract, "SCHEMA", schema)
    _site(AGG_PAGE, "www.petit-bulletin.fr", AGG_URL)
    answer = {"events": [
        {"title": "Wu Lyf", "date": "2026-10-06", "time": "19:30", "performers": ["Wu Lyf"],
         "is_concert": True, "location": "L'Épicerie Moderne"},
        {"title": "Magic Mardi", "date": "2026-10-06", "time": "18:00", "performers": [],
         "is_concert": True, "location": "Le Transbordeur"},  # not the place on the page
    ]}  # fmt: skip
    respx.post(API).mock(return_value=_completion(answer))
    fetcher = Fetcher(cache_dir=None, min_interval=0)
    now = datetime(2026, 10, 6, 9, tzinfo=tz)
    events, _, status = page_llm.read(
        AGG["reader"], AGG["venue"], fetcher, now, tz, 60, page_llm.Context(_task())
    )
    assert [(e.title, e.location_name) for e in events] == [("Wu Lyf", "L'Épicerie Moderne")]
    assert status.endswith("no location 1")
    venues = {
        "osm:node/523776298": Venue(
            "osm:node/523776298", "L'épicerie moderne", 45.67, 4.85, "concert_hall"
        )
    }
    concerts = build_concerts(events, venues, now=now, window_days=60, tz=tz)
    assert [(c.title, c.venue_id, c.ai_extracted) for c in concerts] == [
        ("Wu Lyf", "osm:node/523776298", True)
    ]
