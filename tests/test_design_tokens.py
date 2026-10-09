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

# a hex colour (not an HTML entity such as &#8239;, an id such as #add-artist or an SVG
# reference such as url(#fade)), or a colour function
COLOUR_RE = re.compile(
    r"(?<![&\w])(?<!url\()#[0-9a-fA-F]{3,8}(?![\w-])"
    r"|\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\("
)
# CSS named and system colours (CSS Color 4, https://www.w3.org/TR/css-color-4/#named-colors and
# #css-system-colors), looked for in colour properties only; transparent / currentColor are fine
NAMED = set(
    """aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue
    blueviolet brown burlywood cadetblue chartreuse chocolate coral cornflowerblue
    cornsilk crimson cyan darkblue darkcyan darkgoldenrod darkgray darkgreen darkgrey
    darkkhaki darkmagenta darkolivegreen darkorange darkorchid darkred darksalmon
    darkseagreen darkslateblue darkslategray darkslategrey darkturquoise darkviolet
    deeppink deepskyblue dimgray dimgrey dodgerblue firebrick floralwhite forestgreen
    fuchsia gainsboro ghostwhite gold goldenrod gray green greenyellow grey honeydew
    hotpink indianred indigo ivory khaki lavender lavenderblush lawngreen lemonchiffon
    lightblue lightcoral lightcyan lightgoldenrodyellow lightgray lightgreen lightgrey
    lightpink lightsalmon lightseagreen lightskyblue lightslategray lightslategrey
    lightsteelblue lightyellow lime limegreen linen magenta maroon mediumaquamarine
    mediumblue mediumorchid mediumpurple mediumseagreen mediumslateblue mediumspringgreen
    mediumturquoise mediumvioletred midnightblue mintcream mistyrose moccasin navajowhite
    navy oldlace olive olivedrab orange orangered orchid palegoldenrod palegreen
    paleturquoise palevioletred papayawhip peachpuff peru pink plum powderblue purple
    rebeccapurple red rosybrown royalblue saddlebrown salmon sandybrown seagreen seashell
    sienna silver skyblue slateblue slategray slategrey snow springgreen steelblue tan
    teal thistle tomato turquoise violet wheat white whitesmoke yellow yellowgreen
    accentcolor accentcolortext activetext buttonborder buttonface buttontext canvas
    canvastext field fieldtext graytext highlight highlighttext linktext mark marktext
    selecteditem selecteditemtext visitedtext""".split()
)
COLOUR_PROP_RE = re.compile(
    r"(?:^|[{;\s])(?:color|background(?:-color)?|border(?:-[a-z]+)*|outline(?:-color)?|fill|stroke"
    r"|box-shadow|text-shadow|text-decoration(?:-color)?|caret-color|accent-color|column-rule(?:-color)?)"
    r"\s*:\s*([^;}]+)",
    re.I,
)
# colours set from JavaScript: el.style.color = "red", style.setProperty("color", ...)
JS_STYLE_RE = re.compile(
    r"\.style\.\w*(?:[cC]olor|background|border\w*|outline\w*|fill|stroke)\s*=|setProperty\("
)


def named_colours(line: str) -> list[str]:
    found = []
    for value in COLOUR_PROP_RE.findall(line):
        value = re.sub(r"var\([^)]*\)", " ", value)  # token names such as --accent-ink are fine
        found += [w for w in re.findall(r"[a-z]+", value.lower()) if w in NAMED]
    return found


def test_tokens_css_is_generated_from_the_design_system():
    assert (WEB / "tokens.css").read_text(encoding="utf-8") == gen.render(gen.load())


def test_no_literal_colour_outside_tokens_css():
    offenders = []
    for path in sorted(WEB.glob("*")):
        if path.suffix not in {".css", ".js", ".html"} or path.name == "tokens.css":
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if COLOUR_RE.search(line) or named_colours(line):
                offenders.append(f"{path.name}:{n}: {line.strip()}")
            elif path.suffix == ".js" and JS_STYLE_RE.search(line):
                offenders.append(f"{path.name}:{n}: colour set from JS: {line.strip()}")
    assert not offenders, "\n".join(offenders)


def test_colour_regex_catches_literals_and_spares_entities():
    assert COLOUR_RE.search("color: #fff;")
    assert COLOUR_RE.search("background: rgb(0 0 0 / .4)")
    assert not COLOUR_RE.search("<span>4&#8239;000</span>")
    assert not COLOUR_RE.search("document.querySelector('#concerts')")
    assert not COLOUR_RE.search("document.querySelector('#add-artist')")
    assert not COLOUR_RE.search("fill: url(#fade);")
    assert COLOUR_RE.search("color: color(display-p3 1 0 0);")
    assert named_colours("a { color: tomato; }") == ["tomato"]
    assert named_colours("border: 1px solid Canvas") == ["canvas"]
    assert named_colours("color: var(--accent-ink); background: transparent;") == []
    assert named_colours("outline: 3px solid currentColor") == []


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
