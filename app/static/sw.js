/* Bar Manager Pro PWA: network-only business data, no offline queue. */
const OFFLINE_CACHE = 'barpro-public-offline-v1';
const SAFE_ASSETS = ['/static/offline.html','/static/icons/barpro-192.svg','/static/icons/barpro-512.svg'];
self.addEventListener('install', event => {
  event.waitUntil(caches.open(OFFLINE_CACHE).then(cache => cache.addAll(SAFE_ASSETS)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', event => {
  event.waitUntil(Promise.all([
    caches.keys().then(keys => Promise.all(keys.filter(k => k !== OFFLINE_CACHE).map(k => caches.delete(k)))),
    self.clients.claim()
  ]));
});
self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.mode !== 'navigate' || request.method !== 'GET') return;
  // Never cache authenticated HTML, API responses, cookies, receipts or POSTs.
  event.respondWith(fetch(request).catch(() => caches.match('/static/offline.html')));
});
