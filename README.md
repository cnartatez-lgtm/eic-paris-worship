# EIC Paris · Worship

Sunday's songs, with chords, on the tablet. Built once a week by
Eden, read on a 10" Samsung tab in landscape — no paper, no PDFs,
no network on Sunday morning.

## What it is

One self-contained `index.html`. Every song on the coming Sunday's
Planning Center plan becomes a card, in each of the keys the
library has it in (G, D, C). Christian picks a song, picks a key,
and plays.

The page is opened as a **local file** on the tablet, so the data
is baked into the HTML rather than fetched beside it — a `file://`
page is not allowed to read a sibling JSON. That is why there is
one file and not a folder of assets.

Delivery reuses the Syncthing folder that already carries the
Sunday PDF: the build writes into `worship/tablet/app/`, which is
inside the synced folder, so the tablet gets it minutes later with
no extra setup.

## The weekly run

`tools/worship_app.py` — Saturday 21:00, from Hermes cron.

1. Read the coming plan for each service type (Rueil, Rive Gauche).
2. Collapse songs listed twice. Planning Center repeats a song when
   the set pauses it for a reading and resumes it; that is one card,
   not two.
3. For each song and each key, find the library file and extract it
   **if it is not already cached**.
4. Store the setlist in `data/setlists/`.
5. Rebuild `index.html`.
6. Report anything missing to Telegram.

Re-running costs nothing. Songs already read are never sent to a
model again.

## How a chart becomes a card

`tools/song_extract.py`. Half the library carries a real text layer
and half is image only, so there are two routes to the same JSON:

| Route | When | How |
|---|---|---|
| `text` | `pdftotext` returns real text | The text goes to the model, which puts the two printed columns back into reading order |
| `vision` | no text layer | The page is rendered to PNG and read by a vision model |

**The model's job is perception, never HTML.** It returns chords
positioned inside the lyric, ChordPro style; a plain Python renderer
turns that into the page. That split is deliberate: every song comes
out looking identical, rebuilding is free, and a model that writes
prose instead of markup cannot break the layout.

Results are cached in `data/songs/<slug>__<key>.json`, keyed by the
source file's hash, and **committed to this repo**. The library is
464 files; a song is paid for once, ever.

### Model choice

`qwen/qwen3-vl-235b-a22b-instruct`, set in `config/app.json`.

Chosen by testing, not by price. On a chart where the lyrics wrap
mid-line, the cheaper Gemini flash-lite models misread chords —
putting `D` where the chart said `G`. Qwen3-VL got every chord
right and joined the wrapped lines correctly. A wrong chord on
stage is worse than a few cents.

About $0.003 per song-key, paid once. A new four-song set costs
roughly three cents.

## Chords stay on their syllable

This is the thing the app exists to get right, and it is enforced
structurally rather than by tuning.

Every word is its own two-row inline grid: the chord in row 1, the
word it is played on in row 2. The chord is a **child of its own
word**, not something positioned by column arithmetic, so it cannot
drift — at any font size, any column count, any screen width.

The grid also settles the hard case, a chord wider than its word
(`Asus4` over "we"): the cell takes whichever is wider, so the next
word steps aside and two chords can never collide. Printed charts
space out the same way.

Lines that carry chords always reserve the chord row, so two lyric
lines can never close up and leave a chord nowhere to sit.

Verified by measurement, not by eye: at 14, 20, 28, 36 and 44px,
all 61 chords of *Word of God* sit at exactly `dx = 0` from their
word, with none out of line.

## On the tablet

Four tabs: **Sunday** (the set), **Order** (the whole liturgy from
Planning Center, readings and all — tapping a song opens its chart),
**Archive** (everything ever built, alphabetical, with a search bar)
and **Tuner**.

- **Archive search** — a bar under the title, which stays in view
  while the list scrolls. Capitals, accents and punctuation do not
  matter ("grâce" finds "grace"); every word typed must appear. Songs
  whose name (or author) matches come first, with the match marked;
  then, under "In the lyrics", songs that have the words in their
  lyrics, with the line that matched. Previous/Next in a song opened
  from a search walk the songs found. The search stays when he goes
  back to the list.
- **Tuner** — a guitar tuner on the tablet's microphone (standard
  tuning, A = 440 Hz). A needle crosses red (more than 30 cents out),
  orange (15–30), yellow (5–15) and green (within 5); the zone it is in
  lights up and the circle under it takes its colour, with the note,
  the cents, "tighten" or "loosen", and the frequency heard. Auto
  follows the nearest string; touching a string stays on it (for one
  far out of tune). A string held in the green for 0.7 s gets a tick;
  all six ticked says so. Pitch by YIN on the raw signal (Chrome's echo
  cancelling, noise suppression and auto gain off), checked at 1–2
  cents on synthetic strings with weak fundamentals. The microphone is
  on only while the tuner is on screen, and the screen stays awake
  meanwhile. Needs the web app (https); Chrome asks once for the
  microphone.

- **Fit** — the one that matters. Finds the largest type size at
  which the whole song is on one screen, trying one, two and three
  columns and keeping whichever allows the biggest text — but never
  below `tablet.min_font_px` (20px) in `config/app.json`, the smallest
  size that reads from a music stand. Recalculated per song and when
  the tablet is turned.
- **Long songs slide, they do not shrink.** Columns are exactly as
  tall as the screen, so nothing ever scrolls down or hides under a
  button. A song that does not fit one screen at 20px keeps three
  columns on screen and continues in a fourth, fifth... to the right.
  The strip at the bottom then shows a slider: one segment per column,
  a frame over the ones on screen, "1–3 / 5". Drag it or tap it; it
  always stops on whole columns. A swipe on the song, the arrow keys
  or a page-turner pedal (Page Down / Page Up = one screen) do the same.
- **1/2/3 columns** and **A− / A+** (8–44px) — manual override; using
  them turns Fit off, because he is overriding it on purpose. If the
  chosen size does not fit, the slider appears too.
- **Moon** — night mode for a dark stage.
- **Sun** — keeps the screen awake: a song outlasts the screen timeout,
  and going dark mid-verse is the reason paper felt safer.
- **Language** — Spanish, English, French, for the app's own words. The
  songs are untouched.

### The pen strip

A long strip left of the key picker, running back almost to the song
title. Christian writes "capo 2" with the S Pen and it is kept **the
moment he lifts it** — no keyboard, no save button. A round button at
its right end wipes it so he can rewrite, saved again on lift.

The strip is flexible, not a fixed width: it takes whatever the title
leaves. A short title gives it ~530px; a 100-character title squeezes
it to its 190px floor and the title ellipsises instead. Neither can
push the header out of the screen.

Notes are stored **per song and per key**: one written on the G sheet
does not appear on C, because a capo that makes G sound like A means
nothing there. A dot on a key pill says a note is waiting on that key.

Strokes are kept as fractions of the strip rather than pixels, so they
land correctly at any width. Once a real pen has been seen, touch is
ignored — a palm resting on the tablet cannot scribble.

Preferences and notes live in `localStorage`, wrapped in try/catch: a
tablet with site data blocked still renders, just without memory.

## Layout

```
config/app.json        keys, models, service types, output path
tools/song_extract.py  one PDF  → one song JSON   (costs money once)
tools/build_site.py    cache    → index.html      (free, deterministic)
tools/worship_app.py   the Saturday job
tools/check_fit.py     every chart against the tablet screen (free)
template/app.html      the design, with a __SONG_DATA__ placeholder
data/songs/            the cache — committed, this is the asset
data/setlists/         what was played when
```

## Running it by hand

```bash
V=~/AI-Workspace/theology/.venv/bin/python

# full weekly run
$V tools/worship_app.py --notify

# rebuild the page only (free, no model calls)
$V tools/build_site.py

# re-read one chart that came out wrong (costs a few cents)
$V tools/song_extract.py ~/AI-Workspace/music/EICMusic/<file>.pdf \
    --key G --title "Song Title" --force
```

## Reading a chart, and the compulsory quality gate

Since 14 November 2026 (Christian's rule) no chart reaches the tablet
unchecked, whatever it costs in time or tokens.

**Reading by position, not by guessing.** `song_extract.py` first uses
`tools/chart_layout.py`:

| PDF | How it is read |
|---|---|
| has a text layer | every chord at the exact coordinates it is printed at; chords above or *under* the words (Christian's "Horizontal-Simplificada" sheets) detected from the page structure; two, three or more columns |
| scanned page | tesseract OCR at 400 dpi; fixed-width printouts put each chord on its letter; known OCR slips repaired (Bm read as Bin, C as €, Em7 as Em?) |
| neither works | the model, as before |

A chord never starts inside the consonants that open a word ("Gr|eat"
becomes "Great"); past the first vowel the syllable is kept
("com-pas-sions"). For scanned Ultimate Guitar printouts,
`tools/ug_match.py` rebuilds the chart from Ultimate Guitar's own text:
it tries every version of the song, all 12 transpositions, plain and
"simplified", and keeps the one that matches what the PDF shows.

**The gate** — `tools/chart_gate.py`, run by `worship_app.py` on every
Sunday chart:

1. *Data*: not empty, no verse run into one line, no chord on the gap
   between words, every name a real chord.
2. *Blind review*: a strong vision model reads the PDF without seeing
   the chart and says which chord is printed over which word on every
   line (`tools/chart_review.py`); the chart must agree chord for chord
   and word for word. Claude Sonnet 5.5 reads; when it finds
   differences, Claude Opus 5.5 reads again and decides. Chosen on 14
   November 2026 by a test with five known charts (three bad, two
   good): both Claude models were right 5/5; Gemini 3.1 Pro and
   GPT-6 Luna Pro flagged good charts as bad.
3. *Screen*: every chart laid out at the tablet's size; nothing cut,
   every chord of the data drawn over its letter, no chord names
   touching, no word broken across two rows.

Passing all three makes a chart `"status": "verified"`: the finished
product. The page shows it whenever its song is on a plan, and
`song_extract.py` never reads its PDF again (`--force` refuses it
without `--unlock`). Anything else is `needs-review`, listed in the
Saturday report and marked "⚠ sin revisar" on the tablet.

## Checking every chart against the tablet

`tools/check_fit.py` opens the built page in a headless Chrome at
`tablet.viewport` (1280×728, the Tab S8 in landscape with the app
installed) with `#audit` at the end of the address. The page lays out
every chart in the archive exactly as the tablet would and reports the
size, the columns on screen and in total, and anything cut or spilling.
The same tool flags charts that were read badly — a verse run into one
line, chords sitting between words, an empty chart — which layout
cannot fix; re-read those with `song_extract.py --force`. The Saturday
run adds its summary to the Telegram report.

## If a song is missing

It is not in `~/AI-Workspace/music/EICMusic` under the library
naming convention, or not in that key. The Saturday report names
it; add the PDF through the usual `eden_service.py intake` and run
`worship_app.py` again.
