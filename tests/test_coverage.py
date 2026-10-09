import json
from datetime import datetime
from pathlib import Path

from nightcrawler import coverage
from nightcrawler.cli import one_line
from nightcrawler.models import Concert
from nightcrawler.pipeline import summary_markdown

ROOT = Path(__file__).parent.parent
REFERENCE = ROOT / "eval" / "reference" / "watch_events.csv"
MATCHING = ROOT / "eval" / "reference" / "matching.yaml"
FIXTURE = Path(__file__).parent / "fixtures" / "coverage" / "concerts_2026-10-06.json"


def concert(id="c1", title="", start="2026-10-08T20:00:00+02:00", venue_name="", performers=()):
    return Concert(id, title, start, "v", venue_name, None, None, list(performers), [], "")


def fixture_concerts():
    return [concert(**row) for row in json.loads(FIXTURE.read_text(encoding="utf-8"))]


def test_reference_file_is_the_watch_rows():
    refs = coverage.load_reference(REFERENCE)
    # 69 rows of the shared concerts-vus.md (grep -c '^20'), minus the private-place row
    assert len(refs) == 68
    assert not any("lieu priv" in r["venue"] for r in refs)
    assert refs[0] == {
        "date": "2026-07-26",
        "artists": "Shutdown + Ivy + Nø.V.D",
        "venue": "Rock n Eat, Lyon 9e",
    }
    assert all(datetime.strptime(r["date"], "%Y-%m-%d") for r in refs)
    assert all(r["artists"] and r["venue"] for r in refs)


def test_method_on_a_synthetic_fixture(tz):
    """The coverage method on a synthetic run of 2026-10-06 20:42 (the live data of that
    day cannot be fetched from the agent workspace). The fixture is built so that 11 rows
    are found, like the PRD's hand count; whether the CI measure finds 11 on live data is
    unverified until the first Pipeline run with it. Which rows are hits is illustrative:
    the PRD's "Auditorium (2 concerts)" is the run's concert count there, not 2 hits."""
    now = datetime(2026, 10, 6, 20, 42, tzinfo=tz)
    refs = coverage.load_reference(REFERENCE)
    got = coverage.measure(refs, fixture_concerts(), now, 60, tz, coverage.load_matching(MATCHING))
    # 39, not the hand count's 40: the 2026-12-05 row is on the partly covered last day
    assert (got["in_window"], got["found"], got["rate"]) == (39, 11, 0.282)
    assert got["date_venue_only"] == 1  # 2026-10-20 Périscope: other acts only
    pv = got["per_venue"]
    assert pv["L'Épicerie Moderne"] == [9, 4]
    assert pv["Opéra Underground"] == [10, 0]
    assert pv["La Rayonne"] == [2, 1]  # "CCO Villeurbanne / La Rayonne"
    assert {e["concert_id"] for e in got["events"]} - {None} == {
        f"hit{n:02d}" for n in range(1, 12)
    }
    assert all(set(e) == {"row", "found", "concert_id"} for e in got["events"])
    assert all(refs[e["row"]]["date"] < "2026-12-05" for e in got["events"])


def test_window_matches_the_run_and_drops_the_partial_last_day(tz):
    refs = [
        {"date": d, "artists": "A band", "venue": "V"}
        for d in ("2026-10-05", "2026-10-06", "2026-12-04", "2026-12-05")
    ]
    for hour, minute in ((6, 17), (20, 42)):  # the daily run, and the PRD's hand count
        now = datetime(2026, 10, 6, hour, minute, tzinfo=tz)
        rows = [e["row"] for e in coverage.measure(refs, [], now, 60, tz)["events"]]
        assert rows == [1, 2]  # today counts; 2026-12-05 (now + 60 days) is left out


