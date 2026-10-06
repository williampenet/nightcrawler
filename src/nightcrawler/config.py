"""Zone configuration: everything location-specific lives in config, never in code."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Zone:
    name: str
    latitude: float
    longitude: float
    radius_km: float
    timezone: str = "Europe/Paris"
    window_days: int = 60
    osm_extract_url: str | None = None
    # Gancio community agendas: ({"name": ..., "url": ...}, ...)
    gancio_instances: tuple[dict, ...] = field(default=())
    # venue names dropped from every source, matched on normalised names (WIP-51)
    excluded_venues: tuple[str, ...] = field(default=())
    # "Mes salles" read by a configured reader (WIP-60): ({"name", "venue", "reader"}, ...)
    priority_venues: tuple[dict, ...] = field(default=())

    def contains(self, latitude: float, longitude: float) -> bool:
        """True when the point is within radius_km of the zone centre (haversine)."""
        p1, p2 = math.radians(self.latitude), math.radians(latitude)
        dl = math.radians(longitude - self.longitude)
        h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * 6371.0 * math.asin(math.sqrt(h)) <= self.radius_km

    def bbox(self) -> tuple[float, float, float, float]:
        """(min_lon, min_lat, max_lon, max_lat) enclosing the radius."""
        dlat = self.radius_km / 111.32
        dlon = self.radius_km / (111.32 * math.cos(math.radians(self.latitude)))
        return (
            round(self.longitude - dlon, 5),
            round(self.latitude - dlat, 5),
            round(self.longitude + dlon, 5),
            round(self.latitude + dlat, 5),
        )


def load_zone(path: str | Path = "config/zone.yaml") -> Zone:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return Zone(
        name=str(data["name"]),
        latitude=float(data["latitude"]),
        longitude=float(data["longitude"]),
        radius_km=float(data["radius_km"]),
        timezone=str(data.get("timezone", "Europe/Paris")),
        window_days=int(data.get("window_days", 60)),
        osm_extract_url=data.get("osm_extract_url"),
        gancio_instances=tuple(
            {"name": str(i.get("name") or i["url"]), "url": str(i["url"])}
            for i in data.get("gancio_instances") or []
        ),
        excluded_venues=tuple(str(n) for n in data.get("excluded_venues") or []),
        priority_venues=tuple(_priority_venue(e) for e in data.get("priority_venues") or []),
    )


def _priority_venue(entry: dict) -> dict:
    """Checks a `priority_venues` entry: name, venue, reader {type, url, fields, date_format}."""
    reader = dict(entry["reader"])
    fields = {str(k): str(v) for k, v in dict(reader["fields"]).items()}
    if not str(reader["url"]).startswith("https://"):
        raise ValueError(f"priority venue {entry['name']}: reader url must be https")
    if "title" not in fields or "date" not in fields or not reader.get("date_format"):
        raise ValueError(
            f"priority venue {entry['name']}: needs fields.title, fields.date, date_format"
        )
    # WordPress caps per_page at 100:
    # https://developer.wordpress.org/rest-api/using-the-rest-api/pagination/
    for key, top in (("per_page", 100), ("max_pages", None)):
        n = reader.get(key)
        if n is None:
            continue
        if isinstance(n, bool) or not isinstance(n, int) or n < 1 or (top and n > top):
            limit = f"1..{top}" if top else ">= 1"
            raise ValueError(f"priority venue {entry['name']}: {key} must be an int in {limit}")
    reader["fields"] = fields
    return {
        "name": str(entry["name"]),
        "venue": str(entry.get("venue") or entry["name"]),
        "reader": reader,
    }
