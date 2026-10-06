import json
from datetime import datetime
from pathlib import Path

from nightcrawler import coverage
from nightcrawler.cli import one_line
from nightcrawler.models import Concert
from nightcrawler.pipeline import summary_markdown

ROOT = Path(__file__).parent.parent
REFERENCE = ROOT / "eval" / "reference" / "watch_events.csv"
ALIASES = ROOT / "eval" / "reference" / "venue_aliases.yaml"
FIXTURE = Path(__file__).parent / "fixtures" / "coverage" / "concerts_2026-10-06.json"


def concert(id="c1", title="", start="2026-10-08T20:00:00+02:00", venue_name="", performers=()):
    return Concert(id, title, start, "v", venue_name, None, None, list(performers), [], "")


def fixture_concerts():
    return [concert(**row) for row in json.loads(FIXTURE.read_text(encoding="utf-8"))]


def test_reference_file_is_the_watch_rows():
    refs = coverage.load_reference(REFERENCE)
    assert len(refs) == 69  # rows of the shared concerts-vus.md (grep -c '^20')
    assert refs[0] == {
        "date": "2026-07-26",
        "artists": "Shutdown + Ivy + Nø.V.D",
        "venue": "Rock n Eat, Lyon 9e",
    }
    assert all(datetime.strptime(r["date"], "%Y-%m-%d") for r in refs)
    assert all(r["artists"] and r["venue"] for r in refs)


def test_hand_measurement_method(tz):
    """PRD v3, 2026-10-06: 40 watch events in the window (<= 2026-12-05), 11 present.
    The live data of that day cannot be fetched here: the fixture is synthetic, built to the
    same totals and to the per-venue figures the PRD gives (Épicerie 4, Auditorium 2,
    Opéra Underground / Subsistances / Trinité 0); the other venues' split is illustrative."""
    now = datetime(2026, 10, 6, 20, 42, tzinfo=tz)
    refs = coverage.load_reference(REFERENCE)
    got = coverage.measure(refs, fixture_concerts(), now, 60, tz, coverage.load_aliases(ALIASES))
    assert (got["in_window"], got["found"], got["rate"]) == (40, 11, 0.275)
    pv = got["per_venue"]
    assert pv["L'Épicerie Moderne"] == [9, 4]
    assert pv["Auditorium de Lyon"] == [3, 2]
    assert pv["Opéra Underground"] == [10, 0]
    assert "Les Subsistances" not in pv and "Grrrnd Zero" not in pv  # none in this window
    assert pv["Chapelle de la Trinité"] == [3, 0]
    assert pv["La Rayonne"] == [3, 1]  # "CCO Villeurbanne / La Rayonne"
    found = {e["concert_id"] for e in got["events"]} - {None}
    assert found == {f"hit{n:02d}" for n in range(1, 12)}
    assert all(e["date"] <= "2026-12-05" for e in got["events"])


def test_venue_only_match_does_not_count(tz):
    refs = [{"date": "2026-10-20", "artists": "Franges", "venue": "Le Périscope"}]
    now = datetime(2026, 10, 6, tzinfo=tz)
    decoys = [
        concert(title="Autre groupe", start="2026-10-20T20:30:00+02:00", venue_name="Périscope"),
        concert(title="Frangesque", start="2026-10-20T20:30:00+02:00", venue_name="Périscope"),
    ]
    assert coverage.measure(refs, decoys, now, 60, tz)["found"] == 0
    hit = concert(title="x", performers=["FRANGES"], start="2026-10-20T20:30:00+02:00")
    hit.venue_name = "Le Périscope"
    assert coverage.measure(refs, [hit], now, 60, tz)["found"] == 1


def test_local_date_and_window(tz):
    refs = [
        {"date": "2026-11-10", "artists": "Gurriers", "venue": "L'Épicerie Moderne"},
        {"date": "2026-10-05", "artists": "Gurriers", "venue": "L'Épicerie Moderne"},
    ]
    now = datetime(2026, 10, 6, 9, tzinfo=tz)
    utc_late = concert(
        title="Gurriers", start="2026-11-10T23:30:00+00:00", venue_name="Épicerie Moderne"
    )
    got = coverage.measure(refs, [utc_late], now, 60, tz)
    assert (got["in_window"], got["found"]) == (1, 0)  # 00:30 on the 11th in Lyon
    utc_late.start = "2026-11-09T23:30:00+00:00"  # 00:30 on the 10th in Lyon
    assert coverage.measure(refs, [utc_late], now, 60, tz)["found"] == 1


def test_artist_split_and_aliases(tz):
    assert coverage.artist_pieces("Callahan & Witscher + Pif & The Gee Gees") == [
        "callahan",
        "witscher",
        "pif",
        "the gee gees",
    ]
    assert coverage.artist_pieces("PΞB + We Use Cookies") == ["we use cookies"]  # "pb" too short
    refs = [
        {"date": "2026-07-31", "artists": "Aho Ssan + DJ Vanille", "venue": "Le Sucre, Lyon 2e"}
    ]
    now = datetime(2026, 7, 25, tzinfo=tz)
    c = concert(title="DJ Vanille b2b", start="2026-07-31T23:00:00+02:00", venue_name="Le Sucre")
    assert coverage.measure(refs, [c], now, 60, tz)["found"] == 0  # district not in the name
    aliases = coverage.load_aliases(ALIASES)
    assert coverage.measure(refs, [c], now, 60, tz, aliases)["found"] == 1


def test_report_lines_keep_names_out_of_the_annotation(tz):
    now = datetime(2026, 10, 6, 20, 42, tzinfo=tz)
    cov = coverage.measure(coverage.load_reference(REFERENCE), fixture_concerts(), now, 60, tz)
    report = {
        "zone": {"name": "Z", "window_days": 60},
        "venues": 1,
        "venues_with_website": 1,
        "probe_status": {},
        "probe_method": {},
        "raw_events": 0,
        "concerts": 0,
        "venues_with_concerts": 0,
        "dedup": {"merged": 0, "conflicts": 0},
        "artists": {"identified": 0, "candidates": 0, "concerts_with_artist": 0},
        "coverage": cov,
    }
    line = one_line(report)
    assert line.endswith(f"reference coverage: {cov['found']}/40 rate={cov['rate']}")
    assert "Épicerie" not in line and "Lemon" not in line
    md = summary_markdown(report)
    assert f"| Reference events found (FR-11) | {cov['found']} / 40 (" in md
    assert "| … at Opéra Underground | 0 / 10 |" in md
    assert one_line(report | {"coverage": None}).endswith("reference coverage: -")
