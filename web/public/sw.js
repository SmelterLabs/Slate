// Slate service worker: keeps the app shell (index.html, hashed assets, icons) so the installed
// app opens instantly and still opens offline. It never touches the API, the live stream or MCP:
// data always comes from the server, and the board shows "Reconnecting" when it can't.
const CACHE = 'slate-shell-v1';
const SHELL = ['/', '/manifest.webmanifest', '/icons/icon-192.png', '/icons/apple-touch-icon.png'];
const PASS = ['/api/', '/mcp', '/healthz'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

// Drop hashed assets the current index.html no longer references, so old deploys don't pile up.
async function prune(html) {
  const live = new Set(html.match(/\/assets\/[^"'\s)]+/g) ?? []);
  const cache = await caches.open(CACHE);
  for (const req of await cache.keys()) {
    const path = new URL(req.url).pathname;
    if (path.startsWith('/assets/') && !live.has(path)) await cache.delete(req);
  }
}

// Pages: network first, so a deploy shows up on the next open; the cached shell when offline,
// and while the proxy answers 502/503 for the server restarting under a deploy (the board
// then shows "Reconnecting" and reloads into the new build by itself).
async function page(req) {
  const cache = await caches.open(CACHE);
  try {
    const res = await fetch(req);
    if (res.ok) {
      await cache.put('/', res.clone());
      void res.clone().text().then(prune);
    } else if (res.status >= 500) {
      const shell = await cache.match('/');
      if (shell) return shell;
    }
    return res;
  } catch {
    return (await cache.match('/')) ?? Response.error();
  }
}

// Hashed assets never change under the same name: cache first.
async function asset(req) {
  const cache = await caches.open(CACHE);
  const hit = await cache.match(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok) await cache.put(req, res.clone());
  return res;
}

// Icons and the manifest: answer from cache, refresh in the background.
async function revalidate(req) {
  const cache = await caches.open(CACHE);
  const hit = await cache.match(req);
  const fresh = fetch(req)
    .then((res) => {
      if (res.ok) void cache.put(req, res.clone());
      return res;
    })
    .catch(() => hit ?? Response.error());
  return hit ?? fresh;
}

self.addEventListener('fetch', (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== self.location.origin) return;
  if (PASS.some((p) => url.pathname.startsWith(p))) return;
  if (req.mode === 'navigate') e.respondWith(page(req));
  else if (url.pathname.startsWith('/assets/')) e.respondWith(asset(req));
  else e.respondWith(revalidate(req));
});

// Push: Slate's server sends one encrypted JSON notification per inbox entry (service/push.py
// notification_for): {title, body, url, tag}. One tag per task, so a busy task replaces its
// last notification instead of stacking.
self.addEventListener('push', (e) => {
  let n = {};
  try {
    n = e.data ? e.data.json() : {};
  } catch {
    n = { title: 'Slate', body: e.data ? e.data.text() : '' };
  }
  e.waitUntil(
    self.registration.showNotification(n.title || 'Slate', {
      body: n.body || '',
      tag: n.tag || undefined,
      renotify: Boolean(n.tag),
      icon: '/icons/icon-192.png',
      badge: '/icons/icon-192.png',
      data: { url: n.url || '/#/inbox' },
    }),
  );
});

// A tap opens the task: focus an open Slate window and route it, else open a new one.
self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const url = new URL(e.notification.data?.url || '/#/inbox', self.location.origin).href;
  e.waitUntil(
    (async () => {
      const wins = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
      const win = wins.find((w) => new URL(w.url).origin === self.location.origin);
      if (win) {
        await win.focus();
        win.postMessage({ type: 'slate-open', url });
        return;
      }
      await self.clients.openWindow(url);
    })(),
  );
});
