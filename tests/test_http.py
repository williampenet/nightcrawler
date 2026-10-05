import time

import httpx
import respx

from nightcrawler.http import Fetcher


@respx.mock
def test_rate_limit_per_host():
    respx.get("https://a.example/robots.txt").respond(404)
    respx.get("https://a.example/1").respond(200, text="one")
    respx.get("https://a.example/2").respond(200, text="two")
    f = Fetcher(cache_dir=None, min_interval=0.3)
    t0 = time.monotonic()
    f.get("https://a.example/1")
    f.get("https://a.example/2")
    assert time.monotonic() - t0 >= 0.6  # robots.txt + 2 pages, 0.3 s apart


@respx.mock
def test_cache_and_no_cache(tmp_path):
    respx.get("https://a.example/robots.txt").respond(404)
    route = respx.get("https://a.example/page").respond(200, text="hello")
    f = Fetcher(cache_dir=tmp_path, min_interval=0)
    f.get("https://a.example/page?key=secret", use_cache=False)
    assert not any("secret" in p.read_text() for p in tmp_path.glob("*.json"))
    f.get("https://a.example/page")
    f.get("https://a.example/page")
    assert route.call_count == 2  # second plain call served from cache


@respx.mock
def test_robots_server_error_disallows():
    respx.get("https://a.example/robots.txt").respond(503)
    assert Fetcher(cache_dir=None, min_interval=0).allowed("https://a.example/page") is False


@respx.mock
def test_binary_responses_not_downloaded():
    respx.get("https://a.example/robots.txt").respond(404)
    respx.get("https://a.example/big.zip").respond(
        200, content=b"x" * 10, headers={"content-type": "application/zip"}
    )
    r = Fetcher(cache_dir=None, min_interval=0).get("https://a.example/big.zip")
    assert r.text == ""


@respx.mock
def test_unreachable_robots_allows_then_page_fails():
    respx.get("https://a.example/robots.txt").mock(side_effect=httpx.ConnectError("x"))
    assert Fetcher(cache_dir=None, min_interval=0).allowed("https://a.example/") is True
