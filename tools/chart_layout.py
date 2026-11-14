#!/usr/bin/env python3

"""
Read a chord chart that has a real text layer by position, not by model.

Charts made on a computer (Christian's "Horizontal-Simplificada"
sheets, ReportLab, Word, PraiseCharts text PDFs) carry every letter
and every chord with its exact place on the page. Sending that text
to a model throws the places away and asks the model to guess them
back; on 14 November 2026 that put the chords of Remembrance at the
ends of phrases instead of on their syllables. Here the places are
kept: a chord belongs to the letter it is printed over (or under),
measured in points, so the result is exact and costs nothing.

How a page is read:

- Text is grouped into rows by height on the page; a page in two
  columns is read left column first, then right.
- A row of chord names (bold, or every word a chord) is a chord row;
  a bold upper-case row is a section label ("VERSE 1", "CHORUS");
  italic text is a performance note ("Repeat Chorus..."), kept as a
  label-only section; a row of bars ("| Am F | C / / / |") is an
  instrumental line.
- Each chord row is paired with its lyric row. Charts put chords
  either ABOVE the words (most) or BELOW them (the simplified sheets:
  every lyric row is followed by its chords, and a section ends on a
  chord row). The side is decided per page from that structure, and
  can be forced with --chords above|below.
- A chord lands on the character whose box contains the chord's left
  edge. Past the end of the words it is a chord played after the last
  word, kept at the end of the line.

Used by song_extract.py before any model call; by hand:

    chart_layout.py FILE.pdf                  # show the result as text
    chart_layout.py FILE.pdf --key C --write  # store it in the cache
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

CACHE = ROOT / "data" / "songs"

CHORD = re.compile(
    r"^\(?[A-G](?:#|b)?"
    r"(?:maj|min|m|M|dim|aug|sus|add|°|ø|\+)?"
    r"[0-9]*(?:(?:sus|add|maj|b|#)[0-9]+)*"
    r"(?:\([^)]*\))?"
    r"(?:/[A-G](?:#|b)?)?\)?$"
)

BAR_TOKEN = re.compile(r"^(\||/|%|x\d+|\d+x|\(x?\d+x?\)|-+|\.+)$", re.I)

LABEL_WORDS = re.compile(
    r"^(INTRO|VERSE|PRE[- ]?CHORUS|CHORUS|BRIDGE|TAG|ENDING|FIRST|FINAL|"
    r"INSTRUMENTAL|INTERLUDE|OUTRO|TURNAROUND|REFRAIN|CODA|VAMP|"
    r"BREAKDOWN|POST[- ]?CHORUS|SPONTANEOUS|HOOK|SOLO)\b",
    re.I,
)


# Chord names the OCR could not settle, for a person to check.
DOUBTS = []


# "[Chorus]", "[Chorus] x2", "[Chorus] (x2)", "[Chorus](kids)", and the
# OCR's "[Chorus |" (closing bracket read as a bar).
LABEL_LINE = re.compile(
    r"^\s*\[\s*([A-Za-z][^\]|]*?)\s*[\]|]\s*(\([^)]*\)|x\d+|\d+x)?[\s.|]*$", re.I)


def label_of(text):
    """The label a bracketed line names, or None."""

    m = LABEL_LINE.match(text)
    return " ".join(x for x in (m.group(1).strip(), m.group(2)) if x) if m else None


def relabel(sections):
    """Lines that are really section labels ("[Chorus] (x2)" read as a
    lyric) become labels; their section splits there. Lines carrying
    chords are never touched."""

    out = []

    for sec in sections:
        current = {"label": sec["label"], "lines": []}
        out.append(current)

        for line in sec["lines"]:
            text = "".join(p.get("text", "") for p in line["parts"])
            name = label_of(text) if not any(p.get("chord") for p in line["parts"]) else None

            if name:
                current = {"label": name, "lines": []}
                out.append(current)
            else:
                current["lines"].append(line)

    return [s for s in out if s["label"] or s["lines"]]


def is_chord(token):
    return bool(CHORD.match(token))


def ocr_chord(token):
    """A chord misread by OCR, put right, or None. Only the slips seen on
    printed charts: a lower-case root (c), a doubled root (Cc), m read as
    n (En), 0 for O never — a chord name it cannot repair stays wrong
    and the row is reviewed by eye."""

    if is_chord(token):
        return token

    token = token.replace("€", "C").replace("¢", "C")

    if token.endswith("?"):
        token = token[:-1] + "7"

    tries = [token, token[:1].upper() + token[1:]]

    if len(token) == 2 and token[0].upper() == token[1].upper():
        tries.append(token[0].upper())

    tries += [t[:1] + t[1:].replace("n", "m", 1) for t in list(tries)]
    tries += [t[:1] + t[1:].replace("in", "m", 1).replace("rn", "m", 1) for t in list(tries)]

    for t in tries:
        if is_chord(t):
            return t

    return None


def spans_of(page):
    """Every text span with its font, size, box and character boxes."""

    raw = page.get_text("rawdict")
    spans = []

    for block in raw.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                chars = span.get("chars") or []
                text = "".join(c["c"] for c in chars)

                if not text.strip():
                    continue

                spans.append(
                    {
                        "font": span.get("font", ""),
                        "size": round(span.get("size", 0), 1),
                        "flags": span.get("flags", 0),
                        "x0": span["bbox"][0],
                        "x1": span["bbox"][2],
                        "y0": span["bbox"][1],
                        "y1": span["bbox"][3],
                        "chars": [
                            (c["c"], c["bbox"][0], c["bbox"][2])
                            for c in chars
                        ],
                    }
                )

    return spans


def bold(span):
    return "bold" in span["font"].lower() or bool(span["flags"] & 16)


def italic(span):
    name = span["font"].lower()
    return "oblique" in name or "italic" in name or bool(span["flags"] & 2)


SUFFIX = re.compile(r"^(sus\d*|add\d+|maj\d*|m\d*|dim\d*|aug|\d+)$")


def merge_suffixes(words):
    """[("A", x), ("sus", x2)] -> [("Asus", x)]: a chord printed with a
    space before its suffix ("A sus") is one chord."""

    out = []

    for word, x in words:
        if out and SUFFIX.match(word) and is_chord(out[-1][0]) and is_chord(out[-1][0] + word):
            out[-1] = (out[-1][0] + word, out[-1][1])
        else:
            out.append((word, x))

    return out


def drop_notes(words):
    """A chord row may end with a note in brackets: "G D C G (G7 into
    verse)". Brackets holding words that are not chords are dropped;
    "(G)" or "(x3)" stay."""

    out, i = [], 0

    while i < len(words):
        word = words[i][0]

        if word.startswith("("):
            j = i
            while j < len(words) and not words[j][0].endswith(")"):
                j += 1
            group = [w for w, _ in words[i:j + 1]]
            inner = " ".join(group).strip("()").split()
            if any(not (is_chord(w) or BAR_TOKEN.match(w)) for w in inner):
                i = j + 1
                continue
        out.append(words[i])
        i += 1

    return out


def tokens(span):
    """Whitespace-separated words of a span, each with its left edge."""

    out, word, x = [], "", None

    for ch, x0, _x1 in span["chars"]:
        if ch.isspace():
            if word:
                out.append((word, x))
            word, x = "", None
        else:
            if not word:
                x = x0
            word += ch

    if word:
        out.append((word, x))

    return out


def rows_of(spans):
    """Spans grouped into rows: same column, overlapping heights."""

    rows = []

    for span in sorted(spans, key=lambda s: (s["col"], s["y0"], s["x0"])):
        mid = (span["y0"] + span["y1"]) / 2

        for row in rows:
            if row["col"] == span["col"] and row["y0"] - 1 <= mid <= row["y1"] + 1:
                row["spans"].append(span)
                row["y0"] = min(row["y0"], span["y0"])
                row["y1"] = max(row["y1"], span["y1"])
                break
        else:
            rows.append(
                {"col": span["col"], "y0": span["y0"], "y1": span["y1"], "spans": [span]}
            )

    for row in rows:
        row["spans"].sort(key=lambda s: s["x0"])

    rows.sort(key=lambda r: (r["col"], r["y0"]))
    return rows


def kind_of(row, lyric_size):
    words = [t for t, _x in merge_suffixes([w for s in row["spans"] for w in tokens(s)])]
    if words and is_chord(words[0]):
        words = [t for t, _x in drop_notes([(w, 0) for w in words])] or words
    text = " ".join(words)

    # "[Verse 1]", the way Ultimate Guitar and ChordPro print labels.
    if label_of(text):
        return "label"

    if re.match(r"^Page \d+\s*/\s*\d+$", text, re.I):
        return "footer"

    # "VERSE 1", "CHORUS 2", "PRE-CHORUS" in capitals, bold or not; and
    # a row that is nothing but a label ("Chorus", "Verse 2:", "Bridge
    # (x2)") in any case.
    if LABEL_WORDS.match(text) and text.upper() == text and len(words) <= 5:
        return "label"
    if re.fullmatch(LABEL_WORDS.pattern[1:].rstrip("\\b") + r"(\s*\d+)?\s*(\(.*\))?\s*:?", text, re.I):
        return "label"

    if all(italic(s) for s in row["spans"]):
        return "note"

    if words and words[0] == "|" or (
        words and all(is_chord(w) or BAR_TOKEN.match(w) for w in words)
        and any(w == "|" for w in words)
    ):
        return "bars"

    # Read from a picture: a row of chord names with an OCR slip or two.
    # The slips are systematic (Bm read as Bin or Bn, C as Cc) and are
    # repaired; a person still checks the names against the page.
    if words and all(s["font"] == "ocr" for s in row["spans"]) and \
            all(ocr_chord(w) or split_chords(w) for w in words) and len(words) <= 10:
        # Two chords the OCR ran together ("GD") become two spans.
        spans = []
        for s in row["spans"]:
            word = "".join(c for c, _a, _b in s["chars"])
            pieces = None if ocr_chord(word) else split_chords(word)
            if not pieces:
                spans.append(s)
                continue
            at = 0
            for piece in pieces:
                part = dict(s, chars=s["chars"][at:at + len(piece)])
                part["x0"], part["x1"] = part["chars"][0][1], part["chars"][-1][2]
                part["box"] = (int(part["x0"]), s["box"][1], int(part["x1"] - part["x0"]), s["box"][3])
                spans.append(part)
                at += len(piece)
            row.setdefault("fixed", []).append("+".join(pieces))
        row["spans"] = spans
        for s in row["spans"]:
            # The page pass at 400 dpi, repaired. Reading each name again
            # enlarged was tried and was worse (Bm became B or A).
            fixed = ocr_chord("".join(c for c, _a, _b in s["chars"]))
            if fixed and fixed != "".join(c for c, _a, _b in s["chars"]):
                x0, x1 = s["chars"][0][1], s["chars"][-1][2]
                step = (x1 - x0) / len(fixed)
                s["chars"] = [(c, x0 + i * step, x0 + (i + 1) * step) for i, c in enumerate(fixed)]
                row.setdefault("fixed", []).append(fixed)
        return "chords"

    if words and all(is_chord(w) for w in words) and (
        all(bold(s) for s in row["spans"]) or len(words) <= 8
    ):
        return "chords"

    if all(bold(s) for s in row["spans"]) and (
        LABEL_WORDS.match(text) or text.upper() == text
    ):
        return "label"

    return "lyric"


def columns_of(spans, lyric_size):
    """Number the page's columns by the empty gutters between them.

    The text of the song body (below the first section label, at lyric
    or chord size) is projected onto the width of the page; wherever no
    text crosses a gap wider than a gutter, a new column starts. Two,
    three or more columns all work this way."""

    def label_text(s):
        return "".join(c for c, _a, _b in s["chars"]).strip()

    starts = [
        s["y0"] for s in spans
        if (bold(s) or label_text(s).startswith("["))
        and LABEL_WORDS.match(label_text(s).lstrip("["))
    ]
    top = min(starts, default=0) - 1

    widths = sorted(
        (s["x1"] - s["x0"]) / len(s["chars"]) for s in spans if len(s["chars"]) >= 3
    )
    char = widths[len(widths) // 2] if widths else 6
    gutter = max(12, 2.5 * char)

    body = sorted(
        (s["x0"], s["x1"])
        for s in spans
        if s["y0"] >= top and lyric_size * 0.75 <= s["size"] <= lyric_size * 1.3
    )

    edges = []
    reach = None

    for x0, x1 in body:
        if reach is None or x0 > reach + gutter:
            edges.append(x0)
            reach = x1
        else:
            reach = max(reach, x1)

    for s in spans:
        s["col"] = max(
            (i for i, e in enumerate(edges) if s["x0"] >= e - 2), default=0
        )


def place(lyric_chars, chords):
    """Parts of one line: each chord at the character under its left edge."""

    text = "".join(c for c, _a, _b in lyric_chars)
    cuts = []

    for name, x in chords:
        index = len(text)

        # x is the centre of the chord's first letter: the letter under
        # it is where the eye puts the chord. (Word charts typed with
        # spaces leave a chord's left edge at the end of the previous
        # word; its centre is already over the next one.)
        for i, (c, a, b) in enumerate(lyric_chars):
            if x < b:
                index = i
                break

        # A chord over the space between two words belongs to the word
        # that follows it.
        while index < len(text) and text[index] == " ":
            index += 1

        # A chord cannot start inside the consonants that open a word:
        # "Gr|eat", "c|ry" are a column printed one or two places late,
        # and the chord belongs on the whole syllable. Past the first
        # vowel ("com|passions", "for-ev|er") the place is kept.
        start = index
        while start > 0 and text[start - 1] not in " -":
            start -= 1
        if start < index and not re.search(r"[aeiouyAEIOUY]", text[start:index]):
            index = start

        cuts.append((index, name))

    cuts.sort(key=lambda c: c[0])
    parts = []
    first = cuts[0][0] if cuts else len(text)

    # Indentation before the words is print layout.
    lead = len(text) - len(text.lstrip(" "))

    if first > 0 and text[:first].strip():
        parts.append({"chord": "", "text": text[lead:first]})

    for n, (index, name) in enumerate(cuts):
        end = cuts[n + 1][0] if n + 1 < len(cuts) else len(text)
        start = max(index, lead) if n == 0 else index
        parts.append({"chord": name, "text": text[start:end]})

    # Trailing spaces after the last word are print layout, not lyric.
    if parts and parts[-1]["text"].endswith(" ") and not parts[-1]["chord"]:
        parts[-1]["text"] = parts[-1]["text"].rstrip()

    return parts


def row_chars(row):
    """The characters of a lyric row with their boxes, spans joined."""

    out = []

    for span in row["spans"]:
        if out and span["chars"]:
            gap_start = out[-1][2]
            first = span["chars"][0][1]

            if first - gap_start > 1.5:
                out.append((" ", gap_start, first))

        out.extend(span["chars"])

    return out


def chord_tokens(span):
    """Chord names of a span, each with the centre of its first letter."""

    out, word, x = [], "", None

    for ch, x0, x1 in span["chars"]:
        if ch.isspace():
            if word:
                out.append((word, x))
            word, x = "", None
        else:
            if not word:
                x = (x0 + x1) / 2
            word += ch

    if word:
        out.append((word, x))

    return out


def row_chords(row):
    return drop_notes(merge_suffixes([(t, x) for s in row["spans"] for t, x in chord_tokens(s)]))


def read_page(page, lyric_side=None, first_page=True, spans=None):
    spans = spans if spans is not None else spans_of(page)

    if sum(len(s["chars"]) for s in spans) < 200:
        return None

    # The lyric size is the commonest size among non-bold text.
    sizes = {}

    for s in spans:
        if not bold(s) and not italic(s):
            sizes[s["size"]] = sizes.get(s["size"], 0) + len(s["chars"])

    lyric_size = max(sizes, key=sizes.get) if sizes else 0

    columns_of(spans, lyric_size)

    # Header (title, author, key) and footer (CCLI) are not song text:
    # everything above the first label and anything much smaller.
    rows = rows_of([s for s in spans if s["size"] >= lyric_size * 0.75])

    for row in rows:
        row["kind"] = kind_of(row, lyric_size)

        if row.get("doubt"):
            DOUBTS.append(
                " ".join(t for s in row["spans"] for t, _ in tokens(s))
                + "  ← " + ", ".join(row["doubt"])
            )

    # The title is bold capitals too; the song starts at the first row
    # that names a section, or failing that, at the first chord row.
    def names_section(r):
        text = " ".join(t for s in r["spans"] for t, _ in tokens(s))
        return r["kind"] == "label" and LABEL_WORDS.match(text.lstrip("["))

    for r in rows:
        if r["kind"] == "label" and not names_section(r) and \
                max(s["size"] for s in r["spans"]) > lyric_size * 1.3:
            r["kind"] = "header"

    starts = [r for r in rows if names_section(r)] or \
        [r for r in rows if r["kind"] in ("chords", "bars")]

    if first_page and starts:
        head = min(starts, key=lambda r: (r["col"], r["y0"]))

        if spans and spans[0]["font"] == "ocr":
            # A printout (Ultimate Guitar) puts a title and chord
            # diagrams across the top; the right column may start above
            # the first label of the left one. The header ends with the
            # last text above that label that reaches into the gutter
            # between the columns; body text never does.
            top = header_bottom(spans, head)
            rows = [
                r for r in rows
                if (r["col"] == head["col"] and r["y0"] >= head["y0"] - 1)
                or (r["col"] > head["col"] and r["y0"] >= top)
            ]
        else:
            # A computer-made sheet: each column starts at its own first
            # label or chord row (the right column of "I Saw The Light"
            # starts higher than the left one's first label); what sits
            # above that in a column is title, author, key, tempo.
            starts = {}
            for r in rows:
                if names_section(r) or r["kind"] in ("chords", "bars"):
                    starts.setdefault(r["col"], r["y0"])
            rows = [r for r in rows if r["col"] in starts and r["y0"] >= starts[r["col"]] - 1]

    rows = [r for r in rows if r["kind"] not in ("header", "footer")]

    side = lyric_side or chord_side(rows)

    sections = []
    current = None
    pending = None  # (lyric row) waiting for its chords when chords are below

    def section(label):
        nonlocal current
        current = {"label": label, "lines": []}
        sections.append(current)

    def flush():
        nonlocal pending
        if pending is not None:
            current["lines"].append({"parts": place(row_chars(pending), [])})
            pending = None

    i = 0

    while i < len(rows):
        row = rows[i]
        kind = row["kind"]

        if kind == "label":
            if current is not None:
                flush()
            label = " ".join(t for s in row["spans"] for t, _ in tokens(s))
            section(label_of(label) or label)

        elif kind == "note":
            if current is not None:
                flush()
            note = " ".join(t for s in row["spans"] for t, _ in tokens(s))
            sections.append({"label": note, "lines": []})
            current = {"label": "", "lines": []}
            sections.append(current)

        elif kind == "bars":
            if current is None:
                section("")
            flush()
            current["lines"].append(
                {
                    "parts": [
                        {"chord": t, "text": ""}
                        for s in row["spans"]
                        for t, _ in tokens(s)
                    ]
                }
            )

        elif kind == "chords":
            if current is None:
                section("")

            if side == "below" and pending is not None:
                current["lines"].append(
                    {"parts": place(row_chars(pending), row_chords(row))}
                )
                pending = None
            elif side == "above" and i + 1 < len(rows) and rows[i + 1]["kind"] == "lyric" \
                    and rows[i + 1]["col"] == row["col"]:
                current["lines"].append(
                    {"parts": place(row_chars(rows[i + 1]), row_chords(row))}
                )
                i += 1
            else:
                flush()
                current["lines"].append(
                    {"parts": [{"chord": t, "text": ""} for t, _ in row_chords(row)]}
                )

        else:  # lyric
            if current is None:
                section("")

            if side == "below":
                flush()
                pending = row
            else:
                current["lines"].append({"parts": place(row_chars(row), [])})

        i += 1

    if current is not None:
        flush()

    # A note row splits a section; drop the empty remainders.
    return relabel([s for s in sections if s["label"] or s["lines"]]), side


def header_bottom(spans, head):
    """Bottom of the lowest text above the first label that crosses the
    gutter between two columns."""

    below = [s for s in spans if s["y0"] >= head["y0"] - 1]
    gutters = []

    for c in sorted({s["col"] for s in below})[:-1]:
        left = max((s["x1"] for s in below if s["col"] == c), default=None)
        right = min((s["x0"] for s in below if s["col"] == c + 1), default=None)

        if left is not None and right is not None and right > left:
            gutters.append((left, right))

    bottom = 0

    for s in spans:
        if s["y0"] >= head["y0"] - 1:
            continue

        if any(s["x1"] > a + 2 and s["x0"] < b - 2 for a, b in gutters):
            bottom = max(bottom, s["y1"])

    return bottom


def chord_side(rows):
    """'below' when lyric rows are followed by their chords, else 'above'."""

    below = above = 0

    for i, row in enumerate(rows):
        if row["kind"] != "label":
            continue

        nxt = rows[i + 1]["kind"] if i + 1 < len(rows) else ""

        if nxt == "lyric":
            below += 1
        elif nxt == "chords":
            above += 1

        # The row just before the next label.
        j = i + 1

        while j < len(rows) and rows[j]["kind"] not in ("label",) and rows[j]["col"] == row["col"]:
            j += 1

        last = rows[j - 1]["kind"] if j - 1 > i else ""

        if last == "chords":
            below += 1
        elif last == "lyric":
            above += 1

    return "below" if below > above else "above"


def split_chords(token):
    """'GD' -> ['G', 'D'], 'GDA' -> ['G', 'D', 'A']: chords run together."""

    for i in range(1, len(token)):
        a = ocr_chord(token[:i])
        if not a:
            continue
        b = ocr_chord(token[i:])
        if b:
            return [a, b]
        rest = split_chords(token[i:])
        if rest:
            return [a] + rest

    return None


def ocr_spans(png):
    """Words of a page image, from tesseract, as spans with letter boxes.

    The charts read this way are printed in a fixed-width font (Ultimate
    Guitar, ChordPro printouts), so each letter takes an equal share of
    its word's box; a chord's place is then exact to the letter."""

    import subprocess

    out = subprocess.run(
        ["tesseract", str(png), "stdout", "--psm", "4", "tsv"],
        capture_output=True, text=True, timeout=300,
    ).stdout

    spans = []

    for line in out.splitlines()[1:]:
        f = line.split("\t")

        if len(f) < 12 or not f[11].strip() or f[10] == "-1":
            continue

        word = f[11].strip()
        x, y, w, h = (int(v) for v in f[6:10])
        step = w / len(word)
        spans.append(
            {
                "font": "ocr", "size": 10.0, "flags": 0,
                "img": str(png), "box": (x, y, w, h),
                "x0": x, "x1": x + w, "y0": y, "y1": y + h,
                "chars": [
                    (c, x + i * step, x + (i + 1) * step)
                    for i, c in enumerate(word)
                ],
            }
        )

    return spans


def read_pdf(pdf, lyric_side=None, ocr=False, dpi=400):
    """{"sections": [...], "side": ...} or None when it cannot be read.

    With ocr, pages are rendered to images and read with tesseract,
    for charts that are pictures of text."""

    import pymupdf
    import tempfile

    doc = pymupdf.open(str(pdf))
    sections, sides = [], []
    DOUBTS.clear()

    with tempfile.TemporaryDirectory() as work:
        for n, page in enumerate(doc):
            spans = None

            if ocr:
                png = Path(work) / f"p{n}.png"
                page.get_pixmap(dpi=dpi).save(str(png))
                spans = ocr_spans(png)

            result = read_page(page, lyric_side, first_page=(n == 0), spans=spans)

            if result is None:
                return None

            page_sections, side = result

            # A page that starts mid-section continues the last one.
            if sections and page_sections and not page_sections[0]["label"]:
                sections[-1]["lines"].extend(page_sections.pop(0)["lines"])

            sections.extend(page_sections)
            sides.append(side)

    if not sections:
        return None

    return {"sections": sections, "side": sides[0] if sides else "", "doubts": list(DOUBTS)}


NOTES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
INDEX = {n: i for i, n in enumerate(NOTES)}
INDEX.update({n: i for i, n in enumerate(SHARP_NAMES)})
INDEX.update({"Db": 1, "Gb": 6, "Cb": 11, "Fb": 4, "E#": 5, "B#": 0})


def transpose_chord(chord, steps, sharps=True):
    """'Em7' up 7 -> 'Bm7'; slash bass moves too ('D/F#' -> 'A/C#')."""

    names = SHARP_NAMES if sharps else NOTES

    def move(match):
        return names[(INDEX[match.group(0)] + steps) % 12]

    return re.sub(r"[A-G](?:#|b)?", move, chord) if is_chord(chord) else chord


def simplify_chord(chord):
    """What Ultimate Guitar's 'simplify' prints: no bass note, no 7."""

    if not is_chord(chord):
        return chord

    chord = chord.split("/")[0]
    m = re.match(r"^([A-G](?:#|b)?)(m?)(.*)$", chord)
    return m.group(1) + m.group(2) if m else chord


