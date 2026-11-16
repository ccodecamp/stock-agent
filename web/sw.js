// Network-first service worker. Its only jobs: make the app installable and show a
// friendly page when the phone is offline. It never touches /api (chat answers and
// prices must always be live) and always asks the server first, so a deploy is never hidden
// behind an old cached copy.
const CACHE = "stock-guide-v1";

const OFFLINE_PAGE = `<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stock Guide</title>
<body style="font:16px/1.5 system-ui,sans-serif;display:grid;place-items:center;min-height:100vh;margin:0;padding:24px;text-align:center">
<div><h1 style="font-size:1.3rem">You're offline</h1>
<p>Stock Guide needs the internet to look up live prices. Reconnect and try again.</p>
<button onclick="location.reload()" style="font:inherit;padding:10px 18px">Try again</button></div>`;

self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/api/")) {
    return; // the browser handles it normally
  }
  event.respondWith(
    fetch(request, { cache: "no-cache" })
      .then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(request, copy));
        }
        return response;
      })
      .catch(async () => {
        const cached = await caches.match(request);
        if (cached) return cached;
        if (request.mode === "navigate") {
          return new Response(OFFLINE_PAGE, { headers: { "Content-Type": "text/html; charset=utf-8" } });
        }
        return Response.error();
      }),
  );
});
