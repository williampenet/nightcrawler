from datetime import date

from nightcrawler.structured import ical_events, jsonld_events, microdata_events, parse_start


def test_jsonld_graph(fixture_text, tz):
    events = jsonld_events(fixture_text("agenda.html"), "v1", tz)
    assert [e.title for e in events] == ["Drone Night", "Atelier sérigraphie"]
    drone = events[0]
    assert drone.types == ["MusicEvent"]
    assert drone.performers == ["Sunn & Co"]
    assert drone.ticket_url == "https://tickets.example/drone"
    assert drone.start.isoformat() == "2026-10-10T20:30:00+02:00"
    assert events[1].start.tzinfo is not None  # naive date read in zone time


def test_microdata(fixture_text, tz):
    events = microdata_events(fixture_text("microdata.html"), "v2", tz)
    assert len(events) == 1
    assert events[0].title == "Impro libre #4"
    assert events[0].url == "https://ombres.example/impro"


def test_ical(fixture_text, tz):
    events = ical_events(fixture_text("agenda.ics"), "v2", tz)
    assert [e.title for e in events] == ["Concert de jazz"]
    assert events[0].start.hour == 20


def test_parse_start_variants(tz):
    assert parse_start(date(2026, 10, 1), tz).hour == 0
    assert parse_start("not a date at all !!", tz) is None
    assert parse_start(None, tz) is None
    # partial dates must not be completed with today's date
    assert parse_start("20h30", tz) is None
    assert parse_start("12/10", tz) is None
    assert parse_start("12/10/2026 21:00", tz).month == 10


def test_microdata_ignores_nested_items(tz):
    html = """
    <div itemscope itemtype="https://schema.org/MusicEvent">
      <div itemprop="location" itemscope itemtype="https://schema.org/Place">
        <span itemprop="name">La Salle</span>
      </div>
      <span itemprop="name">Le vrai titre</span>
      <meta itemprop="startDate" content="2026-10-12T21:00">
    </div>"""
    assert [e.title for e in microdata_events(html, "v", tz)] == ["Le vrai titre"]


def test_unsafe_urls_dropped(tz):
    html = (
        '<script type="application/ld+json">{"@type":"Event","name":"X",'
        '"startDate":"2026-10-10","url":"javascript:alert(1)"}</script>'
    )
    assert jsonld_events(html, "v", tz)[0].url is None


def test_page_formats_reports_type_names_only():
    """WIP-89: why a platform page gave no event, without any page content."""
    from nightcrawler.structured import page_formats

    html = (
        '<script type="application/ld+json">{"@type": "Organization", "name": "Secret Club",'
        ' "event": {"@type": "MusicEvent"}}</script>'
        '<script type="application/ld+json">{broken</script>'
        '<script id="__NEXT_DATA__" type="application/json">{"props": {}}</script>'
        "<script>self.__next_f.push([1, 'x'])</script>"
        '<div itemscope itemtype="https://schema.org/Place"></div>'
    )
    flags = ["jsonld", "jsonld_invalid", "microdata", "next_data", "next_flight"]
    assert page_formats(html) == flags + ["jsonld:MusicEvent", "jsonld:Organization"]
    assert page_formats("<p>rien</p>") == []
    # @type is free text: only event types and a fixed list are named (the report is public)
    free = ('<script type="application/ld+json">{"@type": ["Lyon", "SecretGuestList", '
            '"MusicEvent\\n", "A b", "WebPage", "Festival", '
            '"JeanDupontEvent"]}</script>')  # fmt: skip
    assert page_formats(free) == ["jsonld", "jsonld:Festival", "jsonld:WebPage", "jsonld:other"]
