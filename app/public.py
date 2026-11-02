"""Giriş gerektirmeyen dosyalar: robots.txt, PWA manifest ve service worker."""
from flask import Blueprint, Response, current_app, send_from_directory, url_for

bp = Blueprint("public", __name__)

# Bilinen yapay zekâ / veri toplama botları (hepsi zaten "*" ile engelli; açıkça da yazılıyor)
AI_BOTS = [
    "GPTBot", "ChatGPT-User", "OAI-SearchBot", "ClaudeBot", "Claude-Web", "Claude-User", "Claude-SearchBot",
    "anthropic-ai", "Google-Extended", "GoogleOther", "Applebot-Extended", "PerplexityBot", "Perplexity-User",
    "CCBot", "Bytespider", "Amazonbot", "meta-externalagent", "meta-externalfetcher", "FacebookBot",
    "cohere-ai", "cohere-training-data-crawler", "Diffbot", "YouBot", "MistralAI-User", "DuckAssistBot",
    "AI2Bot", "Ai2Bot-Dolma", "Omgilibot", "Timpibot", "ImagesiftBot", "PetalBot", "Scrapy",
]


@bp.route("/robots.txt")
def robots():
    lines = [f"User-agent: {b}\nDisallow: /\n" for b in AI_BOTS]
    lines.append("User-agent: *\nDisallow: /\n")
    return Response("# Bu site özeldir; arama motorları ve yapay zekâ botları için kapalıdır.\n\n" + "\n".join(lines),
                    mimetype="text/plain")


@bp.route("/manifest.webmanifest")
def manifest():
    icon = lambda size, purpose="any": {  # noqa: E731
        "src": url_for("static", filename=f"icons/icon-{size}{'-maskable' if purpose == 'maskable' else ''}.png"),
        "sizes": f"{size}x{size}", "type": "image/png", "purpose": purpose,
    }
    data = {
        "name": "Ödeme Takip",
        "short_name": "Ödeme Takip",
        "description": "Özel ders ödeme ve program takibi",
        "lang": "tr",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#11151f",
        "theme_color": "#3d6bff",
        "icons": [icon(192), icon(512), icon(192, "maskable"), icon(512, "maskable")],
        "shortcuts": [
            {"name": "Ödeme ekle", "url": "/odemeler/yeni"},
            {"name": "Haftalık program", "url": "/program"},
        ],
    }
    resp = current_app.response_class(
        current_app.json.dumps(data), mimetype="application/manifest+json")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@bp.route("/sw.js")
def service_worker():
    resp = send_from_directory(current_app.static_folder, "js/sw.js", mimetype="application/javascript")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp
