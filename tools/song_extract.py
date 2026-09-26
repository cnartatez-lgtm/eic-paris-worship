#!/usr/bin/env python3

"""
One chord chart PDF becomes one structured song file.

Half of Christian's library carries a real text layer and half is
image only, so there are two routes to the same JSON:

  text    pdftotext -layout, then a model untangles it. The charts
          are laid out in two columns, so the raw text interleaves
          Verse 1 with Verse 2 line by line; a model reads that
          back into order far more reliably than column-guessing
          heuristics do.

  vision  the page is rendered to PNG and read by a vision model.

The model's job is perception only — never HTML. It returns chords
positioned inside the lyric, ChordPro style, and the renderer turns
that into a page. Keeping generation deterministic is what makes
every song look the same and what makes a re-run free.

Extraction is paid for once. Results are cached by source file and
its hash, so a song already read is never sent to a model again,
and the cache is committed to the repo.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

CONFIG = ROOT / "config" / "app.json"

CACHE = ROOT / "data" / "songs"

ENV_FILE = Path.home() / ".hermes" / ".env"

OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"

TIMEOUT = 180

# Below this, a "text layer" is really just a stray label or a
# page number and the page has to be read as an image.
MIN_TEXT_CHARS = 200

SCHEMA = """{
  "title": "Song title in normal title case",
  "author": "Author or band, empty string if not shown",
  "meta": {"capo": "", "tempo": "", "time_signature": "", "sounds_in": ""},
  "sections": [
    {
      "label": "Verse 1",
      "lines": [
        {"parts": [{"chord": "D", "text": "Lift high the name of "},
                   {"chord": "A7", "text": "Jesus"}]}
      ]
    }
  ]
}"""

PROMPT = """You are transcribing a worship chord chart into JSON.

Return ONLY a JSON object in exactly this shape, no prose, no code
fence:

""" + SCHEMA + """

Rules:

1. Every chord sits inside the lyric line at the syllable it is
   played on, as a "part" whose "chord" is that chord and whose
   "text" is the lyric from that chord up to the next one.
2. Lyric text before the first chord of a line goes in a part with
   "chord": "" — never drop it.
3. An instrumental line with chords and no words is a line whose
   parts have "text": "".
4. SPACING IS EXACT. Joining every "text" of a line in order must
   reproduce that lyric line character for character, spaces
   included. Splitting "the Father praise" at a chord gives
   "the Father " + "praise", never "the Father" + "praise".
5. SPREAD THE CHORDS ACROSS THE WORDS. When a chord line holds
   several chords above one lyric line, each one belongs at the
   word underneath it — line the columns up and split the lyric
   there. Never emit chords as a run of parts with empty text
   followed by the whole lyric in one part. If a lyric line has
   four chords over it, that line has about four parts with words
   in them.

   Wrong:  [{"chord":"G","text":""},{"chord":"Em","text":""},
            {"chord":"A","text":"And we cried out to Him"}]
   Right:  [{"chord":"G","text":"And we "},
            {"chord":"Em","text":"cried out "},
            {"chord":"A","text":"to Him"}]
6. Keep the chords EXACTLY as printed: Asus4, G/B, Em7, A7. Never
   simplify, rename, or transpose them. A wrong chord is worse
   than a missing one.
7. Keep the section labels as printed: Verse 1, Chorus, Bridge,
   Pre-Chorus, Tag, Instrumental, Interlude.
8. Reproduce the lyrics as printed, including hyphens that split a
   word across chords (Re-member, glo-ry).
9. THE PAGE IS USUALLY TWO COLUMNS. Read the WHOLE left column
   top to bottom first, then the whole right column. Do not
   interleave them. Extracted text may already be interleaved
   line by line — put it back in reading order.
10. Ignore chord-shape diagrams, fingering charts, playing guides,
   copyright lines, and CCLI numbers. Lyrics, chords and section
   labels only.
