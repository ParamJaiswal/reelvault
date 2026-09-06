/* ReelVault service worker: app-shell cache, network-first for API */
const CACHE = "reelvault-v1";
const SHELL = ["/", "/styles.css", "/app.js", "/manifest.webmanifest"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET") return;
  // never cache API or media (private data)
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/media/")) return;
  if (url.pathname.startsWith("/share-target")) return;
  e.respondWith(
    fetch(e.request).then(res => {
      const copy = res.clone();
      caches.open(CACHE).then(c => c.put(e.request, copy)).catch(() => {});
      return res;
    }).catch(() => caches.match(e.request))
  );
});

// push events arrive through the service worker; show toast if page visible
self.addEventListener("push", e => {
  let data = { title: "ReelVault", body: "" };
  try { data = e.data.json(); } catch {}
  e.waitUntil(self.registration.showNotification(data.title || "ReelVault", {
    body: data.body || "",
    tag: data.tag,
    data: { url: data.url || "/" },
  }));
});
self.addEventListener("notificationclick", e => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "/";
  e.waitUntil(clients.matchAll({ type: "window" }).then(list => {
    for (const c of list) if ("focus" in c) { c.navigate(url); return c.focus(); }
    return clients.openWindow(url);
  }));
});

// share-target GET helper: stash shared link then redirect into the app
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (!url.pathname.startsWith("/share-target")) return;
  if (e.request.method !== "POST") return;   // GET lands on the server directly
  e.respondWith((async () => {
    const dest = new URL("/#shared=1", url.origin);
    try {
      await e.request.formData();            // drain the POST so browser is happy
    } catch {}
    return Response.redirect(dest, 303);
  })());
});
