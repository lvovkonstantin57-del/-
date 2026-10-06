// Service worker: приложение открывается без сети, свежая версия — всегда, когда сеть есть.
const CACHE = "mpgu-schedule-v4";
const SHELL = [
  "/", "/style.css", "/app.js", "/logo.png", "/manifest.webmanifest",
  "/icon-192.png", "/icon-512.png", "/apple-touch-icon.png",
];
// Ответы API, которые стоит помнить офлайн: профиль и расписание
const CACHED_API = ["/api/me", "/api/schedule"];

self.addEventListener("install", (event) => {
  // cache: "reload" — берём файлы с сервера, а не из кэша браузера
  const fresh = SHELL.map((url) => new Request(url, { cache: "reload" }));
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(fresh)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

// Сначала сеть, при ошибке — сохранённая копия
async function networkFirst(request, cacheKey) {
  const cache = await caches.open(CACHE);
  try {
    // cache: "no-cache" — всегда сверяемся с сервером, даже если у браузера есть копия
    const fresh = request.mode === "navigate"
      ? new Request(request.url, { cache: "no-cache", credentials: "same-origin" })
      : new Request(request, { cache: "no-cache" });
    const response = await fetch(fresh);
    if (response.ok) cache.put(cacheKey, response.clone());
    return response;
  } catch (err) {
    const cached = await cache.match(cacheKey);
    if (cached) return cached;
    throw err;
  }
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (url.pathname.startsWith("/api/")) {
    if (CACHED_API.includes(url.pathname)) event.respondWith(networkFirst(request, url.pathname + url.search));
    return;
  }
  // Страница: любой адрес приложения — это index.html
  const key = request.mode === "navigate" ? "/" : url.pathname;
  event.respondWith(networkFirst(request, key));
});

// По просьбе страницы (выход из аккаунта) — забываем сохранённые данные
self.addEventListener("message", (event) => {
  if (event.data === "clear-api-cache") {
    event.waitUntil(caches.open(CACHE).then(async (cache) => {
      for (const req of await cache.keys()) {
        if (new URL(req.url).pathname.startsWith("/api/")) await cache.delete(req);
      }
    }));
  }
});
