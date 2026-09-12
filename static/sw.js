// Minimal service worker: caches the app shell (logo/icons/manifest) and
// shows a friendly offline page instead of the browser's default error
// when there's no connection. It does NOT cache API responses or booking
// data — this system needs a live connection to work correctly; the goal
// here is just a nicer "you're offline" experience, not offline booking.

const CACHE_NAME = 'raed-shell-v2';
const SHELL_ASSETS = [
  '/static/offline.html',
  '/static/logo.png',
  '/static/icon-192.png',
  '/static/icon-512.png',
  '/static/apple-touch-icon.png',
  '/static/manifest.json',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_ASSETS)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(names.filter((n) => n !== CACHE_NAME).map((n) => caches.delete(n)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const req = event.request;

  // Only handle page navigations specially (offline fallback).
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req).catch(() => caches.match('/static/offline.html'))
    );
    return;
  }

  // For same-origin static shell assets, try cache first, then network.
  const url = new URL(req.url);
  if (url.origin === self.location.origin && SHELL_ASSETS.some((a) => url.pathname === a)) {
    event.respondWith(
      caches.match(req).then((cached) => cached || fetch(req))
    );
  }
  // Everything else (API calls, admin data, etc.) goes straight to the
  // network as normal — intentionally not intercepted.
});

// ── Real Web Push — arrives even when the app/browser is fully closed ──
self.addEventListener('push', (event) => {
  let data = { title: 'إشعار جديد', body: '', url: '/admin/' };
  try {
    if (event.data) data = Object.assign(data, event.data.json());
  } catch (e) {}

  event.waitUntil((async () => {
    await self.registration.showNotification(data.title, {
      body: data.body,
      icon: '/static/icon-192.png',
      badge: '/static/icon-192.png',
      data: { url: data.url || '/admin/' },
      dir: 'rtl',
      lang: 'ar',
    });

    // Wake any open admin page IMMEDIATELY (no waiting for its next poll)
    // so the print station can react the instant a booking is approved —
    // this is what makes it feel like a live receipt printer instead of
    // something that catches up every few seconds.
    if (data.type === 'booking-approved') {
      const clients = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
      clients.forEach((client) => client.postMessage({ type: 'booking-approved' }));
    }
  })());
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || '/admin/';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windowClients) => {
      for (const client of windowClients) {
        if (client.url.includes('/admin') && 'focus' in client) {
          client.navigate(url);
          return client.focus();
        }
      }
      if (clients.openWindow) return clients.openWindow(url);
    })
  );
});
