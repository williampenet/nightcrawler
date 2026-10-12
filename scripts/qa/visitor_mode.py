# ruff: noqa: E501  (inline JavaScript and long check labels)
"""QA WIP-118 (ADR-0008): visitor, owner and refused-key modes of the page, on synthetic data,
with the feedback function faked (no network). Local only for now (Chromium needed; CI wiring
with the visual QA of WIP-100):

    python -m playwright install chromium
    python scripts/qa/visitor_mode.py /tmp/nc-qa-site
"""

import json
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
SITE = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/nc-qa-site")
SHOTS = SITE.parent / "shots"
FN = "https://fn.example/"

shutil.rmtree(SITE, ignore_errors=True)
shutil.copytree(ROOT / "src/nightcrawler/web", SITE)
(SITE / "data").mkdir()
SHOTS.mkdir(exist_ok=True)


def concert(i, day, title, artist=None):
    return {
        "id": f"{i:012x}",
        "title": title,
        "start": f"2026-10-{day}T20:30:00+02:00",
        "venue_id": "v1",
        "venue_name": "Le Périscope",
        "url": f"https://venue.example/{i}",
        "ticket_url": f"https://tickets.example/{i}",
        "performers": [title],
        "sources": ["web"],
        "reason": "music",
        "artists": [artist] if artist else [],
        "links": [],
        "aliases": [],
        "ai_extracted": False,
        "lineup": [title],
    }


concerts = [
    concert(1, 13, "Moondog Ensemble", "moondog"),
    concert(2, 14, "Free Noise Night"),
    concert(3, 15, "Variété du soir"),
]
artists = {"moondog": {"name": "Moondog", "deezer_id": "12345", "tags": ["minimal"]}}
report = {
    "generated_at": "2026-10-12T06:00:00+02:00",
    "zone": {"name": "Lyon et environs", "timezone": "Europe/Paris"},
    "venues": 1,
    "venues_with_website": 1,
    "venues_with_concerts": 1,
    "probe_status": {"structured": 1},
    "artists": {"identified": 1, "candidates": 3},
    "sources": {"gancio": {"instances": []}},
}
venues = [
    {
        "id": "v1",
        "name": "Le Périscope",
        "category": "music_venue",
        "website": "https://venue.example/",
        "probe": {"status": "structured"},
        "concerts": 3,
    }
]
public_verdicts = {
    "generated_at": "2026-10-12T06:00:00+02:00",
    "verdicts": {
        f"{2:012x}": {
            "section": "ne_pas_rater",
            "verdict": "must_see",
            "confidence": 90,
            "reason": "Du free jazz qui croise le noise, comme tu aimes.",
        },
        f"{3:012x}": {
            "section": "tout_voir",
            "verdict": "no",
            "confidence": 80,
            "reason": "De la variété grand public.",
        },
    },
}
public_profile = {
    "data": {
        "seeds": [{"name": "Moondog", "tags": ["minimal"]}],
        "taste_text": "Du free jazz qui croise autre chose.",
        "liked": [],
        "disliked": [],
    }
}
for name, body in {
    "concerts": concerts,
    "artists": artists,
    "report": report,
    "venues": venues,
    "verdicts": public_verdicts,
    "profile": public_profile,
}.items():
    (SITE / "data" / f"{name}.json").write_text(json.dumps(body, ensure_ascii=False))
(SITE / "app-config.json").write_text(json.dumps({"feedback_url": FN}))