def map_chords(sections, fn, drop_repeats=False):
    """Apply fn to every chord. With drop_repeats, a chord equal to the one
    before it on the same line is dropped, its words joining the part
    before (G G/B C simplified prints G C)."""

    out = []

    for sec in sections:
        lines = []

        for line in sec["lines"]:
            parts = []

            for part in line["parts"]:
                chord = fn(part["chord"]) if part["chord"] else ""

                if drop_repeats and chord and parts and parts[-1]["chord"] == chord:
                    parts[-1] = dict(parts[-1], text=parts[-1]["text"] + part["text"])
                    continue

                parts.append({"chord": chord, "text": part["text"]})

            lines.append({"parts": parts})

        out.append({"label": sec["label"], "lines": lines})

    return out


def read_text(text):
    """A chord sheet in plain fixed-width text (Ultimate Guitar, ChordPro
    exports): chord rows over lyric rows, [Section] labels. Columns are
    characters, so every chord lands exactly on its letter."""

    rows = text.replace("\r", "").split("\n")
    sections, current = [], None
    started = False

    def chord_row(raw):
        words = [w for w, _ in drop_notes(merge_suffixes([(w, 0) for w in raw.split()]))]
        return bool(words) and all(is_chord(w) or BAR_TOKEN.match(w) for w in words) \
            and any(is_chord(w) for w in words)

    i = 0

    while i < len(rows):
        raw = rows[i].rstrip()
        i += 1

        if not raw.strip():
            continue

        # Rhythm counts under an intro ("1 & 2 & 3 & 4 &") are not words.
        if re.fullmatch(r"[\s\d&+.]+", raw):
            continue

        name = label_of(raw)

        if name:
            current = {"label": name, "lines": []}
            sections.append(current)
            started = True
            continue

        if not started:
            continue  # title, author, notes above the first label

        if current is None:
            current = {"label": "", "lines": []}
            sections.append(current)

        if chord_row(raw):
            chords = drop_notes(merge_suffixes(
                [(m.group(0), m.start() + 0.5) for m in re.finditer(r"\S+", raw)]))
            nxt = rows[i].rstrip() if i < len(rows) else ""

            # A tab staff ("e|-0--2-|") under a chord row is not its lyric.
            if nxt.strip() and not chord_row(nxt) and not re.match(r"^\s*\[", nxt) \
                    and not re.fullmatch(r"[\s\d&+.]+", nxt) \
                    and not re.match(r"^\s*[eBGDAEbgda]\s?[|:]-|^\s*\|?-{4,}", nxt):
                letters = [(c, k, k + 1) for k, c in enumerate(nxt)]
                current["lines"].append({"parts": place(letters, chords)})
                i += 1
            else:
                current["lines"].append(
                    {"parts": [{"chord": c, "text": ""} for c, _ in chords]}
                )
            continue

        letters = [(c, k, k + 1) for k, c in enumerate(raw)]
        current["lines"].append({"parts": place(letters, [])})

    return relabel([s for s in sections if s["label"] or s["lines"]])


