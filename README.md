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

Three tabs: **Sunday** (the set), **Order** (the whole liturgy from
Planning Center, readings and all — tapping a song opens its chart),
**Archive** (everything ever built, alphabetical).

- **Fit** — the one that matters. Binary-searches the largest type
  size at which the song still fits with no scrolling, trying one, two
  and three columns and keeping whichever allows the biggest text.
  Recalculated per song and when the tablet is turned.
- **1/2/3 columns** and **A− / A+** (8–44px) — manual override; using
  them turns Fit off, because he is overriding it on purpose.
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

## If a song is missing

It is not in `~/AI-Workspace/music/EICMusic` under the library
naming convention, or not in that key. The Saturday report names
it; add the PDF through the usual `eden_service.py intake` and run
`worship_app.py` again.