server = subprocess.Popen(
    [sys.executable, "-m", "http.server", "8765", "-d", str(SITE)],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
time.sleep(1)
BASE = "http://localhost:8765/"
FORBIDDEN = [
    "J'aime",
    "Partager",
    "Pas pour moi",
    "Mauvais rapprochement",
    "À trier",
    "Saisir la clé",
    "Enregistrer",
    "Tout effacer",
    "Importer",
    "Connecter Spotify",
]
results = []


def check(label, ok, detail=""):
    results.append((label, bool(ok), detail))


def visible_controls(page):
    return page.evaluate("""() => [...document.querySelectorAll('button, a, input, textarea, select')]
      .filter(e => e.offsetParent !== null || e.getClientRects().length)
      .map(e => (e.getAttribute('aria-label') || e.textContent || e.tagName).trim().replace(/\\s+/g,' '))""")


def fake_function(page, status, calls):
    def handle(route):
        req = route.request
        calls.append((req.method, req.url))
        if status != 200:
            return route.fulfill(status=status, json={"error": "unauthorized"})
        if req.url.endswith("/verdicts"):
            body = {
                "generated_at": "x",
                "verdicts": {
                    f"{1:012x}": {
                        "section": "pour_toi",
                        "verdict": "for_you",
                        "confidence": 85,
                        "reason": "Live owner verdict.",
                    }
                },
            }
            return route.fulfill(status=200, json=body)
        if req.url.endswith("/profile"):
            if req.method == "GET":
                return route.fulfill(
                    status=200,
                    json={"data": {"seeds": [], "taste_text": "Owner text"}, "version": 3},
                )
            return route.fulfill(
                status=200, json={"data": json.loads(req.post_data)["data"], "version": 4}
            )
        return route.fulfill(status=200, json={"stored": 1})

    page.route("https://fn.example/**", handle)


with sync_playwright() as p:
    browser = p.chromium.launch()

    # Clock: 2026-10-12 10:00 Paris
    def new_page(token=None):
        ctx = browser.new_context(viewport={"width": 375, "height": 812})
        page = ctx.new_page()
        page.clock.install(time=datetime(2026, 10, 12, 8, tzinfo=UTC))  # 2026-10-12T08:00:00Z
        if token:
            page.add_init_script(f"localStorage.setItem('nightcrawler.feedback.token', '{token}')")
        return page

    # 1. visitor
    calls = []
    page = new_page()
    fake_function(page, 200, calls)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(BASE + "#concerts")
    page.wait_for_selector("#concerts li")
    page.screenshot(path=str(SHOTS / "visitor-home.png"), full_page=True)
    ctrls = visible_controls(page)
    check(
        "visitor: no forbidden control on home",
        not [c for c in ctrls if any(f in c for f in FORBIDDEN)],
        ctrls,
    )
    check(
        "visitor: the public must-see is shown with its AI reason",
        "Du free jazz qui croise le noise" in page.inner_text("#concerts"),
    )
    check(
        "visitor: header note",
        page.is_visible("text=La sélection de William, en consultation seule."),
    )
    page.click("text=Tout")
    page.wait_for_timeout(200)
    page.click("button.title-btn:has-text('Moondog Ensemble')")
    panel = page.inner_text("#concerts")
    check(
        "visitor: Page and Billets in details", "Page" in panel and "Billets" in panel, panel[:400]
    )
    check("visitor: no WhatsApp", "WhatsApp" not in panel)
    check("visitor: Écouter shown", page.locator("button.listen:has-text('Écouter')").count() >= 1)
    ctrls = visible_controls(page)
    check(
        "visitor: no forbidden control in Tout",
        not [c for c in ctrls if any(f in c for f in FORBIDDEN)],
        ctrls,
    )
    page.click("a[data-route=calendrier]")
    page.wait_for_timeout(200)
    page.screenshot(path=str(SHOTS / "visitor-calendar.png"), full_page=True)
    ctrls = visible_controls(page)
    check(
        "visitor: no forbidden control in calendar",
        not [c for c in ctrls if any(f in c for f in FORBIDDEN)],
        ctrls,
    )
    page.click("a[data-route=gouts]")
    page.wait_for_timeout(200)
    page.screenshot(path=str(SHOTS / "visitor-gouts.png"), full_page=True)
    gouts_buttons = page.evaluate("""() => [...document.querySelectorAll('#route-gouts button, #route-gouts input')]
      .filter(e => e.getClientRects().length).map(e => e.textContent || e.id)""")
    check("visitor: no button or input in Mes goûts", not gouts_buttons, gouts_buttons)
    check(
        "visitor: seeds read-only with William's artists",
        page.eval_on_selector("#seeds", "e => e.readOnly && e.value") == "Moondog",
    )
    check(
        "visitor: taste read-only with William's text",
        page.eval_on_selector("#taste-text", "e => e.readOnly && e.value")
        == "Du free jazz qui croise autre chose.",
    )
    stored = page.evaluate("() => localStorage.getItem('nightcrawler.v1')")
    check("visitor: nothing of the profile stored", stored is None, stored)
    check("visitor: no call to the function", not calls, calls)
    check("visitor: no page error", not errors, errors)

    # 2. hidden owner link
    page.goto(BASE + "#cle")
    page.wait_for_timeout(500)
    check(
        "#cle: key field shown and focused",
        page.evaluate("() => document.activeElement && document.activeElement.id")
        == "feedback-key",
    )
    page.screenshot(path=str(SHOTS / "visitor-cle.png"), full_page=True)

    # 3. owner
    calls = []
    page = new_page("good-key")
    fake_function(page, 200, calls)
    page.goto(BASE + "#concerts")
    page.wait_for_selector("#concerts li")
    page.wait_for_timeout(800)
    page.click("text=Tout")
    page.wait_for_timeout(200)
    ctrls = visible_controls(page)
    check(
        "owner: J'aime and Partager shown",
        any("J'aime" in c for c in ctrls) and any("Partager" in c for c in ctrls),
        ctrls,
    )
    check("owner: live verdict used", "Live owner verdict." in page.inner_text("#concerts"))
    check("owner: function called", any(u.endswith("/verdicts") for _, u in calls), calls)
    page.click("a[data-route=gouts]")
    page.wait_for_timeout(200)
    check("owner: Enregistrer shown", page.is_visible("#save-seeds"))
    check("owner: taste editable", page.eval_on_selector("#taste-text", "e => !e.readOnly"))
    page.screenshot(path=str(SHOTS / "owner-gouts.png"), full_page=True)

    # 4. refused key
    calls = []
    page = new_page("bad-key")
    fake_function(page, 401, calls)
    page.goto(BASE + "#concerts")
    page.wait_for_selector("#concerts li")
    page.wait_for_timeout(1000)
    page.click("text=Tout")
    page.wait_for_timeout(200)
    ctrls = visible_controls(page)
    check(
        "refused: back to visitor controls",
        not [c for c in ctrls if any(f in c for f in FORBIDDEN)],
        ctrls,
    )
    page.click("a[data-route=gouts]")
    page.wait_for_timeout(200)
    check("refused: key field shown to fix it", page.is_visible("#feedback-key"))
    check(
        "refused: public profile shown",
        page.eval_on_selector("#seeds", "e => e.value") == "Moondog",
    )

    # 5. a late 401 for a key already replaced (review of PR #105): William stays owner and his
    # live edit, not the public copy, is what reaches PUT /profile
    page = new_page()
    held, auths, puts = [], [], []

    def slow_bad_key(route):
        req = route.request
        auth = req.headers.get("authorization", "")
        auths.append(auth)
        if "bad-key" in auth:
            return held.append(route)  # answered later, after the right key is in
        if req.url.endswith("/verdicts"):
            return route.fulfill(status=200, json={"generated_at": "x", "verdicts": {}})
        if req.url.endswith("/profile"):
            if req.method == "GET":
                return route.fulfill(
                    status=200, json={"data": {"taste_text": "Owner text"}, "version": 3}
                )
            puts.append((auth, json.loads(req.post_data)["data"].get("taste_text")))
            return route.fulfill(
                status=200, json={"data": json.loads(req.post_data)["data"], "version": 4}
            )
        return route.fulfill(status=200, json={"stored": 1})

    page.route("https://fn.example/**", slow_bad_key)
    page.goto(BASE + "#cle")
    page.wait_for_timeout(500)
    page.fill("#feedback-key", "bad-key")
    page.press("#feedback-key", "Enter")
    page.wait_for_timeout(500)
    page.fill("#feedback-key", "good-key")
    page.press("#feedback-key", "Enter")
    page.wait_for_timeout(800)
    page.fill("#taste-text", "Owner text edited live")
    page.wait_for_timeout(200)
    for r in held:
        r.fulfill(status=401, json={"error": "unauthorized"})
    page.wait_for_timeout(2500)
    check("late 401 of an old key: still owner", page.is_visible("#save-seeds"))
    check(
        "late 401 of an old key: the live edit is pushed",
        puts and puts[-1] == ("Bearer good-key", "Owner text edited live"),
        puts,
    )

    # 6. clearing the key: focus on the heading, the owner's verdict copy dropped
    page.fill("#feedback-key", "")
    page.press("#feedback-key", "Enter")
    page.wait_for_timeout(800)
    focus = page.evaluate(
        "() => [document.activeElement.id || document.activeElement.tagName, document.getElementById('feedback-row').hidden, location.hash]"
    )
    check(
        "cleared key: focus kept (key field still shown on #cle) or on the heading",
        focus[0] == ("taste-title" if focus[1] else "feedback-key"),
        focus,
    )
    check(
        "cleared key: verdict copy dropped",
        page.evaluate("() => localStorage.getItem('nightcrawler.verdicts')") is None,
    )
    check("cleared key: visitor controls", page.is_hidden("#save-seeds"))
    browser.close()

server.terminate()
bad = [r for r in results if not r[1]]
for label, ok, detail in results:
    print(("PASS " if ok else "FAIL ") + label + ("" if ok else f"  -> {detail}"))
print(f"{len(results) - len(bad)}/{len(results)} passed")
sys.exit(1 if bad else 0)
