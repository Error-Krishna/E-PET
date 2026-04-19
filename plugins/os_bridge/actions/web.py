from __future__ import annotations

import logging
import urllib.parse

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

from .apps import open_url

logger = logging.getLogger(__name__)

WEBSITE_MAP = {
    "youtube": "https://youtube.com",
    "google": "https://google.com",
    "gmail": "https://mail.google.com",
    "github": "https://github.com",
    "reddit": "https://reddit.com",
    "twitter": "https://twitter.com",
    "x": "https://x.com",
    "linkedin": "https://linkedin.com",
    "netflix": "https://netflix.com",
    "spotify": "https://open.spotify.com",
    "amazon": "https://amazon.com",
    "wikipedia": "https://wikipedia.org",
    "stack overflow": "https://stackoverflow.com",
    "chatgpt": "https://chat.openai.com",
    "claude": "https://claude.ai",
    "notion": "https://notion.so",
    "figma": "https://figma.com",
    "vercel": "https://vercel.com",
    "heroku": "https://heroku.com",
    "aws console": "https://console.aws.amazon.com",
    "google cloud": "https://console.cloud.google.com",
    "azure": "https://portal.azure.com",
}


def google_search(query: str) -> None:
    open_url(f"https://google.com/search?q={urllib.parse.quote_plus(str(query or '').strip())}")


def youtube_search(query: str) -> None:
    open_url(f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(str(query or '').strip())}")


def open_website(name: str) -> None:
    target = WEBSITE_MAP.get(str(name or "").strip().lower())
    if not target:
        target = str(name or "").strip()
    open_url(target)


def get_weather(city: str = None) -> str:
    if requests is None:
        return ""
    url = "https://wttr.in"
    if city:
        url = f"{url}/{urllib.parse.quote(str(city).strip())}"
    try:
        response = requests.get(f"{url}?format=3", timeout=10)
        response.raise_for_status()
        return response.text.strip()
    except Exception as exc:
        logger.debug("weather lookup failed: %s", exc)
        return ""


def get_news_headlines(topic: str = None) -> list[str]:
    if requests is None:
        return []
    rss_url = "https://news.google.com/rss"
    if topic:
        rss_url = f"{rss_url}/search?q={urllib.parse.quote_plus(topic)}"
    try:
        response = requests.get(rss_url, timeout=10)
        response.raise_for_status()
        headlines = []
        for line in response.text.splitlines():
            if "<title>" in line:
                title = line.split("<title>", 1)[1].split("</title>", 1)[0]
                if title and "RSS" not in title:
                    headlines.append(title)
        return headlines[:10]
    except Exception as exc:
        logger.debug("news lookup failed: %s", exc)
        return []


def translate_text(text: str, target_lang: str = "en") -> str:
    if requests is None:
        return str(text or "")
    payload = {"q": str(text or ""), "source": "auto", "target": target_lang, "format": "text"}
    try:
        response = requests.post("https://libretranslate.de/translate", data=payload, timeout=10)
        response.raise_for_status()
        data = response.json()
        return str(data.get("translatedText", text))
    except Exception as exc:
        logger.debug("translation failed: %s", exc)
        return str(text or "")
