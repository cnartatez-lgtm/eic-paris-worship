#!/usr/bin/env python3

"""
Rebuild a scanned Ultimate Guitar printout from Ultimate Guitar's own text.

Most scanned charts in the library are printouts of Ultimate Guitar
pages. OCR reads their chord positions well but slips on names and on
bar lines ("| G D | Em C |"). Ultimate Guitar still serves the same
chart as text, every chord in its exact column, so the printout can be
rebuilt exactly from it — once the right version is found and put in
the printout's key.

This tool finds it by comparison, not by trust:

1. Search Ultimate Guitar for the song title (public pages, no login).
2. For each "Chords" version, read the text, and try all 12
   transpositions, plain and "simplified" (no bass notes, no 7ths —
   Ultimate Guitar's print option, used for some printouts).
3. Score each against the chord sequence read from the printout (the
   current chart) and keep the best. Below MIN_SCORE nothing is
   written: the printout is a different arrangement.

The result still has to pass chart_gate.py: the PDF has the last word
(on 14 November 2026 a printout carried four chords the current UG
revision no longer has).

    ug_match.py slug__key            # show the best match
    ug_match.py slug__key --write    # rebuild the chart from it
"""

from __future__ import annotations

import argparse
import difflib
import functools
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "tools"))

import chart_layout as cl  # noqa: E402

CACHE = ROOT / "data" / "songs"

AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

MIN_SCORE = 0.85

FLAT_KEYS = {"C", "F", "Bb", "Eb", "Ab", "Db", "Gb", "Dm", "Gm", "Cm", "Fm"}


def get(url):
    request = urllib.request.Request(url, headers={"User-Agent": AGENT})

    with urllib.request.urlopen(request, timeout=40) as response:
        return response.read().decode("utf-8", "replace")


def store(page):
    found = re.search(r'class="js-store" data-content="([^"]*)"', page)
    return json.loads(html.unescape(found.group(1))) if found else {}


@functools.lru_cache(maxsize=None)
def search(title):
    url = ("https://www.ultimate-guitar.com/search.php?search_type=title&value="
           + urllib.parse.quote(title))
    results = store(get(url)).get("store", {}).get("page", {}).get("data", {}).get("results", [])
    want = re.sub(r"[^a-z0-9]", "", title.lower())

    return [
        r for r in results
        if r.get("type") == "Chords"
        and re.sub(r"[^a-z0-9]", "", str(r.get("song_name", "")).lower()) == want
    ]


@functools.lru_cache(maxsize=None)
def content(url):
    data = store(get(url)).get("store", {}).get("page", {}).get("data", {})
    text = data.get("tab_view", {}).get("wiki_tab", {}).get("content", "")
    text = html.unescape(re.sub(r"\[/?(ch|tab)\]", "", text.replace("\r", "")))
    return text, data.get("tab", {})


def pitch(chord):
    """A chord as pitch classes, so G# and Ab compare equal."""

    m = re.match(r"^\(?([A-G](?:#|b)?)([^/)]*)(?:/([A-G](?:#|b)?))?\)?$", chord)

    if not m:
        return chord

    root = cl.INDEX.get(m.group(1), m.group(1))
    bass = cl.INDEX.get(m.group(3), "") if m.group(3) else ""
    return f"{root}{m.group(2)}/{bass}"


def chords(sections):
    return [
        part["chord"]
        for sec in sections
        for line in sec["lines"]
        for part in line["parts"]
        if part.get("chord") and cl.is_chord(part["chord"])
    ]


def variants(text, key):
    sharps = key not in FLAT_KEYS
    base = cl.read_text(text)

    for simple in (False, True):
        sections = cl.map_chords(base, cl.simplify_chord, drop_repeats=True) if simple else base

        for shift in range(12):
            moved = cl.map_chords(sections, lambda c: cl.transpose_chord(c, shift, sharps)) \
                if shift else sections
            yield simple, shift, moved


def best(song, title, quiet=False):
    target = [pitch(c) for c in chords(song["sections"])]
    found = []

    for result in search(title)[:10]:
        fresh = content.cache_info().currsize
        try:
            text, tab = content(result["tab_url"])
        except Exception:  # noqa: BLE001
            continue

        if content.cache_info().currsize > fresh:
            time.sleep(1)  # gentle with Ultimate Guitar

        for simple, shift, sections in variants(text, song.get("key", "")):
            mine = [pitch(c) for c in chords(sections)]
            score = difflib.SequenceMatcher(a=target, b=mine, autojunk=False).ratio()
            found.append((score, result["tab_url"], simple, shift, sections, result))

    found.sort(key=lambda f: -f[0])

    if not quiet:
        for score, url, simple, shift, _s, r in found[:5]:
            print(f"  {score:.3f}  {r.get('artist_name')} v{r.get('version')} "
                  f"{'simplificada ' if simple else ''}+{shift}  {url}")

    return found[0] if found else None


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("charts", nargs="+")
    parser.add_argument("--title", help="Search title, if not the chart's own.")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    for stem in args.charts:
        path = CACHE / f"{stem}.json"
        song = json.loads(path.read_text(encoding="utf-8"))
        title = args.title or song.get("title", "")
        print(f"== {stem} ({title})")
        top = best(song, title)

        if not top or top[0] < MIN_SCORE:
            print(f"   sin coincidencia suficiente (mejor {top[0]:.2f})" if top else "   sin resultados")
            continue

        score, url, simple, shift, sections, _r = top

        if args.write:
            song.update({
                "sections": sections,
                "method": "ug-text",
                "model": "",
                "status": "new",
                "source_url": url,
                "note": (f"Rebuilt from the Ultimate Guitar text of the printout "
                         f"({'simplified, ' if simple else ''}transposed +{shift}); "
                         f"match with the PDF reading {score:.2f}."),
                "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            })
            # The stored review keeps the model's reading of the PDF, which
            # the gate compares with the rebuilt chart at no cost.
            song.pop("quality", None)
            path.write_text(json.dumps(song, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"   → reconstruida desde UG ({score:.2f})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
