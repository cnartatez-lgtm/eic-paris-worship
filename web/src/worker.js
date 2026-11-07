// worship.pilgrimagewithgod.org — the Sunday songs app on the web.
//
// The page itself is the same self-contained index.html that Syncthing
// carries to the tablet; this Worker only decides who may read it.
// Chord charts are licensed for the church, not for the open internet,
// and the order of service names the people reading, so the page sits
// behind one passphrase. A device that has entered it once keeps a
// cookie for 400 days (the browser maximum) and is never asked again.
//
// A few paths stay open because they carry no songs and the browser
// fetches some of them without cookies: the manifest and icons (app
// install), the service worker, the build stamp Eden checks on
// Saturday night, and the last build the tablet reported saving.

const COOKIE = "eicw";
const COOKIE_MAX_AGE = 400 * 24 * 3600;

const PUBLIC = new Set([
  "/manifest.webmanifest",
  "/icon-192.png",
  "/icon-512.png",
  "/icon-maskable-512.png",
  "/sw.js",
  "/version.json",
  "/favicon.ico",
]);

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

    if (PUBLIC.has(path)) {
      return env.ASSETS.fetch(request);
    }

    if (path === "/tablet.json") {
      return tabletStatus(env);
    }

    if (!env.PASSPHRASE) {
      // Fail closed: a missing secret must not publish the songs.
      return page(
        "La página todavía no está configurada.",
        "Falta la contraseña en Cloudflare.",
        503,
      );
    }

    if (path === "/login" && request.method === "POST") {
      return login(request, env);
    }

    if (!(await authorised(request, env))) {
      return loginPage("", 401);
    }

    if (path === "/api/seen" && request.method === "POST") {
      return seen(request, env);
    }

    const response = await env.ASSETS.fetch(request);

    if (path === "/" || path === "/index.html") {
      // Always revalidate: Saturday's build must replace Friday's.
      const fresh = new Response(response.body, response);
      fresh.headers.set("Cache-Control", "no-cache");
      fresh.headers.set("X-EIC-App", "1");
      return fresh;
    }

    return response;
  },
};

async function token(env) {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(env.PASSPHRASE),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const mac = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode("eic-worship-v1"),
  );
  return [...new Uint8Array(mac)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function cookieValue(request) {
  const header = request.headers.get("Cookie") || "";
  for (const part of header.split(";")) {
    const [name, ...rest] = part.trim().split("=");
    if (name === COOKIE) return rest.join("=");
  }
  return "";
}

function sameString(a, b) {
  // Constant time, so the comparison does not leak the token.
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

async function authorised(request, env) {
  const value = cookieValue(request);
  return Boolean(value) && sameString(value, await token(env));
}

async function login(request, env) {
  let given = "";
  try {
    const form = await request.formData();
    given = String(form.get("passphrase") || "");
  } catch {
    given = "";
  }

  if (!sameString(given.trim(), env.PASSPHRASE.trim())) {
    // A short pause makes guessing slow without bothering a person.
    await new Promise((resolve) => setTimeout(resolve, 900));
    return loginPage("Contraseña incorrecta.", 401);
  }

  return new Response(null, {
    status: 303,
    headers: {
      Location: "/",
      "Set-Cookie":
        `${COOKIE}=${await token(env)}; Path=/; Max-Age=${COOKIE_MAX_AGE}; ` +
        "HttpOnly; Secure; SameSite=Lax",
    },
  });
}

async function seen(request, env) {
  // The tablet's service worker reports each build it saves, so Eden
  // can tell on Saturday night whether Sunday is already on the tablet.
  let build = "";
  try {
    build = String((await request.json()).build || "").slice(0, 64);
  } catch {
    build = "";
  }

  if (build && env.SEEN) {
    await env.SEEN.put(
      "tablet",
      JSON.stringify({ build, at: new Date().toISOString() }),
    );
  }

  return new Response(null, { status: 204 });
}

async function tabletStatus(env) {
  const value = env.SEEN ? await env.SEEN.get("tablet") : null;
  return new Response(value || "{}", {
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
    },
  });
}

function escape(text) {
  return String(text).replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
}

function shell(body, status) {
  return new Response(
    `<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#2D637F">
<link rel="manifest" href="/manifest.webmanifest">
<title>EIC Paris · Worship</title>
<style>
  :root { --bg:#F2F5F7; --surface:#FFFFFF; --ink:#16232B; --muted:#5B6B75; --teal:#2D637F; --line:#D5DEE4; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#0F1417; --surface:#182025; --ink:#E7EEF2; --muted:#9AAAB4; --line:#2A353C; }
  }
  * { box-sizing: border-box; }
  body { margin:0; min-height:100vh; display:grid; place-items:center; padding:16px;
         background:var(--bg); color:var(--ink);
         font:18px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
  main { width:100%; max-width:420px; background:var(--surface); border:1px solid var(--line);
         border-radius:16px; padding:28px 24px; }
  h1 { margin:0 0 6px; font-size:22px; }
  p { margin:0 0 18px; color:var(--muted); }
  label { display:block; font-weight:600; margin-bottom:8px; }
  input { width:100%; font:inherit; padding:12px 14px; border-radius:10px;
          border:1px solid var(--line); background:var(--bg); color:var(--ink); }
  button { margin-top:16px; width:100%; font:inherit; font-weight:600; padding:12px;
           border:0; border-radius:10px; background:var(--teal); color:#fff; cursor:pointer; }
  .error { color:#B3261E; font-weight:600; }
</style>
</head>
<body>
<main>
${body}
</main>
</body>
</html>`,
    {
      status,
      headers: {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-store",
      },
    },
  );
}

function loginPage(error, status) {
  return shell(
    `<h1>EIC Paris · Worship</h1>
<p>Las canciones del domingo. Escribe la contraseña una vez; este dispositivo la recordará.</p>
${error ? `<p class="error">${escape(error)}</p>` : ""}
<form method="post" action="/login">
  <label for="passphrase">Contraseña</label>
  <input id="passphrase" name="passphrase" type="password" autocomplete="current-password" required autofocus>
  <button type="submit">Entrar</button>
</form>`,
    status,
  );
}

function page(title, detail, status) {
  return shell(`<h1>${escape(title)}</h1><p>${escape(detail)}</p>`, status);
}
