#!/usr/bin/env python3

"""
Compare a chart with other evidence of the same song, word by word.

The evidence can be:
- another reading of the same PDF (OCR: --ocr), or
- the chart of the same song in another key (--with slug__KEY), moved
  into this chart's key first: an arrangement printed in G and in D
  must have the same chords on the same words, a fifth apart.

Differences come out as in chart_review.py: line, kind, what the chart
has, what the evidence says. Agreement of two independent readings is
strong evidence; each difference is then settled by looking at the PDF.

    chart_compare.py slug__key --ocr
    chart_compare.py slug__key --with slug__G
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import chart_layout as cl  # noqa: E402
import chart_review as cr  # noqa: E402

CACHE = ROOT / "data" / "songs"
LIBRARY = Path("~/AI-Workspace/music/EICMusic").expanduser()


def as_evidence(sections):
    text, chords = cr.plain_lines(sections)
    line_text = {r.split(": ", 1)[0]: r.split(": ", 1)[1]
                 for r in text.split("\n") if re.match(r"^L\d+: ", r)}
    return {"reading": {k: [list(x) for x in v] for k, v in chords.items()}, "line_text": line_text}


def interval(a_sections, b_sections):
    """Semitones that move b's chords onto a's (most common root step)."""

    roots = lambda s: [cl.INDEX.get(re.match(r"[A-G](#|b)?", c).group(0))
                       for c in (p["chord"] for sec in s for l in sec["lines"] for p in l["parts"])
                       if c and re.match(r"[A-G]", c)]
    pairs = Counter((x - y) % 12 for x, y in zip(roots(a_sections), roots(b_sections)))
    return pairs.most_common(1)[0][0] if pairs else 0


def differences(song, evidence_sections):
    fake = {"sections": song["sections"], "review": as_evidence(evidence_sections)}
    return cr.recompare(fake)["issues"]


def stream(sections):
    """The song as one stream of words, each with the chords played on it,
    plus the chord-only lines in order. Line wrapping does not matter."""

    words, bars = [], []

    for sec in sections:
        for line in sec["lines"]:
            parts = line["parts"]
            text = "".join(p.get("text", "") for p in parts)

            if not text.strip():
                bars.append([p["chord"] for p in parts if p.get("chord") and p["chord"] not in ("|", "/")])
                continue

            spans, at = [], 0
            for m in re.finditer(r"\S+", text):
                spans.append([m.start(), m.end(), re.sub(r"[^a-z0-9]", "", m.group(0).lower()), []])
            for part in parts:
                if part.get("chord") and spans:
                    target = next((w for w in spans if w[0] <= at < w[1]), None)
                    if target is None:
                        target = next((w for w in spans if w[0] >= at), spans[-1])
                    target[3].append(part["chord"].strip("()"))
                at += len(part.get("text", ""))
            words.extend((w[2], w[3], w[2]) for w in spans if w[2])

    return words, bars


def stream_differences(a_sections, b_sections):
    """Chord-on-word differences between two readings of one song."""

    import difflib

    a, abars = stream(a_sections)
    b, bbars = stream(b_sections)
    sm = difflib.SequenceMatcher(a=[w for w, _c, _t in a], b=[w for w, _c, _t in b], autojunk=False)
    out = []

    def ctx(seq, i):
        return " ".join(t for t, _c, _x in seq[max(0, i - 2):i + 3])

    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            for i, j in zip(range(i1, i2), range(j1, j2)):
                if a[i][1] != b[j][1]:
                    out.append(("acorde", ctx(a, i), a[i][1], b[j][1]))
        else:
            ca = [c for _w, cs, _t in a[i1:i2] for c in cs]
            cb = [c for _w, cs, _t in b[j1:j2] for c in cs]
            if ca != cb:
                out.append(("letra", ctx(a, i1) + " / " + ctx(b, j1), ca, cb))

    bm = difflib.SequenceMatcher(a=[" ".join(x) for x in abars], b=[" ".join(x) for x in bbars], autojunk=False)
    for op, i1, i2, j1, j2 in bm.get_opcodes():
        if op != "equal":
            out.append(("instrumental", "", abars[i1:i2], bbars[j1:j2]))

    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("chart")
    parser.add_argument("--ocr", action="store_true")
    parser.add_argument("--with", dest="other")
    args = parser.parse_args()

    song = json.loads((CACHE / f"{args.chart}.json").read_text(encoding="utf-8"))

    if args.ocr:
        evidence = cl.read_pdf(LIBRARY / song["source_file"], ocr=True)["sections"]
    else:
        other = json.loads((CACHE / f"{args.other}.json").read_text(encoding="utf-8"))
        step = interval(song["sections"], other["sections"])
        sharps = song.get("key") not in ("C", "F", "Bb", "Eb", "Ab")
        evidence = cl.map_chords(other["sections"], lambda c: cl.transpose_chord(c, step, sharps))
        print(f"(transpuesta {step} semitonos)")

    issues = stream_differences(song["sections"], evidence)
    print(f"== {args.chart}: {len(issues)} diferencias")
    for kind, where, mine, theirs in issues:
        print(f"   {kind:12} «{where}»  carta: {mine}  evidencia: {theirs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