def meta_of(pdf):
    """Capo and time signature printed on the page, when there is a text
    layer; empty otherwise."""

    meta = {"capo": "", "tempo": "", "time_signature": "", "sounds_in": ""}

    try:
        import pymupdf
        text = " ".join(page.get_text() for page in pymupdf.open(str(pdf)))
    except Exception:  # noqa: BLE001
        return meta

    capo = re.search(r"capo[:\s]+(\d+)", text, re.I)
    if capo:
        meta["capo"] = capo.group(1)

    time = re.search(r"(?<![\d/])([2-9]/[248])(?![\d/])", text)
    if time:
        meta["time_signature"] = time.group(1)

    return meta


def as_text(sections):
    """Chords over their syllables, the way a printed chart looks."""

    out = []

    for sec in sections:
        out.append(f"[{sec['label']}]")

        for line in sec["lines"]:
            top, bottom = "", ""

            for part in line["parts"]:
                if part["chord"]:
                    if len(top) > len(bottom):
                        bottom += " " * (len(top) - len(bottom) + 1)
                    top += " " * (len(bottom) - len(top)) + part["chord"]
                bottom += part["text"]

            if top.strip():
                out.append("  " + top.rstrip())
            out.append("  " + bottom.rstrip())

    return "\n".join(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("pdf")
    parser.add_argument("--chords", choices=["above", "below"])
    parser.add_argument("--ocr", action="store_true",
                        help="Read the page as an image (tesseract), for scanned charts.")
    parser.add_argument("--key", help="Key of the chart, for --write.")
    parser.add_argument("--write", action="store_true",
                        help="Replace the cached chart (keeps title, author, meta).")
    args = parser.parse_args()

    pdf = Path(args.pdf).expanduser()
    result = read_pdf(pdf, args.chords, ocr=args.ocr)

    if result is None:
        print("Sin capa de texto: esta carta se lee como imagen.")
        return 1

    print(f"(acordes {'debajo' if result['side'] == 'below' else 'encima'} de la letra)")
    print(as_text(result["sections"]))

    for doubt in result.get("doubts", []):
        print("⚠ acorde dudoso:", doubt)

    if not args.write:
        return 0

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import song_extract

    target = next(
        (p for p in CACHE.glob(f"*__{args.key}.json")
         if json.loads(p.read_text(encoding="utf-8")).get("source_file") == pdf.name),
        None,
    )

    if target is None:
        print(f"No hay carta en caché de {pdf.name} en {args.key}.")
        return 1

    song = json.loads(target.read_text(encoding="utf-8"))
    song.update(
        {
            "sections": result["sections"],
            "source_hash": song_extract.file_hash(pdf),
            "method": "ocr-layout" if args.ocr else "layout",
            "model": "",
            "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    )
    target.write_text(json.dumps(song, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"→ {target.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
