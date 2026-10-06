import json

import respx

from nightcrawler.artists import (
    DEEZER_SEARCH,
    MB_SEARCH,
    enrich,
    musicbrainz_tags,
    norm,
    performers_of,
)
from nightcrawler.http import Fetcher
from nightcrawler.models import Concert


def concert(title, performers=()):
    return Concert(
        "id",
        title,
        "2026-10-10T20:00:00+02:00",
        "v",
        "Venue",
        None,
        None,
        list(performers),
        ["json-ld"],
        "music venue",
    )


def fetcher():
    return Fetcher(cache_dir=None, min_interval=0)


def test_norm():
    assert norm("Sunn O)))") == "sunno" and norm("Björk") == "bjork"


def test_performers_from_source_first():
    assert performers_of(concert("Whatever", ["Earth", "EARTH", "X"])) == (["Earth"], None)


def test_performers_split_from_title():
    names, whole = performers_of(concert("Concert : Earth + Sunn O))) / Boris"))
    assert names == ["Earth", "Sunn O)))", "Boris"] and whole == "Earth + Sunn O))) / Boris"
    names, _ = performers_of(concert("Drone Night: Earth, Boris (complet)"))
    assert names == ["Drone Night", "Earth", "Boris"]
    assert performers_of(concert("Simon & Garfunkel")) == (["Simon & Garfunkel"], None)
    names, whole = performers_of(concert("Earth: live in Lyon"))
    assert "Earth" in names and whole == "Earth: live in Lyon"


@respx.mock
def test_enrich_exact_match_only():
    respx.get(DEEZER_SEARCH, params={"q": "Earth"}).respond(
        json={
            "data": [
                {"id": 1, "name": "Earth Wind", "nb_fan": 9},
                {"id": 42, "name": "EARTH", "nb_fan": 1234},
            ]
        }
    )
    respx.get(DEEZER_SEARCH, params={"q": "Earth + Nobody Knows"}).respond(json={"data": []})
    respx.get(DEEZER_SEARCH, params={"q": "Nobody Knows"}).respond(
        json={"data": [{"id": 7, "name": "Nobody", "nb_fan": 5}]}
    )
    respx.get("https://api.deezer.com/artist/42/related").respond(
        json={"data": [{"name": "Sunn O)))"}, {"name": "Boris"}]}
    )
    respx.get(MB_SEARCH).respond(
        json={
            "artists": [
                {
                    "name": "Earth",
                    "score": 100,
                    "tags": [{"name": "Drone", "count": 5}, {"name": "doom metal", "count": 9}],
                }
            ]
        }
    )
    concerts = [concert("Earth + Nobody Knows")]
    artists, stats = enrich(concerts, fetcher())
    assert list(artists) == ["earth"]
    earth = artists["earth"]
    assert earth.deezer_id == 42 and earth.fans == 1234
    assert earth.related == ["Sunn O)))", "Boris"]
    assert earth.tags == ["doom metal", "drone"]
    assert concerts[0].artists == ["earth"]
    assert stats == {
        "candidates": 3,
        "identified": 1,
        "with_tags": 1,
        "confident": 1,
        "doubt_ambiguous": 0,
        "doubt_unverified": 0,
        "doubt_low_fans": 0,
        "doubt_short_name": 0,
        "concerts_with_artist": 1,
        "capped": False,
    }


@respx.mock
def test_musicbrainz_needs_exact_name_and_score():
    respx.get(MB_SEARCH).respond(
        json={
            "artists": [
                {"name": "Earthless", "score": 100, "tags": [{"name": "rock", "count": 1}]},
                {"name": "Earth", "score": 90, "tags": [{"name": "drone", "count": 1}]},
            ]
        }
    )
    assert musicbrainz_tags(fetcher(), "Earth") is None


@respx.mock
def test_homonyms_and_thin_profiles_get_no_related_or_tags():
    related = respx.get(url__regex=r"https://api\.deezer\.com/artist/\d+/related").respond(
        json={"data": [{"name": "Acid Arab"}]}
    )
    respx.get(DEEZER_SEARCH, params={"q": "Sheldon"}).respond(
        json={
            "data": [
                {"id": 1, "name": "Sheldon", "nb_fan": 5000},
                {"id": 2, "name": "SHELDON", "nb_fan": 40},
            ]
        }
    )
    respx.get(DEEZER_SEARCH, params={"q": "Lupio"}).respond(
        json={"data": [{"id": 3, "name": "Lupio", "nb_fan": 120}]}
    )
    respx.get(DEEZER_SEARCH, params={"q": "Asna"}).respond(
        json={"data": [{"id": 4, "name": "Asna", "nb_fan": 9000}]}
    )
    respx.get(MB_SEARCH).respond(
        json={"artists": [{"name": "Asna", "score": 100}, {"name": "Asna", "score": 100}]}
    )
    artists, stats = enrich([concert("t", ["Sheldon", "Lupio", "Asna"])], fetcher())
    assert {k: a.doubt for k, a in artists.items()} == {
        "sheldon": "ambiguous",
        "lupio": "low_fans",
        "asna": "unverified",
    }
    assert all(not a.related and not a.tags and not a.confident for a in artists.values())
    assert artists["sheldon"].fans == 5000  # best-known homonym kept for exact seed matches
    assert related.call_count == 0
    assert (stats["confident"], stats["doubt_ambiguous"], stats["doubt_unverified"]) == (0, 1, 1)


