// ViVeSec PWA service worker.
// Strategy:
//  - Navigations (SSR HTML): network-first, fall back to cached offline shell when offline.
//  - Same-origin static assets (scripts, styles, images, fonts): stale-while-revalidate.
//  - Everything else / non-GET / cross-origin: passthrough (never cached).
// Kept intentionally conservative so server-rendered responses are never served stale.

const VERSION = "v1";
const STATIC_CACHE = `vivesec-static-${VERSION}`;
const RUNTIME_CACHE = `vivesec-runtime-${VERSION}`;

// Resolved against the registration scope, so the same file works at the site
// root and under a mount prefix (/latest/, /<version>/) without rebuilding.
const SCOPE = new URL("./", self.registration ? self.registration.scope : self.location.href);
const scoped = (relative) => new URL(relative, SCOPE).pathname;

const OFFLINE_URL = scoped("offline.html");
const ICONS_PATH = scoped("icons/");

const PRECACHE_URLS = [OFFLINE_URL, scoped("manifest.webmanifest")];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(STATIC_CACHE)
      .then((cache) => cache.addAll(PRECACHE_URLS))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key !== STATIC_CACHE && key !== RUNTIME_CACHE)
            .map((key) => caches.delete(key)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

function isStaticAsset(request) {
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return false;
  if (request.destination === "script") return true;
  if (request.destination === "style") return true;
  if (request.destination === "image") return true;
  if (request.destination === "font") return true;
  if (url.pathname.startsWith(ICONS_PATH)) return true;
  return false;
}

self.addEventListener("fetch", (event) => {
  const { request } = event;

  if (request.method !== "GET") return;

  if (request.mode === "navigate") {
    event.respondWith(
      (async () => {
        try {
          return await fetch(request);
        } catch {
          const cache = await caches.open(STATIC_CACHE);
          const cached = await cache.match(OFFLINE_URL);
          return (
            cached ??
            new Response("You are offline.", {
              status: 503,
              headers: { "content-type": "text/plain; charset=utf-8" },
            })
          );
        }
      })(),
    );
    return;
  }

  if (isStaticAsset(request)) {
    event.respondWith(
      (async () => {
        const cache = await caches.open(RUNTIME_CACHE);
        const cached = await cache.match(request);
        const network = fetch(request)
          .then((response) => {
            if (response && response.status === 200 && response.type === "basic") {
              cache.put(request, response.clone());
            }
            return response;
          })
          .catch(() => cached);
        return cached ?? network;
      })(),
    );
  }
});
