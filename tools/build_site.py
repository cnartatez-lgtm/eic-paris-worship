#!/usr/bin/env python3

"""
Setlists plus the song cache become one self-contained page.

The page is opened from a local file on the tablet, and a file://
page is not allowed to fetch a sibling JSON — the browser blocks it
as a cross-origin read. So the data is baked into the HTML instead
of loaded beside it, and the whole app is one file that works with
no server and no network.

Rendering is plain Python on purpose. The model reads the charts;
this turns the result into a page. That split is what keeps every
song looking identical and what makes rebuilding free.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

CONFIG = ROOT / "config" / "app.json"

CACHE = ROOT / "data" / "songs"

SETLISTS = ROOT / "data" / "setlists"

TEMPLATE = ROOT / "template" / "app.html"

PLACEHOLDER = "__SONG_DATA__"


def load_config():
    data = {}

    if CONFIG.exists():
        data = json.loads(CONFIG.read_text(encoding="utf-8"))

    return data


def slugify(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def load_song(slug, key):
    path = CACHE / f"{slug}__{key}.json"

    if not path.exists():
        return None

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def trim(song):
    """Only what the page draws — the cache keeps the rest."""

    return {
        "title": song.get("title", ""),
        "author": song.get("author", ""),
        "meta": song.get("meta", {}),
        "sections": song.get("sections", []),
    }


def entry_for(slug, title, author, keys):
    """One song with every key it exists in."""

    found = {}

    for key in keys:
        song = load_song(slug, key)

        if song:
            found[key] = trim(song)

    if not found:
        return None

    # Open on the first configured key the song actually has, so
    # the sheet never lands on a key with nothing behind it.
    default_key = next(k for k in keys if k in found)

    # The chart's own title reads properly ("Word of God"); the
    # library filename is CamelCase ("WordOfGod") and only stands
    # in when the chart did not name itself.
    first = found[default_key]

    return {
        "slug": slug,
        "title": first.get("title") or title or slug,
        "author": first.get("author") or author or "",
        "keys": found,
        "default_key": default_key,
    }


def read_setlists():
    """Every stored setlist, newest first."""

    rows = []

    for path in sorted(SETLISTS.glob("*.json")):
        try:
            rows.append(
                json.loads(path.read_text(encoding="utf-8"))
            )
        except (OSError, json.JSONDecodeError):
            continue

    rows.sort(
        key=lambda r: str(r.get("sort_date") or ""),
        reverse=True,
    )

    return rows


def build(config, current_only=True):
    keys = config.get("keys", ["G", "D", "C"])

    setlists = read_setlists()

    # Christian asked for the coming Sunday only. Every setlist
    # shares the newest sort_date — one per service type that
    # week — so the front page can hold Rueil and Rive Gauche
    # side by side without showing last month as well.
    services = []

    if setlists:
        newest = str(setlists[0].get("sort_date") or "")

        for row in setlists:
            if current_only and str(
                row.get("sort_date") or ""
            ) != newest:
                continue

            songs = []

            for item in row.get("songs", []):
                entry = entry_for(
                    item["slug"],
                    item.get("title", ""),
                    item.get("author", ""),
                    keys,
                )

                if entry:
                    songs.append(entry)

            services.append(
                {
                    "name": row.get("service", ""),
                    "date": row.get("date", ""),
                    "title": row.get("title", ""),
                    "songs": songs,
                }
            )

    # Rueil is where Christian is most weeks, so it opens first
    # whatever order the files happened to be read in. A service
    # with no songs yet sinks below one that has them.
    home = str(config.get("default_service_type") or "")

    services.sort(
        key=lambda s: (
            slugify(s["name"]) != slugify(home),
            not s["songs"],
        )
    )

    # The archive is everything ever extracted, alphabetical. It
    # is built from the cache rather than from the setlists so a
    # song stays reachable even if its setlist is pruned.
    seen = {}

    for path in sorted(CACHE.glob("*.json")):
        slug = path.stem.split("__")[0]

        if slug in seen:
            continue

        entry = entry_for(slug, "", "", keys)

        if entry:
            seen[slug] = entry

    archive = sorted(
        seen.values(),
        key=lambda e: e["title"].lower(),
    )

    return {
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(timespec="seconds"),
        "keys": keys,
        "services": services,
        "archive": archive,
    }


def render(data, out_dir):
    html = TEMPLATE.read_text(encoding="utf-8")

    if PLACEHOLDER not in html:
        raise RuntimeError(
            f"{TEMPLATE} has no {PLACEHOLDER} placeholder."
        )

    # </script> inside the data would close the tag early and
    # break the page; escaping the slash keeps the JSON valid.
    payload = json.dumps(data, ensure_ascii=False).replace(
        "</", "<\\/"
    )

    html = html.replace(PLACEHOLDER, payload)

    out_dir = Path(out_dir).expanduser()

    out_dir.mkdir(parents=True, exist_ok=True)

    target = out_dir / "index.html"

    # Written beside the target and moved into place, so Syncthing
    # never copies a half-written page to the tablet.
    staging = out_dir / ".index.html.tmp"

    staging.write_text(html, encoding="utf-8")

    staging.replace(target)

    return target


def main():
    parser = argparse.ArgumentParser(
        description="Build the worship page."
    )

    parser.add_argument(
        "--output",
        help="Where index.html goes. Defaults to the config.",
    )

    parser.add_argument(
        "--all-setlists",
        action="store_true",
        help="Show every stored setlist, not just the coming "
        "Sunday.",
    )

    args = parser.parse_args()

    config = load_config()

    data = build(config, current_only=not args.all_setlists)

    out = args.output or config.get(
        "output", "~/AI-Workspace/worship/tablet/app"
    )

    target = render(data, out)

    songs = sum(
        len(s["songs"]) for s in data["services"]
    )

    print(
        f"{target}  ·  {songs} canciones del domingo  ·  "
        f"{len(data['archive'])} en el archivo  ·  "
        f"{target.stat().st_size // 1024} KB"
    )

    for service in data["services"]:
        print(
            f"   {service['name']} · {service['date']} · "
            + ", ".join(s["title"] for s in service["songs"])
        )


if __name__ == "__main__":
    main()