11. If the chart says Capo, tempo, time signature or what key it
   sounds in, put those in meta. Otherwise leave them "".

Repeated sections that only say "Repeat chorus" should be kept as
a section with that label and no lines."""


def load_config():
    data = {}

    if CONFIG.exists():
        data = json.loads(CONFIG.read_text(encoding="utf-8"))

    return data


def api_key():
    """Read the OpenRouter key from the environment or Hermes.

    Never printed, never logged, never written to the cache.
    """

    key = os.environ.get("OPENROUTER_API_KEY", "").strip()

    if key:
        return key

    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(
            encoding="utf-8", errors="ignore"
        ).splitlines():
            if line.strip().startswith("#"):
                continue

            name, _, value = line.partition("=")

            if name.strip() == "OPENROUTER_API_KEY":
                value = value.strip().strip("'\"")

                if value:
                    return value

    raise RuntimeError(
        "No OPENROUTER_API_KEY in the environment or "
        f"{ENV_FILE}. Extraction needs it; the site build "
        "does not."
    )


def file_hash(path):
    return hashlib.sha256(
        Path(path).read_bytes()
    ).hexdigest()[:16]


def page_text(pdf):
    """The text layer, laid out, or '' when there is none."""

    try:
        out = subprocess.run(
            ["pdftotext", "-layout", str(pdf), "-"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return ""

    return out.stdout if out.returncode == 0 else ""


def page_images(pdf, dpi=150, max_pages=3):
    """Render the pages to PNG bytes for the vision route."""

    import tempfile

    images = []

    with tempfile.TemporaryDirectory() as work:
        stem = Path(work) / "pg"

        subprocess.run(
            [
                "pdftoppm",
                "-png",
                "-r",
                str(dpi),
                "-l",
                str(max_pages),
                str(pdf),
                str(stem),
            ],
            capture_output=True,
            timeout=180,
        )

        for path in sorted(Path(work).glob("pg*.png")):
            images.append(path.read_bytes())

    return images


def call_model(messages, model, max_tokens=8000):
    """One OpenRouter chat call returning the text content.

    Reading a chart is perception, not deliberation, so thinking
    budget is switched off where that is allowed: it buys nothing
    here and on some models it swallows the whole reply. Several
    newer models refuse to have it disabled, so a 400 saying so
    is retried once with the field dropped rather than failing
    the song.
    """

    payload_base = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0,
    }

    attempts = [
        dict(payload_base, reasoning={"enabled": False}),
        payload_base,
    ]

    last = None

    for index, attempt in enumerate(attempts):
        request = urllib.request.Request(
            OPENROUTER,
            data=json.dumps(attempt).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key()}",
                "Content-Type": "application/json",
                "X-Title": "EIC Paris Worship",
            },
        )

        try:
            with urllib.request.urlopen(
                request, timeout=TIMEOUT
            ) as response:
                payload = json.loads(response.read())

            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "ignore")[:300]

            last = RuntimeError(
                f"OpenRouter returned {exc.code}: {detail}"
            )

            retryable = (
                exc.code == 400
                and "reasoning" in detail.lower()
                and index == 0
            )

            if not retryable:
                raise last
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Could not reach OpenRouter: {exc}"
            )
    else:
        raise last

    choices = payload.get("choices") or []

    if not choices:
        raise RuntimeError(
            "OpenRouter returned no choices: "
            + json.dumps(payload)[:300]
        )

    return choices[0].get("message", {}).get("content", "")


def parse_json(text):
    """Pull the JSON object out of a model reply."""

    text = (text or "").strip()

    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end <= start:
        raise RuntimeError(
            "The model did not return JSON: " + text[:200]
        )

    return json.loads(text[start : end + 1])


def clean(song, fallback_title, fallback_author, key):
    """Normalise the model's object into the stored shape."""

    meta = song.get("meta") or {}

    sections = []

    for raw in song.get("sections") or []:
        lines = []

        for line in raw.get("lines") or []:
            parts = []

            for part in line.get("parts") or []:
                chord = str(part.get("chord") or "").strip()
                text = str(part.get("text") or "")

                if not chord and not text.strip():
                    continue

                parts.append({"chord": chord, "text": text})

            if parts:
                lines.append({"parts": parts})

        label = str(raw.get("label") or "").strip()

        # A label with no lines is meaningful ("Repeat chorus"),
        # but a nameless empty section is noise.
        if lines or label:
            sections.append({"label": label, "lines": lines})

    return {
        "title": str(
            song.get("title") or fallback_title
        ).strip()
        or fallback_title,
        "author": str(
            song.get("author") or fallback_author
        ).strip(),
        "key": key,
        "meta": {
            "capo": str(meta.get("capo") or "").strip(),
            "tempo": str(meta.get("tempo") or "").strip(),
            "time_signature": str(
                meta.get("time_signature") or ""
            ).strip(),
            "sounds_in": str(
                meta.get("sounds_in") or ""
            ).strip(),
        },
        "sections": sections,
    }


