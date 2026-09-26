#!/usr/bin/env python3

"""
The Saturday job: Planning Center in, tablet page out.

  1. Read the coming plan for each service type.
  2. Resolve every song on it against the library, once per key.
  3. Extract the ones not already in the cache. Only new songs
     cost anything; a song played before is free forever.
  4. Store the setlist and rebuild the page.

Planning Center lists a song twice when the set pauses it for a
reading and resumes it, so songs are collapsed by title the same
way the printed sheets are.

Re-running is safe and cheap: nothing already extracted is sent to
a model again.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from html import unescape
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "tools"))

# The Planning Center client and the library index already exist
# for the printed sheets. Reusing them keeps one definition of
# which file wins for a given song and key.
WORSHIP_TOOLS = (
    Path.home() / "AI-Workspace" / "worship" / "tools"
)

sys.path.insert(0, str(WORSHIP_TOOLS))

import pco_client as pco          # noqa: E402
import song_library as sl         # noqa: E402

import build_site                 # noqa: E402
import song_extract               # noqa: E402


HERMES = Path.home() / ".local" / "bin" / "hermes"

SETLISTS = ROOT / "data" / "setlists"


def notify(text, target="telegram"):
    """Tell Christian. Never fatal, never silent."""

    if not HERMES.exists():
        print(text)
        return

    try:
        subprocess.run(
            [str(HERMES), "send", "--to", target, "--quiet", text],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print("No se pudo avisar:", exc)
        print(text)


def plan_for(service_name):
    """The coming plan for one service type, by name."""

    wanted = sl.normalise(service_name)

    for item in pco.service_types():
        if sl.normalise(item["name"]) != wanted:
            continue

        plan = pco.next_plan(item["id"])

        if not plan:
            return item, None, []

        return (
            item,
            plan,
            pco.plan_items(item["id"], plan["id"]),
        )

    return None, None, []


def clean_html(value):
    """Plain text out of Planning Center rich text."""

    text = re.sub(
        r"<br\s*/?>|</p>|</div>",
        "\n",
        str(value or ""),
        flags=re.I,
    )

    text = re.sub(r"<[^>]+>", "", text)

    text = unescape(text)

    text = re.sub(r"[ \t]+", " ", text)

    return "\n".join(
        line.strip() for line in text.splitlines()
    ).strip()


def order_of_service(items):
    """The liturgy as Planning Center holds it.

    Kept whole — headers, readings, announcements, the lot — because
    the detail Christian writes under an item is the reason the
    sheet exists. The app shows the same thing the printed sheet
    does, so he is never reading two different orders.
    """

    rows = []

    for row in items:
        detail = clean_html(row.get("description"))

        rows.append(
            {
                "seq": row.get("sequence"),
                "type": row.get("type") or "item",
                "title": str(row.get("title") or "").strip(),
                "key": row.get("key") or "",
                "length": row.get("length") or 0,
                "detail": detail,
            }
        )

    return rows


def songs_on(items):
    """Unique song titles, in plan order.

    A song listed twice is the same song paused for a reading and
    resumed, so it becomes one card, not two.
    """

    out = []
    seen = set()

    for row in items:
        if row.get("type") != "song":
            continue

        title = str(row.get("title") or "").strip()

        token = sl.normalise(title)

        if not title or token in seen:
            continue

        seen.add(token)
        out.append(title)

    return out


def gather(title, index, keys, config, force=False):
    """Extract one song in every key the library has it in."""

    candidates = index.get(sl.normalise(title)) or []

    if not candidates:
        return {"title": title, "status": "missing", "keys": {}}

    author = candidates[0].author

    real_title = candidates[0].title

    got = {}
    failed = {}

    for key in keys:
        pick = sl.choose(
            [s for s in candidates if s.key == key],
            config.get("allow_french", False),
        )

        if not pick:
            continue

        try:
            song, how = song_extract.extract(
                pick.path,
                key,
                real_title,
                author,
                config,
                force=force,
            )

            got[key] = how
        except Exception as exc:  # noqa: BLE001
            # One unreadable chart must not cost the whole set.
            failed[key] = str(exc)[:200]

    return {
        "title": real_title,
        "author": author,
        "slug": re.sub(r"[^a-z0-9]", "", real_title.lower()),
        "status": "ok" if got else "failed",
        "keys": got,
        "failed": failed,
        "available": sorted({s.key for s in candidates}),
    }


def run(config, force=False, only=None):
    keys = config.get("keys", ["G", "D", "C"])

    songs, _ = sl.load(
        Path(config.get("library", sl.LIBRARY)).expanduser()
    )

    index = sl.index_by_title(songs)

    names = only or config.get(
        "service_types", [config.get("default_service_type")]
    )

    SETLISTS.mkdir(parents=True, exist_ok=True)

    report = []
    problems = []
    spent = 0

    for name in names:
        service, plan, items = plan_for(name)

        if not service:
            problems.append(f"{name}: no existe en Planning Center")
            continue

        label = service["name"].strip()

        if not plan:
            report.append(f"{label}: sin plan próximo")
            continue

        titles = songs_on(items)

        entries = []

        for title in titles:
            result = gather(
                title, index, keys, config, force=force
            )

            if result["status"] == "missing":
                problems.append(
                    f"{label}: «{title}» no está en la biblioteca"
                )
                continue

            for key, how in result["keys"].items():
                if how != "cached":
                    spent += 1

            for key, why in result.get("failed", {}).items():
                problems.append(
                    f"{label}: «{result['title']}» en {key} "
                    f"no se pudo leer ({why})"
                )

            if not result["keys"]:
                continue

            entries.append(
                {
                    "slug": result["slug"],
                    "title": result["title"],
                    "author": result["author"],
                }
            )

            gaps = [
                k for k in keys if k not in result["keys"]
            ]

            if gaps:
                problems.append(
                    f"{label}: «{result['title']}» no está en "
                    + "/".join(gaps)
                )

        stamp = re.sub(
            r"[^a-z0-9]+",
            "-",
            (str(plan.get("sort_date") or "")[:10]).lower(),
        ).strip("-") or "sin-fecha"

        path = SETLISTS / (
            f"{stamp}-{sl.normalise(label)}.json"
        )

        path.write_text(
            json.dumps(
                {
                    "service": label,
                    "date": plan.get("dates", ""),
                    "sort_date": str(
                        plan.get("sort_date") or ""
                    )[:10],
                    "title": plan.get("title") or "",
                    "order": order_of_service(items),
                    "songs": entries,
                    "built_at": datetime.now(
                        timezone.utc
                    ).isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        report.append(
            f"{label} · {plan.get('dates')} · "
            f"{len(entries)} canciones"
        )

    data = build_site.build(config)

    target = build_site.render(
        data,
        config.get(
            "output", "~/AI-Workspace/worship/tablet/app"
        ),
    )

    return {
        "report": report,
        "problems": problems,
        "extracted": spent,
        "target": target,
        "archive": len(data["archive"]),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Build the worship app for the coming Sunday."
    )

    parser.add_argument(
        "--notify",
        action="store_true",
        help="Send the summary to Telegram.",
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-read every chart even if it is cached. Costs "
        "money; only for when an extraction went wrong.",
    )

    parser.add_argument(
        "--service",
        action="append",
        help="Only this service type. Repeatable.",
    )

    args = parser.parse_args()

    config = build_site.load_config()

    try:
        outcome = run(
            config, force=args.force, only=args.service
        )
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()

        if args.notify:
            notify(
                "⚠️ La app de worship no se pudo construir: "
                f"{exc}"
            )

        return 1

    lines = ["🎸 Canciones del domingo en la tableta", ""]

    lines += outcome["report"]

    if outcome["extracted"]:
        lines.append(
            f"\n{outcome['extracted']} carta(s) nueva(s) leída(s)."
        )

    lines.append(f"Archivo: {outcome['archive']} canciones.")

    if outcome["problems"]:
        lines.append("")
        lines += ["⚠️ " + p for p in outcome["problems"]]

    text = "\n".join(lines)

    print()
    print(text)
    print()
    print(outcome["target"])

    if args.notify:
        notify(text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