@respx.mock
def test_api_errors_are_misses():
    respx.get(DEEZER_SEARCH).respond(500)
    artists, stats = enrich([concert("Earth")], fetcher())
    assert artists == {} and stats["identified"] == 0


def test_concert_json_has_artists():
    assert json.loads(json.dumps(concert("x").to_dict()))["artists"] == []


@respx.mock
def test_whole_title_wins_over_split():
    respx.get(DEEZER_SEARCH, params={"q": "Earth, Wind & Fire"}).respond(
        json={"data": [{"id": 5, "name": "Earth, Wind & Fire", "nb_fan": 99}]}
    )
    respx.get("https://api.deezer.com/artist/5/related").respond(json={"data": []})
    respx.get(MB_SEARCH).respond(json={"artists": []})
    concerts = [concert("Earth, Wind & Fire")]
    artists, _ = enrich(concerts, fetcher())
    assert concerts[0].artists == ["earthwindfire"] and list(artists) == ["earthwindfire"]


@respx.mock
def test_malformed_payloads_never_crash():
    respx.get(DEEZER_SEARCH, params={"q": "A1"}).respond(json={"data": None})
    respx.get(DEEZER_SEARCH, params={"q": "B2"}).respond(json={"data": ["x", {"name": None}]})
    respx.get(DEEZER_SEARCH, params={"q": "C3"}).respond(
        json={"data": [{"id": 3, "name": "C3", "nb_fan": "n/a"}]}
    )
    respx.get("https://api.deezer.com/artist/3/related").respond(json={"data": None})
    respx.get(MB_SEARCH).respond(
        json={"artists": [{"name": "C3", "score": 100, "tags": [{"name": "x", "count": None}]}]}
    )
    respx.get(DEEZER_SEARCH, params={"q": "D4"}).respond(json={"error": {"code": 4}})
    concerts = [concert("t", ["A1", "B2", "C3", "D4"])]
    artists, stats = enrich(concerts, fetcher())
    c3 = artists["c3"]
    assert list(artists) == ["c3"] and c3.fans == 0 and c3.tags == [] and c3.doubt == "short_name"
    assert stats["identified"] == 1


@respx.mock
def test_quota_error_not_cached(tmp_path):
    route = respx.get(DEEZER_SEARCH).respond(json={"error": {"code": 4, "message": "Quota"}})
    f = Fetcher(cache_dir=tmp_path, min_interval=0)
    enrich([concert("t", ["Earth"])], f)
    enrich([concert("t", ["Earth"])], f)
    assert route.call_count == 2


def test_lookup_cap():
    artists, stats = enrich([concert("t", ["A1", "B2"])], fetcher(), max_lookups=0)
    assert stats["capped"] is True and artists == {}


@respx.mock
def test_unverified_short_and_famous_names():
    respx.get(url__regex=r"https://api\.deezer\.com/artist/\d+/related").respond(
        json={"data": [{"name": "Phoenix"}]}
    )
    respx.get(DEEZER_SEARCH, params={"q": "Asna"}).respond(
        json={"data": [{"id": 4, "name": "Asna", "nb_fan": 9000}]}
    )
    respx.get(DEEZER_SEARCH, params={"q": "Air"}).respond(
        json={"data": [{"id": 5, "name": "Air", "nb_fan": 900000}]}
    )
    respx.get(DEEZER_SEARCH, params={"q": "Nes"}).respond(
        json={"data": [{"id": 6, "name": "Nes", "nb_fan": 20000}]}
    )
    respx.get(MB_SEARCH).respond(json={"artists": []})  # nobody on MusicBrainz
    artists, stats = enrich([concert("t", ["Asna", "Air", "Nes"])], fetcher())
    assert artists["asna"].doubt == "unverified" and not artists["asna"].related
    assert artists["nes"].doubt == "short_name"
    air = artists["air"]
    assert air.confident and air.related == ["Phoenix"] and air.tags == []
    assert stats["doubt_unverified"] == 1 and stats["confident"] == 1
