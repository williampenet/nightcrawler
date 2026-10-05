"""Core data types shared by sources, the probe and the pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

# Venue categories, from most to least likely to host concerts.
MUSIC_CATEGORIES = {"music_venue", "concert_hall", "nightclub", "live_music"}
MIXED_CATEGORIES = {
    "arts_centre",
    "theatre",
    "events_venue",
    "social_centre",
    "community_centre",
}


@dataclass
class Venue:
    id: str  # stable id, e.g. "osm:node/123" or "tm:KovZ..."
    name: str
    latitude: float
    longitude: float
    category: str
    website: str | None = None
    address: str | None = None
    sources: list[str] = field(default_factory=list)

    @property
    def is_music_venue(self) -> bool:
        return self.category in MUSIC_CATEGORIES

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Probe:
    """Result of probing a venue website for its agenda."""

    venue_id: str
    status: str  # see PROBE_STATUSES
    method: str | None = None  # "json-ld", "microdata", "ical"
    agenda_url: str | None = None
    events_found: int = 0
    platforms: list[str] = field(default_factory=list)
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


PROBE_STATUSES = (
    "structured",  # events found in machine-readable form
    "platform_only",  # a ticketing widget was found, but no readable events
    "no_agenda",  # site reachable, nothing machine-readable
    "robots_blocked",  # robots.txt disallows us
    "fetch_error",  # site unreachable or error
    "no_website",  # nothing to probe
)


@dataclass
class RawEvent:
    """An event as read from a source, before normalisation."""

    title: str
    start: datetime
    source: str  # "json-ld", "microdata", "ical", "ticketmaster"
    venue_id: str
    url: str | None = None
    ticket_url: str | None = None
    performers: list[str] = field(default_factory=list)
    description: str | None = None
    types: list[str] = field(default_factory=list)  # schema.org types, e.g. ["MusicEvent"]
    location_name: str | None = None


@dataclass
class Concert:
    id: str
    title: str
    start: str  # ISO 8601 with offset
    venue_id: str
    venue_name: str
    url: str | None
    ticket_url: str | None
    performers: list[str]
    sources: list[str]
    reason: str  # why it was kept as a concert

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
