self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open("campus-radar-v1").then((cache) =>
      cache.addAll(["/", "/assets/app.css", "/assets/app.js", "/manifest.webmanifest"])
    )
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (url.pathname.startsWith("/api/")) return;
  event.respondWith(caches.match(event.request).then((cached) => cached || fetch(event.request)));
});

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (_error) {
    data = { title: "校园机会雷达", body: event.data ? event.data.text() : "有新的校园机会" };
  }
  const title = data.title || "校园机会雷达";
  const options = {
    body: data.body || "发现新的校园机会",
    data: { url: data.url || "/" },
    icon: "/assets/icon.svg",
    badge: "/assets/icon.svg"
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = event.notification.data && event.notification.data.url ? event.notification.data.url : "/";
  event.waitUntil(clients.openWindow(url));
});
