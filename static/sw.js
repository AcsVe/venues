// Minimal service worker: caches the app shell (logo/icons/manifest) and
// shows a friendly offline page instead of the browser's default error
// when there's no connection. It does NOT cache API responses or booking
// data — this system needs a live connection to work correctly; the goal
// here is just a nicer "you're offline" experience, not offline booking.

const CACHE_NAME = 'raed-shell-v1';
const SHELL_ASSETS = [
  '/static/offline.html',
  '/static/logo.jpg',
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
