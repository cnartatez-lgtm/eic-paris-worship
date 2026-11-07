// Keeps Sunday's page on the tablet so it opens with no network.
//
// The page is fetched network-first: with internet the tablet always
// shows the latest build and saves it; without internet (or on a
// network too slow to answer in a few seconds) it opens the saved copy.
// Only a real build replaces the saved copy, never the login page or
// an error, so a bad connection on Saturday cannot wipe Sunday.
//
// Saving happens whenever the app is opened online and, when Android
// allows it, on a background refresh the browser schedules by itself.
// Each saved build is reported to the server so Eden can tell on
// Saturday night whether Sunday is already on the tablet.

const CACHE = "eicw-v1";
const PAGE = "/";
const NETWORK_TIMEOUT_MS = 4000;

self.addEventListener("install", (event) => {
  self.skipWaiting();
  event.waitUntil(refresh());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("periodicsync", (event) => {
  if (event.tag === "eicw-refresh") {
    event.waitUntil(refresh());
  }
});

self.addEventListener("message", (event) => {
  if (event.data === "refresh") {
    event.waitUntil(refresh());
  }
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);

  if (request.method !== "GET" || url.origin !== self.location.origin) {
    return;
  }

  if (request.mode === "navigate" || url.pathname === PAGE || url.pathname === "/index.html") {
    event.respondWith(page(event));
  }
});

function isBuild(response) {
  return response && response.ok && response.headers.get("X-EIC-App") === "1";
}

async function save(response) {
  const cache = await caches.open(CACHE);
  await cache.put(PAGE, response);
  await report();
}

async function report() {
  try {
    const version = await fetch("/version.json", { cache: "no-store" });
    if (!version.ok) return;
    const { build } = await version.json();
    await fetch("/api/seen", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ build }),
      credentials: "same-origin",
    });
  } catch {
    // Reporting is a courtesy to Eden; the saved page is what matters.
  }
}

async function refresh() {
  try {
    const response = await fetch(PAGE, { cache: "no-store", credentials: "same-origin" });
    if (isBuild(response)) {
      await save(response);
    }
  } catch {
    // Offline: keep what is saved.
  }
}

function withTimeout(promise, ms) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("timeout")), ms);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error) => {
        clearTimeout(timer);
        reject(error);
      },
    );
  });
}

async function page(event) {
  const cache = await caches.open(CACHE);
  const saved = await cache.match(PAGE);
  const network = fetch(PAGE, { cache: "no-store", credentials: "same-origin" });

  // Whatever the timeout decides, a build that arrives late is still saved.
  event.waitUntil(
    network
      .then((response) => (isBuild(response) ? save(response.clone()) : null))
      .catch(() => null),
  );

  try {
    const response = await withTimeout(network, saved ? NETWORK_TIMEOUT_MS : 30000);
    if (isBuild(response)) {
      return response;
    }
    // The login page (expired cookie) is shown so it can be fixed; a
    // server error is not, when there is a saved Sunday to open instead.
    if (response.status >= 500 && saved) {
      return saved;
    }
    return response;
  } catch {
    if (saved) {
      return saved;
    }
    return new Response(
      "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>" +
        "<body style='font:20px system-ui;padding:24px'>Sin conexión y sin copia guardada todavía. " +
        "Abre la app una vez con internet para que guarde el domingo.</body>",
      { status: 503, headers: { "Content-Type": "text/html; charset=utf-8" } },
    );
  }
}
