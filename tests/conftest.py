from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from nightcrawler.config import Zone

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_text():
    return lambda name: (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def tz():
    return ZoneInfo("Europe/Paris")


@pytest.fixture
def zone():
    return Zone(name="Test", latitude=45.7578, longitude=4.832, radius_km=15, window_days=60)
