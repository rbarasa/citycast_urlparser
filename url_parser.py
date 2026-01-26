import json
import re
import time
from dataclasses import dataclass
from typing import Optional, Dict, Any, List, Tuple

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from urllib.parse import urlsplit
from adapters import is_timely_slug_response, extract_title_and_description


@dataclass
class EventCard:
    url: str
    title: Optional[str] = None
    description: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    location: Optional[str] = None
    image: Optional[str] = None
    source: Optional[str] = None  # requests or playwright

def looks_blocked(html: str) -> bool:
    h = html.lower()
    signals = [
        "403 error", "request could not be satisfied", "access denied",
        "cloudfront", "akamai", "bot detection", "captcha", "incapsula",
        "verify that you're not a robot", "javascript is disabled", "aws waf"
    ]
    return any(s in h for s in signals) or len(html.strip()) < 1200


def fetch_with_requests(url: str, timeout: int = 25) -> Tuple[int, str]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/141.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    with requests.Session() as s:
        r = s.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        return r.status_code, r.text


def fetch_with_playwright(url: str, timeout_ms: int = 60000) -> Tuple[str, Optional[Dict[str, Any]]]:
    timely_payload: Optional[Dict[str, Any]] = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            storage_state="storage_state.json",
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
            locale="en-US",
            timezone_id="America/Denver",
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()

        def on_response(resp):
            nonlocal timely_payload
            if timely_payload is not None:
                return
            ct = (resp.headers.get("content-type") or "").lower()
            if "json" not in ct:
                return
            if is_timely_slug_response(resp.url):
                try:
                    timely_payload = resp.json()
                except Exception:
                    pass

        page.on("response", on_response)

        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(4000)

        html = page.content()
        browser.close()
        return html, timely_payload


def extract_from_ld_json(soup: BeautifulSoup) -> Dict[str, Any]:
    scripts = soup.select('script[type="application/ld+json"]')
    for s in scripts:
        raw = (s.string or s.get_text() or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue

        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            t = item.get("@type")
            if isinstance(t, list):
                t = next((x for x in t if "Event" in str(x)), None)
            if t and "Event" in str(t):
                return item
    return {}


def extract_open_graph(soup: BeautifulSoup) -> Dict[str, str]:
    def meta(prop: str) -> Optional[str]:
        tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
        return tag.get("content") if tag and tag.get("content") else None

    return {
        "title": meta("og:title") or meta("twitter:title"),
        "description": meta("og:description") or meta("twitter:description"),
        "image": meta("og:image") or meta("twitter:image"),
    }


def parse_event(url: str) -> EventCard:
    has_fragment = bool(urlsplit(url).fragment)

    status, html = fetch_with_requests(url)

    timely_payload = None
    if has_fragment or status in (403, 429) or looks_blocked(html):
        html, timely_payload = fetch_with_playwright(url)
        source = "playwright"
    else:
        source = "requests"

    soup = BeautifulSoup(html, "html.parser")
    card = EventCard(url=url, source=source)

    if timely_payload:
        fields = extract_title_and_description(timely_payload)
        card.title = fields.get("title")
        card.description = fields.get("description")

    ld = extract_from_ld_json(soup)
    if ld:
        card.title = ld.get("name")
        card.description = ld.get("description")
        card.start_date = ld.get("startDate")
        card.end_date = ld.get("endDate")

        loc = ld.get("location")
        if isinstance(loc, dict):
            card.location = loc.get("name") or (loc.get("address") or "")
        elif isinstance(loc, list) and loc and isinstance(loc[0], dict):
            card.location = loc[0].get("name")

        img = ld.get("image")
        if isinstance(img, str):
            card.image = img
        elif isinstance(img, list) and img:
            card.image = img[0] if isinstance(img[0], str) else None

    # Fill missing fields from Open Graph as fallback
    og = extract_open_graph(soup)
    card.title = card.title or og.get("title") or (soup.title.get_text(strip=True) if soup.title else None)
    card.description = card.description or og.get("description")
    card.image = card.image or og.get("image")

    # Last resort description from page text snippet
    if not card.description:
        text = soup.get_text(" ", strip=True)
        card.description = text[:280] if text else None

    return card

urls =[
"https://www.historycolorado.org/events-experiences#event=holiday-tea-4;instance=20251129000000?popup=1&lang=en-US", 
"https://www.eventbrite.com/e/phil-goodstein-live-at-tattered-cover-colfax-tickets-1901340613959?aff=oddtdtcreator",
"https://waldschankeciders.com/events/",
"https://tickets.meowwolf.com/events/denver/adulti-verse-1126/",
"https://nocturnejazz.com/music/16-uncategorised/4541-adam-gang-quintet",
"https://www.woodiefisher.com/event/drinksgiving-at-woodie-fisher/",
"https://dairyblock.com/events/geeks-who-drink-trivia/2025-11-27/",
"https://raceroster.com/events/2025/97668/mile-high-united-way-turkey-trot-2025",
"https://luvinarms.org/news/turkey-trot-thanksliving-2025/",
"https://www.lavenderhilldenver.org/winter-pride",
"https://www.eventbrite.com/e/drag-stravaganza-holgay-fri-dec-5-or-sat-dec-6-tickets-1902773870869",
"https://fareharbor.com/embeds/book/talnuadistillery/items/676704/calendar/2025/11/?flow=1416710&full-items=yes",
"https://www.stanleymarketplace.com/all-events/first-friday-3d-pen-craft-movie-night-trmra-fpfs5",
"https://www.bierstadtlager.com/events/bierhalle-brawl-live-pro-wrestling-december",
"https://www.southpearlstreet.com/winterfest/",
"https://fictionbeer.com/events/2025/12/6/cookie-bake-off",
"https://www.thedinnerdetective.com/denver/murder-mystery-tickets-showtimes/",
"https://www.eventbrite.com/e/drag-stravaganza-holgay-fri-dec-5-or-sat-dec-6-tickets-1902773870869",
"https://www.dmns.org/purchase/tickets/#events-45134",
"https://www.southpearlstreet.com/winterfest/",
"https://stranahans.com/events/snowflake/",
"https://fictionbeer.com/events/2025/12/7/audacious-immersive-presents-drunk-christmas",
"https://www.bierstadtlager.com/events/artisan-holiday-market",
"https://raceroster.com/events/2025/109502/rudolph-ramble-5k-runwalk"
]

results = [parse_event(u) for u in urls]
for r in results:
    print(r)
