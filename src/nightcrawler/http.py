"""Polite HTTP client: identifies itself, honours robots.txt, rate-limits per host, caches."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

log = logging.getLogger(__name__)

PROJECT_URL = "https://github.com/williampenet/nightcrawler"
USER_AGENT = f"NightcrawlerBot/0.1 (+{PROJECT_URL})"
MAX_BYTES = 3_000_000
TIMEOUT = 20.0
MAX_CRAWL_DELAY = 60.0  # seconds; a longer robots.txt Crawl-delay means "do not crawl" to us


TEXT_TYPES = ("text/", "html", "xml", "json", "calendar")


def _is_text(content_type: str) -> bool:
    return not content_type or any(t in content_type.lower() for t in TEXT_TYPES)


class RobotsBlocked(Exception):
    """Raised when robots.txt disallows a URL."""


@dataclass
class Response:
    url: str
    status: int
    text: str
    content_type: str


class Fetcher:
    """Thread-safe fetcher.

    - one request per host every `min_interval` seconds
    - robots.txt checked once per host (missing or unreadable robots.txt = allowed)
    - on-disk cache: a cached body younger than `max_age` seconds is reused
    """

    def __init__(
        self,
        cache_dir: str | Path | None = ".cache/http",
        min_interval: float = 1.0,
        max_age: float = 20 * 3600,
        client: httpx.Client | None = None,
        host_intervals: dict[str, float] | None = None,
    ) -> None:
        self.client = client or httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "fr,en;q=0.8"},
            timeout=TIMEOUT,
            follow_redirects=True,
        )
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.min_interval = min_interval
        self.host_intervals = host_intervals or {}
        self.crawl_delays: dict[str, float] = {}  # host -> robots.txt Crawl-delay honoured
        self.max_age = max_age
        self._robots: dict[str, RobotFileParser | None] = {}
        self._last_hit: dict[str, float] = {}
        self._host_locks: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()
        self._prune_cache()

    # -- public -------------------------------------------------------------

    def get(
        self,
        url: str,
        *,
        check_robots: bool = True,
        params: dict | None = None,
        use_cache: bool = True,
    ) -> Response:
        """GET a URL. Pass use_cache=False when the URL carries a secret (API key)."""
        if params:
            url = str(httpx.URL(url, params=params))
        cached = self._cache_read(url) if use_cache else None
        if cached:
            return cached
        host = urlsplit(url).netloc.lower()
        if check_robots and not self.allowed(url):
            raise RobotsBlocked(url)
        with self._host_lock(host):
            self._wait_turn(host)
            with self.client.stream("GET", url) as resp:
                content_type = resp.headers.get("content-type", "")
                body = self._read_capped(resp) if _is_text(content_type) else b""
                final_url, status, encoding = str(resp.url), resp.status_code, resp.encoding
        # a redirect to another host must also be allowed by that host's robots.txt
        if (
            check_robots
            and urlsplit(final_url).netloc.lower() != host
            and not self.allowed(final_url)
        ):
            raise RobotsBlocked(final_url)
        out = Response(
            url=final_url,
            status=status,
            text=body.decode(encoding or "utf-8", errors="replace"),
            content_type=content_type,
        )
        if status == 200 and use_cache:
            self._cache_write(url, out)
        return out

    @staticmethod
    def _read_capped(resp: httpx.Response) -> bytes:
        chunks: list[bytes] = []
        size = 0
        for chunk in resp.iter_bytes():
            chunks.append(chunk)
            size += len(chunk)
            if size >= MAX_BYTES:
                break
        return b"".join(chunks)[:MAX_BYTES]

    def post(
        self,
        url: str,
        data: dict,
        *,
        headers: dict | None = None,
        timeout: float = TIMEOUT,
    ) -> Response:
        """Uncached POST, used for APIs (e.g. Overpass)."""
        host = urlsplit(url).netloc.lower()
        with self._host_lock(host):
            self._wait_turn(host)
            resp = self.client.post(url, data=data, headers=headers, timeout=timeout)
        return Response(
            url=str(resp.url),
            status=resp.status_code,
            text=resp.text,
            content_type=resp.headers.get("content-type", ""),
        )

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        with self._lock:
            known = base in self._robots
        if not known:
            parser: RobotFileParser | None = None
            try:
                r = self.get(base + "/robots.txt", check_robots=False)
                if r.status == 200 and "html" not in r.content_type:
                    parser = RobotFileParser()
                    parser.parse(r.text.splitlines())
                    self._honour_crawl_delay(parts.netloc.lower(), parser)
                elif r.status >= 500:  # RFC 9309: server error means disallow all
                    parser = RobotFileParser()
                    parser.disallow_all = True
            except httpx.HTTPError:
                parser = None  # unreachable robots.txt: the page fetch will fail on its own
            with self._lock:
                self._robots[base] = parser
        parser = self._robots[base]
        return True if parser is None else parser.can_fetch(USER_AGENT, url)

    # -- internals ----------------------------------------------------------

    def _honour_crawl_delay(self, host: str, parser: RobotFileParser) -> None:
        """`Crawl-delay` for our agent (or `*`) widens this host's interval (La Rayonne asks
        10 s, larayonne.org/robots.txt, 2026-10-07). Above MAX_CRAWL_DELAY the host is
        treated as disallowed: waiting minutes per page would stall the run."""
        try:
            delay = float(parser.crawl_delay(USER_AGENT) or 0)
        except (TypeError, ValueError):
            return
        if delay > MAX_CRAWL_DELAY:
            log.info("%s: Crawl-delay %s s, host skipped", host, delay)
            parser.disallow_all = True
        elif delay > 0:
            with self._lock:
                self.crawl_delays[host] = delay
                current = self.host_intervals.get(host, self.min_interval)
                self.host_intervals[host] = max(current, delay)

    def _host_lock(self, host: str) -> threading.Lock:
        with self._lock:
            return self._host_locks.setdefault(host, threading.Lock())

    def _wait_turn(self, host: str) -> None:
        last = self._last_hit.get(host)
        if last is not None:
            interval = self.host_intervals.get(host, self.min_interval)
            delay = interval - (time.monotonic() - last)
            if delay > 0:
                time.sleep(delay)
        self._last_hit[host] = time.monotonic()

    def cached(self, url: str) -> Response | None:
        """The cached response for `url` if one is fresh: no request, no rate limit."""
        return self._cache_read(url)

    def forget(self, url: str, params: dict | None = None) -> None:
        """Drop a cached response (e.g. an API error returned with HTTP 200)."""
        if params:
            url = str(httpx.URL(url, params=params))
        path = self._cache_path(url)
        if path:
            path.unlink(missing_ok=True)

    def _prune_cache(self) -> None:
        """Delete expired cache entries so the cache does not grow forever."""
        if not self.cache_dir or not self.cache_dir.exists():
            return
        cutoff = time.time() - self.max_age
        for path in self.cache_dir.glob("*.json"):
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)

    def _cache_path(self, url: str) -> Path | None:
        if not self.cache_dir:
            return None
        return self.cache_dir / (hashlib.sha256(url.encode()).hexdigest() + ".json")

    def _cache_read(self, url: str) -> Response | None:
        path = self._cache_path(url)
        if not path or not path.exists():
            return None
        if time.time() - path.stat().st_mtime > self.max_age:
            return None
        try:
            return Response(**json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, TypeError):
            return None

    def _cache_write(self, url: str, resp: Response) -> None:
        path = self._cache_path(url)
        if not path:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(resp.__dict__), encoding="utf-8")
