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
def test_crawl_delay_widens_the_host_interval():
    # larayonne.org/robots.txt, 2026-10-07: "User-agent: *" then "Crawl-delay: 10"
    robots = "User-agent: *\nCrawl-delay: 10\n"
    respx.get("https://a.example/robots.txt").respond(200, text=robots)
    respx.get("https://b.example/robots.txt").respond(200, text="User-agent: *\nCrawl-delay: 600")
    f = Fetcher(cache_dir=None, min_interval=1.0, host_intervals={"c.example": 0.2})
    assert f.allowed("https://a.example/agenda/") is True
    assert f.host_intervals == {"c.example": 0.2, "a.example": 10.0}
    assert f.crawl_delays == {"a.example": 10.0}  # counted in report.json
    assert f.allowed("https://b.example/agenda/") is False  # beyond MAX_CRAWL_DELAY


@respx.mock
def test_unreachable_robots_allows_then_page_fails():
    respx.get("https://a.example/robots.txt").mock(side_effect=httpx.ConnectError("x"))
    assert Fetcher(cache_dir=None, min_interval=0).allowed("https://a.example/") is True


def test_threads_share_one_robots_fetch_and_the_crawl_delay():
    """WIP-107 review: threads reaching a cold host together (probe, "Mes salles" reader, Gancio
    details) fetch robots.txt once, and every page waits the host's Crawl-delay, robots.txt
    included. Before the fix a second robots.txt went out 1 s after the first."""
    import threading

    hits: list[tuple[str, float]] = []
    lock = threading.Lock()
    t0 = time.monotonic()

    def handler(request: httpx.Request) -> httpx.Response:
        with lock:
            hits.append((request.url.path, time.monotonic() - t0))
        if request.url.path == "/robots.txt":
            time.sleep(0.1)  # a slow answer widens the race window
            return httpx.Response(200, text="User-agent: *\nCrawl-delay: 1\n")  # ints only
        return httpx.Response(200, text="page")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    f = Fetcher(cache_dir=None, min_interval=0.05, client=client)
    threads = [threading.Thread(target=f.get, args=(f"https://a.example/p{n}",)) for n in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    paths = [p for p, _ in hits]
    assert paths.count("/robots.txt") == 1 and len(paths) == 4
    times = sorted(t for _, t in hits)
    gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
    assert min(gaps) >= 0.99  # Crawl-delay 1 s between every request to the host
