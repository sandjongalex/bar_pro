/* Bar Manager Pro PWA: network-only business pages, cache-only versioned public shell. */
const CACHE = 'bar-pro-shell-v1';
const SHELL = ['/static/offline.html', '/static/icons/pwa-icon.svg', '/static/icons/pwa-maskable.svg'];
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', event => {
  event.waitUntil(Promise.all([caches.keys().then(keys => Promise.all(keys.filter(k => k.startsWith('bar-pro-shell-') && k !== CACHE).map(k => caches.delete(k)))), self.clients.claim()]));
});
self.addEventListener('fetch', event => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  // Never cache authenticated pages, API calls, sales, invoices or business data.
  if (req.mode === 'navigate') {
    event.respondWith(fetch(req).catch(() => caches.match('/static/offline.html')));
    return;
  }
  if (SHELL.includes(url.pathname)) {
    event.respondWith(caches.match(req).then(hit => hit || fetch(req)));
  }
});
