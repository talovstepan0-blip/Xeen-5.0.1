/**
 * Сиен 3.0.0 — Service Worker
 *
 * Стратегия:
 *  • Статика (CSS, JS, шрифты) — Cache-first
 *  • Страницы HUD/dashboard — Network-first с офлайн-fallback
 *  • API (/dashboard/api/*, /tasks/*) — Network-only (всегда свежие данные)
 *  • Push-уведомления через Web Push API
 */

const CACHE_NAME = 'sien-v3.0.0';
const STATIC_ASSETS = [
  '/hud/',
  '/hud/index.html',
  '/hud/style.css',
  '/hud/main.js',
  '/hud/local_commands.js',
  '/static/manifest.json',
  '/static/offline.html',
];

// ── Установка ────────────────────────────────────────────────────
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) =>
      cache.addAll(STATIC_ASSETS).catch((err) => {
        console.warn('[SW] Не все ресурсы закэшированы:', err);
      })
    )
  );
  self.skipWaiting();
});

// ── Активация: чистим старые версии ─────────────────────────────
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))
      )
    )
  );
  self.clients.claim();
});

// ── Fetch ────────────────────────────────────────────────────────
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // API — всегда сеть
  if (url.pathname.startsWith('/dashboard/api/') ||
      url.pathname.startsWith('/tasks/') ||
      url.pathname.startsWith('/agents/')) {
    return; // браузер сам сходит в сеть
  }

  // WebSocket — не трогаем
  if (event.request.headers.get('upgrade') === 'websocket') {
    return;
  }

  // Только GET кэшируем
  if (event.request.method !== 'GET') return;

  // Cache-first для статики
  if (url.pathname.startsWith('/static/') ||
      url.pathname.startsWith('/hud/')) {
    event.respondWith(
      caches.match(event.request).then((cached) => {
        if (cached) return cached;
        return fetch(event.request).then((response) => {
          if (response && response.status === 200) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
          }
          return response;
        }).catch(() => caches.match('/static/offline.html'));
      })
    );
    return;
  }

  // Network-first для всего остального с офлайн-fallback
  event.respondWith(
    fetch(event.request).catch(() =>
      caches.match(event.request).then((cached) =>
        cached || caches.match('/static/offline.html')
      )
    )
  );
});

// ── Push-уведомления ────────────────────────────────────────────
self.addEventListener('push', (event) => {
  let data = { title: 'Сиен', body: 'Новое уведомление' };
  if (event.data) {
    try { data = event.data.json(); } catch { data.body = event.data.text(); }
  }
  event.waitUntil(
    self.registration.showNotification(data.title || 'Сиен', {
      body: data.body || '',
      icon: '/static/icon-192.png',
      badge: '/static/icon-192.png',
      vibrate: [100, 50, 100],
      data: data.url || '/hud/',
      actions: [
        { action: 'open', title: 'Открыть' },
        { action: 'dismiss', title: 'Закрыть' },
      ],
    })
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  if (event.action === 'dismiss') return;
  event.waitUntil(
    clients.openWindow(event.notification.data || '/hud/')
  );
});
