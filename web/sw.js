// Keeps the app itself available offline. Reports are not cached here (they live
// in the app's own storage); requests to Supabase always go to the network.
const CACHE = "journal-scope-shell-v4";
const SHELL = ["./", "index.html", "styles.css", "report.css", "app.js", "report-view.js", "discover.js", "supabase.js",
  "config.js", "manifest.webmanifest", "icons/icon-192.png", "icons/icon-512.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;   // Supabase: network only
  // Network first so updates show up straight away; fall back to the cached copy offline.
  e.respondWith(fetch(e.request).then((resp) => {
    const copy = resp.clone();
    caches.open(CACHE).then((c) => c.put(e.request, copy));
    return resp;
  }).catch(() => caches.match(e.request).then((r) => r || caches.match("index.html"))));
});