def test_venue_only_match_does_not_count(tz):
    refs = [{"date": "2026-10-20", "artists": "Franges", "venue": "Le Périscope"}]
    now = datetime(2026, 10, 6, tzinfo=tz)
    decoys = [
        concert(title="Autre groupe", start="2026-10-20T20:30:00+02:00", venue_name="Périscope"),
        concert(title="Frangesque", start="2026-10-20T20:30:00+02:00", venue_name="Périscope"),
    ]
    got = coverage.measure(refs, decoys, now, 60, tz)
    assert (got["found"], got["date_venue_only"]) == (0, 1)
    hit = concert(title="x", performers=["FRANGES"], start="2026-10-20T20:30:00+02:00")
    hit.venue_name = "Le Périscope"
    got = coverage.measure(refs, [hit], now, 60, tz)
    assert (got["found"], got["date_venue_only"]) == (1, 0)


def test_local_date(tz):
    refs = [{"date": "2026-11-10", "artists": "Gurriers", "venue": "L'Épicerie Moderne"}]
    now = datetime(2026, 10, 6, 9, tzinfo=tz)
    late = concert(title="Gurriers", start="2026-11-10T23:30:00+00:00", venue_name="Épicerie")
    late.venue_name = "Épicerie Moderne"
    assert coverage.measure(refs, [late], now, 60, tz)["found"] == 0  # 00:30 on the 11th
    late.start = "2026-11-09T23:30:00+00:00"  # 00:30 on the 10th in Lyon
    assert coverage.measure(refs, [late], now, 60, tz)["found"] == 1


def test_bare_names_match(tz):
    m = coverage.load_matching(MATCHING)
    refs = [{"date": "2026-10-08", "artists": "The Lemon Twigs", "venue": "L'Épicerie Moderne"}]
    now = datetime(2026, 10, 6, tzinfo=tz)
    c = concert(title="Lemon Twigs", venue_name="L'Épicerie Moderne")
    assert coverage.measure(refs, [c], now, 60, tz)["found"] == 0  # without matching.yaml
    assert coverage.measure(refs, [c], now, 60, tz, m)["found"] == 1
    assert coverage.artist_pieces("Louis Sclavis Shunkan Trio", m) == ["louis sclavis shunkan"]
    assert coverage.artist_pieces("The Buttshakers XXL", m) == ["buttshakers"]
    assert coverage.artist_pieces("Compagnie Kotekan Tiga", m) == ["kotekan tiga"]
    assert coverage.artist_pieces("Quatuor Béla", m) == ["bela"]
    assert coverage.artist_pieces("Trio + The Trio", m) == ["trio"]  # nothing else remains


def test_artist_split_and_aliases(tz):
    m = coverage.load_matching(MATCHING)
    assert coverage.artist_pieces("Callahan & Witscher + Pif & The Gee Gees", m) == [
        "callahan",
        "witscher",
        "pif",
        "gee gees",
    ]
    assert coverage.artist_pieces("PΞB + We Use Cookies") == ["we use cookies"]  # "pb" too short
    refs = [
        {"date": "2026-07-31", "artists": "Aho Ssan + DJ Vanille", "venue": "Le Sucre, Lyon 2e"}
    ]
    now = datetime(2026, 7, 25, tzinfo=tz)
    c = concert(title="DJ Vanille b2b", start="2026-07-31T23:00:00+02:00", venue_name="Le Sucre")
    assert coverage.measure(refs, [c], now, 60, tz)["found"] == 0  # district not in the name
    assert coverage.measure(refs, [c], now, 60, tz, m)["found"] == 1


def test_report_lines_are_totals_only(tz):
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
    assert line.endswith(
        f"reference coverage: {cov['found']}/39 rate={cov['rate']} "
        f"date_venue_only={cov['date_venue_only']} | judge: -"
    )
    md = summary_markdown(report)
    assert f"| Reference events found (FR-11) | {cov['found']} / 39 (" in md
    for text in (line, md):
        assert "Épicerie" not in text and "Opéra" not in text and "Lemon" not in text
    assert one_line(report | {"coverage": None}).endswith("reference coverage: - | judge: -")
    error = report | {"coverage": {"status": "error: KeyError"}}
    assert one_line(error).endswith("reference coverage: error: KeyError | judge: -")
