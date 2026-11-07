#!/usr/bin/env python3

"""
Put the songs page on worship.pilgrimagewithgod.org.

The tablet's copy travels by Syncthing, which needs the tablet and
the laptop to see each other. On 1 November 2026 they could not, at
church, and the songs were out of reach. The web copy removes that
dependency: any network reaches it, and once the tablet has opened it
the page is kept offline by a service worker (web/static/sw.js).

  1. Copy the freshly built index.html into web/public/, adding the
     two lines that make it an installable, offline app.
  2. Stamp the build (web/public/version.json).
  3. Deploy the Worker (web/), which keeps the page behind one
     passphrase.
  4. Read the stamp back from the live site, so "published" means
     the site really serves this build.

The Syncthing copy is left exactly as it was: the local file stays a
second way to reach Sunday. Never fatal: a failed publish is reported
and the rest of Saturday's run stands.

    publish_web.py                 # publish the current page
    publish_web.py --no-deploy     # prepare web/public only
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

WEB = ROOT / "web"
STATIC = WEB / "static"
PUBLIC = WEB / "public"
WRANGLER = WEB / "node_modules" / ".bin" / "wrangler"

DEFAULT_PAGE = Path.home() / "AI-Workspace" / "worship" / "tablet" / "app" / "index.html"

# Only on the web copy. A file:// page cannot register a service
# worker, and the local copy must not change.
HEAD = (
    '<link rel="manifest" href="/manifest.webmanifest">\n'
    '<link rel="icon" href="/icon-192.png">\n'
    '<link rel="apple-touch-icon" href="/icon-192.png">\n'
)

BODY = """
<script>
if ('serviceWorker' in navigator && (location.protocol === 'https:' || location.hostname === 'localhost')) {
  navigator.serviceWorker.register('/sw.js').then(async (reg) => {
    try {
      // Android may refresh the saved Sunday in the background on its
      // own schedule; opening the app online always does.
      if ('periodicSync' in reg) {
        const state = await navigator.permissions.query({ name: 'periodic-background-sync' });
        if (state.state === 'granted') {
          await reg.periodicSync.register('eicw-refresh', { minInterval: 6 * 3600 * 1000 });
        }
      }
    } catch (e) {}
  }).catch(() => {});
}
</script>
"""


def web_config(config):
    web = dict(config.get("web") or {})
    web.setdefault("enabled", False)
    web.setdefault("url", "https://worship.pilgrimagewithgod.org")
    return web


def prepare(page=DEFAULT_PAGE):
    """web/public/ from the built page. Returns the build stamp."""

    html = Path(page).expanduser().read_text(encoding="utf-8")

    marker = '<meta charset="utf-8">'

    if marker in html:
        html = html.replace(marker, marker + "\n" + HEAD, 1)
    else:
        html = HEAD + html

    html = html.rstrip() + "\n" + BODY

    PUBLIC.mkdir(parents=True, exist_ok=True)

    for old in PUBLIC.iterdir():
        if old.is_file():
            old.unlink()

    for item in STATIC.iterdir():
        if item.is_file():
            shutil.copy2(item, PUBLIC / item.name)

    (PUBLIC / "index.html").write_text(html, encoding="utf-8")

    stamp = {
        "build": hashlib.sha256(html.encode("utf-8")).hexdigest()[:16],
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    (PUBLIC / "version.json").write_text(json.dumps(stamp) + "\n", encoding="utf-8")

    return stamp


def live_build(url, timeout=15):
    request = urllib.request.Request(
        url.rstrip("/") + "/version.json",
        headers={"Cache-Control": "no-cache", "User-Agent": "eden-worship-publish"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read()).get("build")


def deploy(stamp, url):
    """Deploy and confirm. Returns (ok, one line for Telegram)."""

    if not WRANGLER.exists():
        return False, "⚠️ Web: falta la herramienta de Cloudflare (wrangler) en web/."

    if "__SEEN_NAMESPACE_ID__" in (WEB / "wrangler.jsonc").read_text(encoding="utf-8"):
        return False, "⚠️ Web: la configuración de Cloudflare no está terminada."

    try:
        result = subprocess.run(
            [str(WRANGLER), "deploy"],
            cwd=WEB,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"⚠️ Web: no se pudo publicar ({exc})."

    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()[-3:]
        return False, "⚠️ Web: Cloudflare rechazó la publicación: " + " / ".join(tail)

    # The edge can take a few seconds to serve the new version.
    for _ in range(12):
        try:
            if live_build(url) == stamp["build"]:
                host = url.split("//", 1)[-1].rstrip("/")
                return True, f"🌐 Web al día: {host}"
        except (OSError, ValueError):
            pass
        time.sleep(5)

    return False, "⚠️ Web: se publicó, pero el sitio todavía no muestra la versión nueva."


def publish(config, page=DEFAULT_PAGE):
    """Prepare and deploy when the web copy is enabled. None when it is off."""

    web = web_config(config)

    if not web["enabled"]:
        return None

    try:
        stamp = prepare(page)
    except OSError as exc:
        return f"⚠️ Web: no se pudo preparar la página ({exc})."

    return deploy(stamp, web["url"])[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--page", default=str(DEFAULT_PAGE), help="The built index.html.")
    parser.add_argument("--no-deploy", action="store_true", help="Prepare web/public only.")
    args = parser.parse_args()

    stamp = prepare(args.page)
    print(f"web/public listo · build {stamp['build']}")

    if args.no_deploy:
        return 0

    import build_site

    ok, line = deploy(stamp, web_config(build_site.load_config())["url"])
    print(line)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
