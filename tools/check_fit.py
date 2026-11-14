#!/usr/bin/env python3

"""
Does every chart fit the tablet, and was every chart read properly?

Two checks, both free (no model calls):

1. Layout. The built index.html is opened in a headless Chrome at the
   tablet's screen size, with "#audit" at the end of the address. The
   page then lays out every chart in the archive exactly as the tablet
   would and writes what it found into itself. Reported per chart: the
   type size, the columns on screen and in total. A chart either fits
   one screen at tablet.min_font_px or larger, or keeps that size and
   continues in more columns reached with the slider. Anything cut at
   the bottom or spilling sideways is a fault in the page.

2. Data. A chart that came out of the model badly cannot be fixed by
   layout. Flagged from the cache:
     - empty: sections with no lines at all;
     - merged: a whole verse run into one line (the browser then
       breaks it wherever the column ends);
     - floating chords: many chords sitting on the gaps between words
       instead of on a syllable. Seen on the "Horizontal-Simplificada"
       charts, which print the chords UNDER the lyric line; the model
       read them as belonging to the next line.
   The fix is to re-read the chart (song_extract.py --force), never to
   edit the page.

Run by worship_app.py after every build; the summary goes into the
Saturday Telegram report. By hand:

    check_fit.py                 # the built page from the config
    check_fit.py --page X.html   # another build
    check_fit.py --all           # every chart, not only the problems
"""

from __future__ import annotations

import argparse
import glob
import html
import json
import os
import re
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

CACHE = ROOT / "data" / "songs"

DEFAULT_VIEWPORT = (1280, 728)

# A line longer than this is a verse run together, not a line.
MERGED_CHARS = 110

# Share of chords sitting between words that marks a misread chart.
# Good charts measure 0-14 %, misread ones 22-78 %.
FLOATING_SHARE = 0.20


def find_chrome(config):
    """A headless Chrome, or None. Never installs anything."""

    wanted = (config.get("tablet") or {}).get("browser") or os.environ.get(
        "EDEN_CHROME"
    )

    if wanted and Path(wanted).expanduser().exists():
        return str(Path(wanted).expanduser())

    shells = sorted(
        glob.glob(
            str(
                Path.home()
                / ".cache/ms-playwright/chromium_headless_shell-*"
                / "chrome-headless-shell-linux64/chrome-headless-shell"
            )
        )
    )

    if shells:
        return shells[-1]

    for name in ("chromium", "chromium-browser", "google-chrome"):
        path = shutil.which(name)

        if path:
            return path

    return None


