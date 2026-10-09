"""Design tokens (WIP-94): tokens.css matches the design system and is the only file of the page
with literal colours; the contrast pairs of docs/DESIGN.md hold."""

import importlib.util
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parent.parent
WEB = ROOT / "src" / "nightcrawler" / "web"

_spec = importlib.util.spec_from_file_location("gen_tokens", ROOT / "scripts" / "gen_tokens.py")
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

# a hex colour (not an HTML entity such as &#8239;), or a colour function
COLOUR_RE = re.compile(r"(?<![&\w])#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch)\(")


def test_tokens_css_is_generated_from_the_design_system():
    assert (WEB / "tokens.css").read_text(encoding="utf-8") == gen.render(gen.load())


def test_no_literal_colour_outside_tokens_css():
    offenders = []
    for path in sorted(WEB.glob("*")):
        if path.suffix not in {".css", ".js", ".html"} or path.name == "tokens.css":
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if COLOUR_RE.search(line):
                offenders.append(f"{path.name}:{n}: {line.strip()}")
    assert not offenders, "\n".join(offenders)


def test_colour_regex_catches_literals_and_spares_entities():
    assert COLOUR_RE.search("color: #fff;")
    assert COLOUR_RE.search("background: rgb(0 0 0 / .4)")
    assert not COLOUR_RE.search("<span>4&#8239;000</span>")
    assert not COLOUR_RE.search("document.querySelector('#concerts')")


def test_text_pairs_meet_wcag_aa():
    ratios = {(fg, bg): r for fg, bg, r in gen.contrast_table(gen.load())}
    # the accent is a fill only (design system rule): 1.8:1 is expected, never used for text
    assert ratios.pop(("accent", "ground")) < 3
    for pair, ratio in ratios.items():
        assert ratio >= 4.5, pair
    # figures quoted by the design system and the ticket
    assert ratios[("ink", "accent")] == 9.9
    assert ratios[("accent-ink", "ground")] == 6.1
    assert ratios[("ink-muted", "ground")] == 7.3


def test_wcag_contrast_reference_values():
    assert round(gen.contrast("#000000", "#ffffff"), 1) == 21.0
    assert round(gen.contrast("#777777", "#ffffff"), 2) == 4.48  # WebAIM checker: 4.48:1


def test_fonts_are_self_hosted_and_packaged():
    css = (WEB / "tokens.css").read_text(encoding="utf-8")
    files = re.findall(r'url\("(fonts/[^"]+)"\)', css)
    assert len(files) == 4
    for f in files:
        assert (WEB / f).is_file(), f
    assert "googleapis" not in (WEB / "index.html").read_text(encoding="utf-8")
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "web/fonts/*" in data["tool"]["setuptools"]["package-data"]["nightcrawler"]


def test_page_loads_tokens_before_its_styles():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert html.index('href="tokens.css"') < html.index('href="style.css"')
