#!/usr/bin/env python3

"""
The final check of a chart against its PDF, by a strong vision model.

Christian's rule (14 November 2026): every chart, before it reaches the
tablet, is compared with the printed original — every chord present,
with its exact name, over the same word and syllable — no matter what
it costs in time or tokens. Reading by position (chart_layout.py) gets
the places right by construction; this is the independent second pair
of eyes that catches what a reader cannot know it got wrong: an OCR
slip (a bold C read as G, F as E), a chord the reader missed, a line
paired with the wrong chords.

The model sees the page images and the chart as the app will hold it,
written as plain text with each chord over its letter, lines numbered.
It answers in JSON: ok, and a list of differences. It does not repair
anything: a chart with differences is marked "needs-review" and a
person (or Eden, following its skill) fixes it from the PDF.

    chart_review.py data/songs/<slug>__<key>.json     # review one chart
    chart_review.py --all-unverified                  # every chart not yet verified
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "tools"))

import song_extract  # noqa: E402

DEFAULT_MODEL = "anthropic/claude-opus-5.5"

PROMPT = """You are reading a worship chord chart (the page images) for a
musician who will play from it on Sunday. A wrong or missing chord is a
real fault, so read carefully.

Below are the lyric lines of this chart, numbered L1, L2, ... (lines with
only chords, such as intros and instrumentals, are numbered too and shown
as "(chords only)"). The original may wrap a long line onto two printed
lines; the numbered line may be the joined text.

For EVERY numbered line, list in order every chord the ORIGINAL prints for
that line, each with the word it is printed over:

- Use the chord name exactly as printed: G, G/B, Am7, Dsus4, F#m.
- Decide first, for the whole sheet, whether chords are printed ABOVE or
  UNDER the words, from its structure: when every section label is
  followed by a lyric line and every section ends with a row of chords,
  the chords are UNDER the words and each chord row belongs to the lyric
  line just ABOVE it (Christian's "Simplified guitar" sheets work this
  way). Apply that one rule to every line of the sheet.
- If a chord sits inside a word, give the whole word ("remember" for a
  chord over "mem").
- If a chord comes after the last word of the line, use "(end)".
- For a chords-only line, use "" as the word; keep bar lines out.
- A line with no chords gets an empty list.

Then list, word for word, every lyric line printed in the original that is
NOT among the numbered lines (a missing verse, chorus or line), under
"missing_lines". Ignore titles, credits, notes and page numbers.

Answer with ONLY this JSON, no prose:
{"lines": {"L1": [["C", "how"], ["C", "(end)"]], "L2": [], ...},
 "missing_lines": ["a lyric line the numbered lines lack", ...]}"""


def plain_lines(sections):
    """Numbered lyric lines (no chords) and, for each, the chords the
    chart holds: [(chord, word)], word as the reviewer is asked for it."""

    import re

    text_lines, ours, n = [], {}, 0

    for sec in sections:
        text_lines.append(f"[{sec.get('label', '')}]")

        for line in sec.get("lines", []):
            n += 1
            tag = f"L{n}"
            parts = line.get("parts", [])
            text = "".join(p.get("text", "") for p in parts)
            text_lines.append(f"{tag}: " + (text.strip() or "(chords only)"))
            chords, at = [], 0

            for part in parts:
                if part.get("chord") and part["chord"] not in ("|", "/", "%"):
                    if not text.strip():
                        word = ""
                    elif at >= len(text.rstrip()):
                        word = "(end)"
                    else:
                        a = at
                        while a > 0 and not text[a - 1].isspace():
                            a -= 1
                        b = at
                        while b < len(text) and not text[b].isspace():
                            b += 1
                        # "remem - ber": a word the chart splits with a
                        # spaced hyphen is still one word.
                        while text[b:b + 3] == " - ":
                            b += 3
                            while b < len(text) and not text[b].isspace():
                                b += 1
                        while a >= 3 and text[a - 3:a] == " - ":
                            a -= 3
                            while a > 0 and not text[a - 1].isspace():
                                a -= 1
                        word = text[a:b]
                    chords.append((part["chord"], word))
                at += len(part.get("text", ""))

            ours[tag] = chords

    return "\n".join(text_lines), ours


def same_word(a, b):
    """Same word, allowing for a hyphen-split word ("remem - ber"):
    either piece, or the whole, names the same word."""

    import re
    norm = lambda w: re.sub(r"[^a-z0-9()]", "", str(w).lower())
    ends = ("", "end", "(end)")

    if norm(a) == norm(b) or (norm(a) in ends and norm(b) in ends):
        return True

    pieces = lambda w: {norm(x) for x in re.split(r"\s*-\s*", str(w)) if norm(x)}
    return norm(b) in pieces(a) or norm(a) in pieces(b)


def compare(ours, theirs):
    """Differences between the chart and the reviewer's independent reading."""

    import difflib

    issues = []

    for tag, mine in ours.items():
        read = [tuple(x[:2]) if isinstance(x, (list, tuple)) and len(x) >= 2 else (str(x), "")
                for x in (theirs.get(tag) or [])]
        read = [(str(c).strip(), str(w).strip()) for c, w in read if str(c).strip() not in ("|", "/")]
        # "(C)" printed in brackets is the same chord as C.
        bare = lambda c: c.strip("()")
        sm = difflib.SequenceMatcher(a=[bare(c) for c, _ in mine], b=[bare(c) for c, _ in read], autojunk=False)

        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op == "equal":
                for (c, w), (_c, w2) in zip(mine[i1:i2], read[j1:j2]):
                    if not same_word(w, w2):
                        issues.append({"line": tag, "kind": "wrong_place",
                                       "found": f"{c} sobre «{w}»", "expected": f"{c} sobre «{w2}»"})
            elif op == "replace" and (i2 - i1) == (j2 - j1):
                for (c, w), (c2, w2) in zip(mine[i1:i2], read[j1:j2]):
                    issues.append({"line": tag, "kind": "wrong_name",
                                   "found": f"{c} sobre «{w}»", "expected": f"{c2} sobre «{w2}»"})
            else:
                if i2 > i1:
                    issues.append({"line": tag, "kind": "extra",
                                   "found": " ".join(f"{c}@{w}" for c, w in mine[i1:i2]), "expected": ""})
                if j2 > j1:
                    issues.append({"line": tag, "kind": "missing",
                                   "found": "", "expected": " ".join(f"{c}@{w}" for c, w in read[j1:j2])})

    return issues


