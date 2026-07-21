/**
 * Open ACE Service Worker
 * Provides offline support and caching for PWA
 */

const CACHE_NAME = 'open-ace-v6';
const STATIC_CACHE_NAME = 'open-ace-static-v6';
const API_CACHE_NAME = 'open-ace-api-v6';

// Static assets to cache immediately
const STATIC_ASSETS = ['/', '/app', '/login', '/static/js/dist/index.html'];

// API endpoints to cache
const API_PATTERNS = [
  /\/api\/summary/,
  /\/api\/today/,
  /\/api\/trend/,
  /\/api\/messages/,
  /\/api\/sessions/,
];

// Install event - cache static assets
self.addEventListener('install', (event) => {
  console.log('[SW] Installing service worker');

  event.waitUntil(
    caches.open(STATIC_CACHE_NAME).then((cache) => {
      console.log('[SW] Caching static assets');
      return cache.addAll(STATIC_ASSETS);
    })
  );

  // Activate immediately
  self.skipWaiting();
});

// Activate event - clean up old caches
self.addEventListener('activate', (event) => {
  console.log('[SW] Activating service worker');

  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames
          .filter((name) => {
            return name !== CACHE_NAME && name !== STATIC_CACHE_NAME && name !== API_CACHE_NAME;
          })
          .map((name) => {
            console.log('[SW] Deleting old cache:', name);
            return caches.delete(name);
          })
      );
    })
  );

  // Take control of all clients immediately
  self.clients.claim();
});

// Fetch event - serve from cache or network
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Skip non-GET requests
  if (request.method !== 'GET') {
    return;
  }

  // Skip cross-origin requests
  if (url.origin !== location.origin) {
    return;
  }

  // Handle API requests
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(handleApiRequest(request));
    return;
  }

  // Navigation requests (HTML pages) MUST be network-first so that new
  // deploys (new index.html referencing new content-hashed JS/CSS chunks)
  // are picked up immediately. A cache-first strategy here would freeze
  // users on the stale app shell from a previous deploy, defeating the
  // purpose of content-hashed assets. Issue #1912.
  if (request.mode === 'navigate') {
    event.respondWith(handleNavigationRequest(request));
    return;
  }

  // Handle static assets (content-hashed, immutable) — cache-first is safe.
  event.respondWith(handleStaticRequest(request));
});

/**
 * Handle API requests with network-first strategy
 */
async function handleApiRequest(request) {
  const url = new URL(request.url);
  const shouldCache = API_PATTERNS.some((pattern) => pattern.test(url.pathname));

  try {
    // Try network first
    const response = await fetch(request);

    if (response.ok && shouldCache) {
      // Cache successful responses
      const cache = await caches.open(API_CACHE_NAME);
      cache.put(request, response.clone());
    }

    return response;
  } catch (error) {
    // Network failed, try cache
    const cachedResponse = await caches.match(request);

    if (cachedResponse) {
      console.log('[SW] Serving from API cache:', request.url);
      return cachedResponse;
    }

    // Return error response
    return new Response(JSON.stringify({ error: 'Network error', offline: true }), {
      status: 503,
      headers: { 'Content-Type': 'application/json' },
    });
  }
}

/**
 * Handle navigation requests with network-first strategy.
 *
 * Always try the network first so new deploys are visible immediately; fall
 * back to the cached app shell only when offline. The fetched response is
 * cloned into the static cache so subsequent offline loads work.
 */
async function handleNavigationRequest(request) {
  try {
    const response = await fetch(request);
    if (response.ok && response.type === 'basic') {
      const cache = await caches.open(STATIC_CACHE_NAME);
      cache.put(request, response.clone());
    }
    return response;
  } catch (error) {
    const cachedResponse = await caches.match(request);
    if (cachedResponse) {
      return cachedResponse;
    }
    // Last-resort fallbacks for offline navigation.
    const fallback =
      (await caches.match('/app')) || (await caches.match('/static/js/dist/index.html'));
    if (fallback) {
      return fallback;
    }
    return new Response('Offline', { status: 503 });
  }
}

/**
 * Handle static requests with cache-first strategy
 */
async function handleStaticRequest(request) {
  // Try cache first
  const cachedResponse = await caches.match(request);

  if (cachedResponse) {
    // Refresh cache in background
    refreshCache(request);
    return cachedResponse;
  }

  // Not in cache, try network
  try {
    const response = await fetch(request);

    if (response.ok) {
      // Cache successful responses
      const cache = await caches.open(STATIC_CACHE_NAME);
      cache.put(request, response.clone());
    }

    return response;
  } catch (error) {
    // Return offline page for navigation requests
    if (request.mode === 'navigate') {
      const offlineResponse = await caches.match('/app');
      if (offlineResponse) {
        return offlineResponse;
      }
    }

    return new Response('Offline', { status: 503 });
  }
}

/**
 * Refresh cache in background
 */
async function refreshCache(request) {
  try {
    const response = await fetch(request);

    if (response.ok) {
      const cache = await caches.open(STATIC_CACHE_NAME);
      cache.put(request, response);
    }
  } catch (error) {
    // Ignore refresh errors
  }
}

// Handle messages from the main thread
self.addEventListener('message', (event) => {
  if (event.data === 'skipWaiting') {
    self.skipWaiting();
  }

  if (event.data === 'clearCache') {
    caches.keys().then((names) => {
      names.forEach((name) => caches.delete(name));
    });
  }
});

// Background sync for offline actions
self.addEventListener('sync', (event) => {
  console.log('[SW] Background sync:', event.tag);

  if (event.tag === 'sync-data') {
    event.waitUntil(syncData());
  }
});

/**
 * Sync data when back online
 */
async function syncData() {
  // Implement sync logic here
  console.log('[SW] Syncing data...');
}

// Push notifications
self.addEventListener('push', (event) => {
  const data = event.data?.json() || {};

  const options = {
    body: data.body || 'New notification',
    icon: '/static/icons/icon-192x192.png',
    badge: '/static/icons/icon-72x72.png',
    vibrate: [100, 50, 100],
    data: {
      url: data.url || '/app',
    },
    actions: [
      { action: 'open', title: 'Open' },
      { action: 'close', title: 'Close' },
    ],
  };

  event.waitUntil(self.registration.showNotification(data.title || 'Open ACE', options));
});

// Notification click
self.addEventListener('notificationclick', (event) => {
  event.notification.close();

  if (event.action === 'open' || !event.action) {
    const url = event.notification.data?.url || '/app';

    event.waitUntil(
      self.clients.matchAll({ type: 'window' }).then((clients) => {
        // Check if there's already a window open
        for (const client of clients) {
          if (client.url === url && 'focus' in client) {
            return client.focus();
          }
        }

        // Open new window
        return self.clients.openWindow(url);
      })
    );
  }
});
