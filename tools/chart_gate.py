#!/usr/bin/env python3

"""
The quality gate every chart passes before it is trusted on the tablet.

Christian's rule (14 November 2026), compulsory for Eden whatever it
costs in time or tokens:

1. DATA — the chart is not empty, no verse is run into one line, no
   chord sits on the gap between two words, every chord name is a real
   chord.
2. REVIEW — a strong vision model (config model_review, Claude Opus 5.5)
   reads the PDF on its own, without seeing the chart, and says which
   chord is printed over which word on every line. The chart must
   agree with that reading chord for chord and word for word
   (chart_review.py). A review is kept with a fingerprint of the chart
   and repeated only when the chart changes.
3. SCREEN — the built page is opened at the tablet's size
   (tablet.viewport) and every chart is laid out as on Sunday: nothing
   cut, nothing spilling sideways, every chord of the data drawn over
   the first letter of its piece of word, no two chord names touching,
   no word broken across two rows.

A chart that passes all three is "verified"; anything else is
"needs-review", with the reasons stored in the chart ("quality") and
shown on the tablet as "⚠ sin revisar". A verified chart is the
finished product: Eden never reads its PDF again (song_extract.py
refuses), the page just shows it whenever it is on a Sunday plan.

    chart_gate.py --sunday            # the coming Sunday's charts (what Eden runs)
    chart_gate.py --all               # every chart in the archive
    chart_gate.py slug__key ...       # some charts
    chart_gate.py --all --no-review   # data and screen only, no model calls
    chart_gate.py slug__key --accept "reason"
                                      # a person checked it against the PDF and
                                      # accepts the listed differences as
                                      # reviewer mistakes; recorded in the chart
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "tools"))

import build_site  # noqa: E402
import chart_layout  # noqa: E402
import chart_review  # noqa: E402
import check_fit  # noqa: E402

CACHE = ROOT / "data" / "songs"


def fingerprint(song):
    body = json.dumps(song.get("sections", []), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def coverage(song, library):
    """Share of the PDF's words the chart holds, by a fresh reading of the
    PDF (text layer, or OCR for a picture). None when unreadable."""

    import re

    pdf = library / song["source_file"]
    letters = lambda secs: len(re.sub(r"[^a-z]", "", " ".join(
        p.get("text", "") for s in secs for l in s["lines"] for p in l["parts"]).lower()))

    try:
        reading = chart_layout.read_pdf(pdf) or chart_layout.read_pdf(pdf, ocr=True)
    except Exception:  # noqa: BLE001
        return None

    if not reading:
        return None

    want = letters(reading["sections"])
    return letters(song["sections"]) / want if want else None


def data_problems(song):
    problems = list(check_fit.chart_problems(song))
    bad = sorted({
        part["chord"]
        for sec in song.get("sections", [])
        for line in sec.get("lines", [])
        for part in line.get("parts", [])
        if part.get("chord")
        and not chart_layout.is_chord(part["chord"])
        and not chart_layout.BAR_TOKEN.match(part["chord"])
    })

    if bad:
        problems.append("acordes que no son acordes: " + ", ".join(bad))

    return problems


def screen_problems(row, min_px):
    out = []

    if row["cut"]:
        out.append("se corta abajo")
    if row["wide"]:
        out.append("una palabra no cabe en su columna")
    if row["px"] < min_px:
        out.append(f"solo cabe a {row['px']} px")
    if row.get("drawn") != row.get("chords"):
        out.append(f"acordes perdidos: {row.get('chords')} en los datos, {row.get('drawn')} en pantalla")
    if row.get("off"):
        out.append(f"{row['off']} acordes fuera de su letra")
    if row.get("overlap"):
        out.append(f"{row['overlap']} acordes encimados")
    if row.get("broken"):
        out.append(f"{row['broken']} palabras partidas en dos renglones")

    return out


def screen_audit(config):
    """{(slug, key): row} from the page laid out at the tablet's size."""

    with tempfile.TemporaryDirectory() as work:
        data = build_site.build(config)
        page = build_site.render(data, work)
        chrome = check_fit.find_chrome(config)

        if not chrome:
            return None, "no hay Chrome sin pantalla"

        tablet = config.get("tablet") or {}
        viewport = tuple(tablet.get("viewport") or check_fit.DEFAULT_VIEWPORT)
        layout = check_fit.audit_layout(page, chrome, viewport)

    return {(r["slug"], r["key"]): r for r in layout["rows"]}, layout


def charts_for(args):
    if args.all:
        return sorted(CACHE.glob("*__*.json"))

    if args.sunday:
        config = build_site.load_config()
        data = build_site.build(config)
        slugs = {s["slug"] for svc in data["services"] for s in svc["songs"]}
        return sorted(p for p in CACHE.glob("*__*.json") if p.stem.split("__")[0] in slugs)

    return [CACHE / (c if c.endswith(".json") else c + ".json") for c in args.charts]