def review(pdf, song, model=DEFAULT_MODEL, think=False):
    """{"ok", "issues", "model", "at"}: the chart against an independent
    reading of the PDF by the model, compared here word by word."""

    images = song_extract.page_images(pdf, dpi=170, max_pages=3)

    if not images:
        raise RuntimeError(f"{Path(pdf).name}: could not render the pages")

    text, ours = plain_lines(song.get("sections", []))
    line_text = {
        row.split(": ", 1)[0]: row.split(": ", 1)[1]
        for row in text.split("\n") if re.match(r"^L\d+: ", row)
    }
    content = [{"type": "text", "text": PROMPT + "\n\nLINES (key "
                + str(song.get("key", "")) + "):\n\n" + text}]

    for blob in images:
        content.append({"type": "image_url", "image_url": {
            "url": "data:image/png;base64," + base64.b64encode(blob).decode()}})

    reply = song_extract.call_model(
        [{"role": "user", "content": content}], model, max_tokens=16000, think=think
    )
    found = song_extract.parse_json(reply)
    theirs = found.get("lines") or {}
    issues = compare(ours, theirs)
    # Lines the chart lacks altogether: the gap the per-line reading
    # cannot see (14 Nov 2026: a whole verse was missing from a chart
    # that passed).
    for missing in found.get("missing_lines") or []:
        if str(missing).strip():
            issues.append({"line": "-", "kind": "structure", "found": "",
                           "expected": f"falta la línea «{str(missing).strip()[:80]}»"})

    return {
        "ok": not issues,
        "issues": issues,
        "reading": theirs,
        "line_text": line_text,
        "source_hash": song.get("source_hash", ""),
        "model": model,
        "cost": round(song_extract.LAST_COST, 4),
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def recompare(song):
    """Issues again from the stored reading (no model call), for when the
    comparison rules change."""

    old = song.get("review") or {}

    if "reading" not in old:
        return old

    text, ours = plain_lines(song.get("sections", []))
    reading = old["reading"]

    # The reading is keyed by line number at the time it was made. If
    # lines were added or removed since, match them up by their words.
    if old.get("line_text"):
        import difflib
        now = [(row.split(": ", 1)[0], row.split(": ", 1)[1])
               for row in text.split("\n") if re.match(r"^L\d+: ", row)]
        then = list(old["line_text"].items())
        norm = lambda t: re.sub(r"[^a-z0-9]", "", t.lower())
        sm = difflib.SequenceMatcher(a=[norm(t) for _, t in then],
                                     b=[norm(t) for _, t in now], autojunk=False)
        moved = {}
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op == "equal":
                for i, j in zip(range(i1, i2), range(j1, j2)):
                    moved[now[j][0]] = reading.get(then[i][0], [])
        reading = moved

    issues = compare(ours, reading)
    return dict(old, ok=not issues, issues=issues)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("charts", nargs="*", help="Chart JSON files.")
    parser.add_argument("--model", default=None)
    parser.add_argument("--save", action="store_true",
                        help="Store the result in the chart (review field).")
    args = parser.parse_args()

    config = song_extract.load_config()
    model = args.model or config.get("model_review", DEFAULT_MODEL)
    library = Path(config.get("library", "~/AI-Workspace/music/EICMusic")).expanduser()

    for path in args.charts:
        path = Path(path)
        song = json.loads(path.read_text(encoding="utf-8"))
        result = review(library / song["source_file"], song, model)
        print(f"== {path.stem}: {'OK' if result['ok'] else str(len(result['issues'])) + ' diferencias'}")

        for issue in result["issues"]:
            print(f"   {issue.get('line', '?'):5} {issue.get('kind', ''):12} "
                  f"carta: {issue.get('found', '')!s:34} PDF: {issue.get('expected', '')}")

        if args.save:
            song["review"] = result
            path.write_text(json.dumps(song, ensure_ascii=False, indent=2), encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
