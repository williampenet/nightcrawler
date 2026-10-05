"""Zone configuration: everything location-specific lives in config, never in code."""

from __future__ import annotations

import math
from dataclasses import dataclass
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
    )