def extract(pdf, key, title, author, config, force=False):
    """Read one chart into structured JSON, using the cache."""

    pdf = Path(pdf)

    CACHE.mkdir(parents=True, exist_ok=True)

    slug = re.sub(r"[^a-z0-9]", "", title.lower())

    target = CACHE / f"{slug}__{key}.json"

    digest = file_hash(pdf)

    if target.exists() and not force:
        try:
            cached = json.loads(
                target.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            cached = None

        # Same source file, same content: nothing to pay for.
        if cached and cached.get("source_hash") == digest:
            return cached, "cached"

    text = page_text(pdf)

    model_text = config.get(
        "model_text", "google/gemini-3.1-flash-lite"
    )

    model_vision = config.get(
        "model_vision", "google/gemini-3.1-flash-lite"
    )

    if len(text.strip()) >= MIN_TEXT_CHARS:
        method = "text"
        model = model_text

        messages = [
            {
                "role": "user",
                "content": (
                    PROMPT
                    + "\n\nThis is the extracted text of the "
                    f"chart, in key {key}. The columns may be "
                    "interleaved:\n\n"
                    + text[:20000]
                ),
            }
        ]
    else:
        method = "vision"
        model = model_vision

        images = page_images(pdf)

        if not images:
            raise RuntimeError(
                f"{pdf.name} has no text layer and could not be "
                "rendered to an image. Is poppler-utils "
                "installed?"
            )

        content = [
            {
                "type": "text",
                "text": (
                    PROMPT
                    + f"\n\nRead this chord chart in key {key}."
                ),
            }
        ]

        for blob in images:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64,"
                        + base64.b64encode(blob).decode()
                    },
                }
            )

        messages = [{"role": "user", "content": content}]

    reply = call_model(messages, model)

    song = clean(
        parse_json(reply), title, author, key
    )

    if not song["sections"]:
        raise RuntimeError(
            f"{pdf.name}: the model returned no sections."
        )

    song.update(
        {
            "slug": slug,
            "source_file": pdf.name,
            "source_hash": digest,
            "method": method,
            "model": model,
            "extracted_at": datetime.now(
                timezone.utc
            ).isoformat(timespec="seconds"),
        }
    )

    target.write_text(
        json.dumps(song, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return song, method


def main():
    parser = argparse.ArgumentParser(
        description="Extract one chord chart into JSON."
    )

    parser.add_argument("pdf")
    parser.add_argument("--key", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--author", default="")
    parser.add_argument("--force", action="store_true")

    args = parser.parse_args()

    song, how = extract(
        args.pdf,
        args.key,
        args.title,
        args.author,
        load_config(),
        force=args.force,
    )

    print(
        f"{song['title']} [{song['key']}] · {how} · "
        f"{len(song['sections'])} secciones"
    )


if __name__ == "__main__":
    main()