def audit_layout(page, chrome, viewport):
    """What the page itself reports at this screen size."""

    width, height = viewport

    result = subprocess.run(
        [
            chrome,
            "--headless",
            "--disable-gpu",
            "--hide-scrollbars",
            f"--window-size={width},{height}",
            "--force-device-scale-factor=2",
            "--dump-dom",
            Path(page).resolve().as_uri() + "#audit",
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )

    found = re.search(r'<pre id="audit">(.*?)</pre>', result.stdout, re.S)

    if not found:
        raise RuntimeError(
            "la página no devolvió la revisión "
            "(¿plantilla sin el modo #audit?)"
        )

    return json.loads(html.unescape(found.group(1)))


def chart_problems(song):
    """Reasons a cached chart looks misread. Empty list if it looks fine."""

    lines = [
        line
        for section in song.get("sections", [])
        for line in section.get("lines", [])
    ]

    if not lines:
        return ["vacía"]

    reasons = []

    text_of = lambda line: "".join(  # noqa: E731
        part.get("text") or "" for part in line.get("parts", [])
    )

    if max(len(text_of(line)) for line in lines) > MERGED_CHARS:
        reasons.append("versos pegados en una sola línea")

    chords = floating = 0

    for line in lines:
        parts = line.get("parts", [])
        words = [bool((p.get("text") or "").strip()) for p in parts]

        if not any(words):
            # A chord-only line (intro, instrumental) is not misread.
            chords += sum(1 for p in parts if p.get("chord"))
            continue

        for i, part in enumerate(parts):
            if not part.get("chord"):
                continue

            chords += 1

            if not words[i] and any(words[:i]) and any(words[i + 1:]):
                floating += 1

    if chords and floating / chords > FLOATING_SHARE:
        reasons.append(
            f"acordes fuera de su sílaba ({floating} de {chords})"
        )

    return reasons


def data_problems():
    """{(slug, key): [reasons]} for every cached chart that looks misread."""

    found = {}

    for path in sorted(CACHE.glob("*__*.json")):
        slug, key = path.stem.split("__", 1)

        try:
            song = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            found[(slug, key)] = ["no se puede leer el archivo"]
            continue

        reasons = chart_problems(song)

        if reasons:
            found[(slug, key)] = reasons

    return found


def check(config, page, sunday=()):
    """Run both checks. Returns a dict; never raises for a missing browser."""

    tablet = config.get("tablet") or {}
    viewport = tuple(tablet.get("viewport") or DEFAULT_VIEWPORT)
    data = data_problems()

    chrome = find_chrome(config)

    if not chrome:
        return {"layout": None, "why": "no hay Chrome sin pantalla", "data": data}

    try:
        layout = audit_layout(page, chrome, viewport)
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as exc:
        return {"layout": None, "why": str(exc), "data": data}

    return {"layout": layout, "why": "", "data": data, "sunday": list(sunday)}


def summary(result, sunday=()):
    """Lines for the Saturday report, in Spanish."""

    lines = []
    layout = result.get("layout")
    data = result.get("data") or {}
    sunday = set(sunday)

    if layout is None:
        lines.append(
            "📏 Tableta: no se pudo revisar el ajuste "
            f"({result.get('why')})."
        )
    else:
        rows = layout["rows"]
        width, height = layout["viewport"]
        one = [r for r in rows if r["columns"] <= r["on_screen"]]
        faults = [
            r
            for r in rows
            if r["wide"] or r["cut"] or r["px"] < layout["min_px"]
        ]

        lines.append(
            f"📏 Tableta {width}×{height}, letra mínima "
            f"{layout['min_px']} px: {len(one)} de {len(rows)} cartas en "
            f"una pantalla; {len(rows) - len(one)} siguen con el "
            "deslizador."
        )

        mine = {}

        for r in rows:
            if r["slug"] in sunday:
                mine.setdefault(r["title"], []).append(
                    f"{r['key']} "
                    + (
                        "1 pantalla"
                        if r["columns"] <= r["on_screen"]
                        else f"{r['columns']} col."
                    )
                )

        for title, keys in mine.items():
            lines.append(f"   {title}: " + " · ".join(keys))

        for r in faults:
            why = (
                "se corta"
                if r["cut"]
                else "una palabra no cabe"
                if r["wide"]
                else f"solo cabe a {r['px']} px"
            )
            lines.append(f"⚠️ Tableta: {r['title']} {r['key']}: {why}.")

    bad_sunday = {k: v for k, v in data.items() if k[0] in sunday}
    titles = {r["slug"]: r["title"] for r in (layout or {}).get("rows", [])}

    for (slug, key), reasons in sorted(bad_sunday.items()):
        lines.append(
            f"⚠️ Carta mal leída este domingo: {titles.get(slug, slug)} {key}: "
            + "; ".join(reasons)
            + ". Volver a leerla con song_extract.py --force."
        )

    others = len(data) - len(bad_sunday)

    if others:
        lines.append(
            f"   Otras {others} cartas del archivo parecen mal leídas "
            "(check_fit.py --all)."
        )

    return lines


def main():
    import build_site

    parser = argparse.ArgumentParser(
        description="Check every chart against the tablet screen."
    )

    parser.add_argument("--page", help="The built index.html.")

    parser.add_argument(
        "--all",
        action="store_true",
        help="List every chart, not only the problems.",
    )

    args = parser.parse_args()

    config = build_site.load_config()

    page = args.page or str(
        Path(config.get("output", "~/AI-Workspace/worship/tablet/app"))
        .expanduser()
        / "index.html"
    )

    result = check(config, page)

    for line in summary(result):
        print(line)

    layout = result.get("layout")

    if args.all and layout:
        print()

        for r in layout["rows"]:
            print(
                f"  {r['title'][:38]:38} {r['key']}  {r['px']:>2} px  "
                f"{r['on_screen']} en pantalla / {r['columns']} col."
            )

    if result["data"]:
        print()
        print("Cartas que parecen mal leídas:")

        for (slug, key), reasons in sorted(result["data"].items()):
            print(f"  {slug} {key}: " + "; ".join(reasons))

    faults = layout and any(
        r["wide"] or r["cut"] or r["px"] < layout["min_px"]
        for r in layout["rows"]
    )

    return 1 if faults else 0


if __name__ == "__main__":
    raise SystemExit(main())
