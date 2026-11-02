// Ödeme Takip service worker: uygulamanın telefona "ana ekrana ekle" ile kurulabilmesi için.
// Kişisel veriler önbelleğe alınmaz; sadece CSS/JS/ikonlar ve bağlantı yokken gösterilecek sayfa saklanır.
const CACHE = "odeme-takip-v1";
const OFFLINE_HTML = `<!doctype html><html lang="tr"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Bağlantı yok</title>
<body style="font-family:system-ui;background:#11151f;color:#e6e9f2;display:grid;place-items:center;min-height:100vh;margin:0;text-align:center;padding:20px">
<div><div style="font-size:48px">📡</div><h1 style="font-size:20px">İnternet bağlantısı yok</h1>
<p style="color:#9099b0">Bağlantı gelince sayfayı yenileyin.</p>
<button onclick="location.reload()" style="padding:10px 18px;border-radius:9px;border:0;background:#3d6bff;color:#fff;font-weight:600">Yenile</button></div>`;

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.put("/__offline", new Response(OFFLINE_HTML, {
    headers: { "Content-Type": "text/html; charset=utf-8" },
  }))));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))));
  self.clients.claim();
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;

  // Statik dosyalar: önbellekten hızlı, arkada güncelle
  if (url.pathname.startsWith("/static/")) {
    e.respondWith(caches.open(CACHE).then(async (c) => {
      const hit = await c.match(req);
      const net = fetch(req).then((res) => { if (res.ok) c.put(req, res.clone()); return res; }).catch(() => hit);
      return hit || net;
    }));
    return;
  }
  // Sayfalar: her zaman ağdan (veriler güncel kalsın); bağlantı yoksa bilgi sayfası
  if (req.mode === "navigate") {
    e.respondWith(fetch(req).catch(() => caches.match("/__offline")));
  }
});