def gate(paths, review=True, accept=None, quiet=False, rereview=False, manual=None):
    """Run the gate on these chart files; returns {stem: status}."""

    config = build_site.load_config()
    library = Path(config.get("library", "~/AI-Workspace/music/EICMusic")).expanduser()
    model = config.get("model_review", chart_review.DEFAULT_MODEL)
    min_px = int((config.get("tablet") or {}).get("min_font_px", 20))

    screens, layout = screen_audit(config)
    results = {}

    for path in paths:
        song = json.loads(path.read_text(encoding="utf-8"))
        slug, key = path.stem.split("__", 1)
        mark = fingerprint(song)
        quality = dict(song.get("quality") or {})

        quality["data"] = data_problems(song)

        # Nothing of the PDF left out: a fresh reading of the PDF must not
        # hold clearly more words than the chart. Only for charts not yet
        # verified as they are (a reading costs nothing but takes seconds).
        if not (song.get("status") == "verified" and quality.get("fingerprint") == mark):
            share = coverage(song, library)
            quality["coverage"] = round(share, 3) if share else None
            if share is not None and share < 0.9:
                quality["data"].append(
                    f"le falta letra del PDF: la carta tiene el {share:.0%} de lo que se lee en el PDF")

        # The reviewer's reading is of the PDF, not of the chart: while the
        # PDF is the same, a corrected chart is compared with that reading
        # again for free. A new reading is paid for only for a new PDF.
        # A person compared the chart with its PDF line by line: that is the
        # review (kept with the chart's fingerprint, like a model's).
        if manual:
            previous = song.get("review") or {}
            song["review"] = {"ok": True, "issues": [], "manual": True, "by": manual,
                              "fingerprint": mark,
                              "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            if previous.get("reading") is not None:
                song["review"]["ai_reading"] = {k: previous.get(k) for k in
                                                ("model", "reading", "line_text", "source_hash")}

        old = chart_review.recompare(song)
        if old.get("reading") is not None and old.get("source_hash", song.get("source_hash")) \
                == song.get("source_hash"):
            old = dict(old, fingerprint=mark, source_hash=song.get("source_hash"))
            song["review"] = old

        if review and (old.get("fingerprint") != mark or rereview):
            try:
                result = chart_review.review(library / song["source_file"], song, model)

                # Differences found: the strongest reader decides.
                confirm = config.get("model_review_confirm")
                if not result["ok"] and confirm and confirm != model:
                    second = chart_review.review(library / song["source_file"], song, confirm)
                    second["first_opinion"] = {k: result[k] for k in ("model", "issues", "cost")}
                    result = second

                result["fingerprint"] = mark
                song["review"] = old = result
            except Exception as exc:  # noqa: BLE001
                old = {"ok": False, "issues": [{"kind": "error", "found": str(exc)[:200]}],
                       "fingerprint": ""}
                song["review"] = old

        # Still differences after the corrections: the strongest reader
        # reads the PDF once more and its reading decides (once per PDF).
        confirm = config.get("model_review_confirm")
        if review and confirm and old.get("fingerprint") == mark and not old.get("ok") \
                and old.get("reading") is not None and old.get("model") != confirm:
            try:
                second = chart_review.review(library / song["source_file"], song, confirm)
                second["first_opinion"] = {k: old.get(k) for k in ("model", "issues", "reading")}
                second["fingerprint"] = mark
                song["review"] = old = second
            except Exception as exc:  # noqa: BLE001
                print(f"   (segunda opinión no disponible: {str(exc)[:80]})")

        reviewed = old.get("fingerprint") == mark
        quality["review"] = (
            "sin revisión de IA" if not reviewed
            else "OK (revisión manual)" if old.get("manual") and old.get("ok")
            else "OK" if old.get("ok")
            else f"{len(old.get('issues', []))} diferencias con el PDF"
        )

        if screens is None:
            quality["screen"] = ["no se pudo revisar la pantalla (" + str(layout) + ")"]
        else:
            row = screens.get((slug, key))
            quality["screen"] = screen_problems(row, min_px) if row else ["no aparece en la página"]

        accepted = song.get("accepted") or {}

        if accept:
            accepted = {"reason": accept, "fingerprint": mark,
                        "issues": old.get("issues", []),
                        "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            song["accepted"] = accepted

        review_ok = reviewed and (old.get("ok") or accepted.get("fingerprint") == mark)
        passed = not quality["data"] and review_ok and not quality["screen"]

        quality["checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        quality["fingerprint"] = mark
        song["quality"] = quality
        song["status"] = "verified" if passed else "needs-review"
        path.write_text(json.dumps(song, ensure_ascii=False, indent=2), encoding="utf-8")
        results[path.stem] = song["status"]

        if not quiet:
            flag = "✅" if passed else "⚠️"
            print(f"{flag} {path.stem}: datos {quality['data'] or 'OK'} · IA {quality['review']}"
                  f" · pantalla {quality['screen'] or 'OK'}")

            if reviewed and not old.get("ok") and not accepted.get("fingerprint") == mark:
                for issue in old.get("issues", [])[:12]:
                    print(f"     {issue.get('line', '?'):5} {issue.get('kind', ''):12} "
                          f"carta: {issue.get('found', '')!s:32} PDF: {issue.get('expected', '')}")

    return results


def summary(results, titles=None):
    """Lines for the Saturday report."""

    bad = [k for k, v in results.items() if v != "verified"]
    lines = [f"🔎 Control de calidad: {len(results) - len(bad)} de {len(results)} cartas verificadas."]

    for stem in bad:
        slug, key = stem.split("__", 1)
        lines.append(f"⚠️ {(titles or {}).get(slug, slug)} {key}: no pasó el control "
                     f"(se ve en la tableta como «sin revisar»; ver chart_gate.py {stem}).")

    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("charts", nargs="*")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--sunday", action="store_true")
    parser.add_argument("--no-review", action="store_true")
    parser.add_argument("--rereview", action="store_true",
                        help="Ask the model again even if the chart did not change.")
    parser.add_argument("--accept", help="Reason a person accepts the remaining differences.")
    parser.add_argument("--manual", help="Who compared the chart with the PDF line by line, and how.")
    args = parser.parse_args()

    paths = charts_for(args)

    if not paths:
        print("No hay cartas que revisar.")
        return 1

    results = gate(paths, review=not args.no_review and not args.manual, accept=args.accept,
                   rereview=args.rereview, manual=args.manual)
    print()
    print("\n".join(summary(results)))
    return 0 if all(v == "verified" for v in results.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
