"""Runs the page's JavaScript unit tests when Node.js is available (it is on CI runners)."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_scoring_js():
    files = sorted(str(p) for p in (ROOT / "tests" / "js").glob("*.test.js"))
    result = subprocess.run(["node", "--test", *files], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_page_scripts_parse():
    for name in (
        "app.js",
        "scoring.js",
        "feedback.js",
        "profile.js",
        "verdicts.js",
        "visits.js",
        "calendar.js",
    ):
        path = ROOT / "src" / "nightcrawler" / "web" / name
        result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
