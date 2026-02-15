import json
import re
from dataclasses import dataclass
from typing import Optional, Dict, Any, List, Tuple

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page
from adapters import ADAPTERS
import html as html_lib
from urllib.parse import urlsplit, urljoin
from dateparser.search import search_dates
from dateutil.parser import isoparse
import extruct
from w3lib.html import get_base_url


# if site is returning blocked content, mark source as blocked to help identify patterns
BLOCKED_KEYWORDS = [
    "identity verified",
    "verify your identity",
    "are you a real fan",
    "just a moment",
]

SESSION = requests.Session()

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
    
class PlaywrightSession:
    def __init__(self, headless: bool = True):
        self._p = sync_playwright().start()
        self._browser: Browser = self._p.chromium.launch(headless=headless)
        self._context: BrowserContext = self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/141.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            viewport={"width": 1280, "height": 800},
        )

        # Block heavy stuff for speed
        def route_handler(route):
            r = route.request
            rt = r.resource_type
            url = r.url.lower()

            if rt in ("image", "media", "font"):
                return route.abort()

            if any(x in url for x in ("google-analytics", "doubleclick", "googletagmanager", "facebook", "segment", "mixpanel")):
                return route.abort()

            return route.continue_()

        self._context.route("**/*", route_handler)
        self._page: Page = self._context.new_page()

    def close(self):
        try:
            self._page.close()
        except Exception:
            pass
        self._context.close()
        self._browser.close()
        self._p.stop()

    def fetch(self, url: str, timeout_ms: int = 25000) -> Tuple[str, Optional[Dict[str, Any]]]:
        extracted: Optional[Dict[str, Any]] = None
        page: Page = self._page

        def on_response(resp):
            nonlocal extracted
            if extracted is not None:
                return
            ct = (resp.headers.get("content-type") or "").lower()
            if "json" not in ct:
                return
            for adapter in ADAPTERS:
                try:
                    if not adapter.match_response(resp.url, ct):
                        continue
                    payload = resp.json()
                    fields = adapter.extract_event(payload)
                    if (fields.get("title") or "").strip() or (fields.get("description") or "").strip():
                        extracted = fields
                        return
                except Exception:
                    continue

        page.on("response", on_response)

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception:
            pass

        # if JSON was captured, do not scroll, do not sleep more
        if extracted is not None:
            html = page.content()
            page.remove_listener("response", on_response)
            return html, extracted

        # small settle, not 600ms every time
        page.wait_for_timeout(150)

        # scroll only if still nothing
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(150)
        except Exception:
            pass

        html = page.content()
        page.remove_listener("response", on_response)
        return html, extracted

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
    r = SESSION.get(url, headers=headers, timeout=timeout, allow_redirects=True)
    return r.status_code, r.text

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

        queue: List[Any] = []
        if isinstance(data, list):
            queue.extend(data)
        else:
            queue.append(data)

        while queue:
            item = queue.pop(0)
            if isinstance(item, dict) and "@graph" in item and isinstance(item["@graph"], list):
                queue.extend(item["@graph"])

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

        # location hints commonly present on event pages
        "locality": meta("og:locality"),
        "region": meta("og:region"),
        "country": meta("og:country-name"),
        "place": meta("og:place"),
        "street_address": meta("og:street-address") or meta("place:location:street_address"),
    }
    
def extract_main_text(soup: BeautifulSoup, limit: int = 600) -> Optional[str]:
    # Remove obvious non content
    for tag in soup.select("script, style, noscript, svg, header, nav, footer, form"):
        tag.decompose()

    # Prefer main content containers
    container = soup.select_one("main") or soup.select_one('[role="main"]') or soup.select_one(".content") or soup.body
    if not container:
        return None

    text = container.get_text(" ", strip=True)

    # Filter out common overlay noise
    noise_phrases = [
        "Press Option+1 for screen-reader mode",
        "Accessibility Screen-Reader Guide",
        "Feedback, and Issue Reporting",
    ]
    for phrase in noise_phrases:
        text = text.replace(phrase, "")

    text = re.sub(r"\s+", " ", text).strip()
    
    return text[:limit] if text else None

def format_address(addr: Any) -> Optional[str]:
    if isinstance(addr, str):
        return addr.strip() or None
    if isinstance(addr, dict):
        parts = []
        for k in ["streetAddress", "addressLocality", "addressRegion", "postalCode", "addressCountry"]:
            v = addr.get(k)
            if v:
                parts.append(str(v).strip())
        out = ", ".join([p for p in parts if p])
        return out or None
    return None

def normalize_iso_no_tz(s: Optional[str]) -> Optional[str]:
    if not s or not isinstance(s, str):
        return None
    try:
        dt = isoparse(s)
        dt = dt.replace(tzinfo=None, microsecond=0)
        return dt.isoformat()
    except Exception:
        return s.strip() or None

def clean_whitespace(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = s.replace("\xa0", " ")              # non breaking space
    s = re.sub(r"\s*\n+\s*", " ", s)        # turn newlines into single spaces
    s = re.sub(r"\s+", " ", s).strip()      # collapse repeated spaces
    return s or None

def to_naive_iso(dt) -> str:
    # if timezone aware, drop tzinfo but keep wall clock time
    if getattr(dt, "tzinfo", None) is not None:
        dt = dt.replace(tzinfo=None)
    return dt.replace(microsecond=0).isoformat()

def extract_start_date_from_text(text: str) -> str | None:
    if not text:
        return None

    cleaned = re.sub(r"\s+", " ", text).strip()

    hits = search_dates(
        cleaned,
        settings={
            "RETURN_AS_TIMEZONE_AWARE": True,   # allow parsing offsets if present
            "PREFER_DATES_FROM": "future",
        },
    )

    if not hits:
        return None

    for matched, dt in hits[:10]:
        ml = matched.lower()

        # ignore common non event dates
        if any(bad in ml for bad in ["updated", "posted", "copyright", "©"]):
            continue

        # basic guard: require month or numeric date to reduce false positives
        if not re.search(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|\d{1,2}/\d{1,2})", ml):
            continue

        return to_naive_iso(dt)

    return None

def _iter_dicts(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _iter_dicts(v)
    elif isinstance(obj, list):
        for it in obj:
            yield from _iter_dicts(it)
            

def extract_from_next_data_generic(soup: BeautifulSoup) -> Dict[str, Any]:
    tag = soup.find("script", attrs={"id": "__NEXT_DATA__", "type": "application/json"})
    if not tag:
        return {}

    raw = (tag.string or tag.get_text() or "").strip()
    if not raw:
        return {}

    try:
        data = json.loads(raw)
    except Exception:
        return {}

    event_obj = _find_first_event_like_object(data)
    if not event_obj:
        return {}

    return _normalize_event_like_object(event_obj)


def _find_first_event_like_object(obj: Any) -> Optional[Dict[str, Any]]:
    if isinstance(obj, dict):
        keys = set(obj.keys())

        has_title = ("name" in keys) or ("title" in keys)
        has_any_time = (
            ("startDate" in keys) or ("endDate" in keys)
            or ("start_date" in keys) or ("end_date" in keys)
            or ("start" in keys and "end" in keys)
        )
        has_location_hint = ("venue" in keys) or ("location" in keys)

        # require title plus either time or location, to reduce false hits
        if has_title and (has_any_time or has_location_hint):
            return obj

        for v in obj.values():
            hit = _find_first_event_like_object(v)
            if hit:
                return hit

    if isinstance(obj, list):
        for item in obj:
            hit = _find_first_event_like_object(item)
            if hit:
                return hit

    return None


def _normalize_event_like_object(e: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}

    out["title"] = _pick_first_str(e.get("name"), e.get("title"))
    out["description"] = _pick_first_str(e.get("summary"), e.get("description"))

    out["start_date"] = _pick_first_str(
        e.get("start_date"),
        e.get("startDate"),
        _nested_str_any(e, ["start", "utc"]),
        _nested_str_any(e, ["start", "local"]),
        _nested_str_any(e, ["start", "date"]),
        _nested_str_any(e, ["start", "date_time"]),
    )
    out["end_date"] = _pick_first_str(
        e.get("end_date"),
        e.get("endDate"),
        _nested_str_any(e, ["end", "utc"]),
        _nested_str_any(e, ["end", "local"]),
        _nested_str_any(e, ["end", "date"]),
        _nested_str_any(e, ["end", "date_time"]),
    )

    venue = e.get("venue") or e.get("location") or e.get("venue_data") or {}
    out["location"] = _stringify_location_generic(venue)

    img = e.get("image") or e.get("logo") or e.get("primary_image") or e.get("image_url")
    if isinstance(img, dict):
        out["image"] = _pick_first_str(img.get("url"), img.get("original"), img.get("crop_mask"))
    elif isinstance(img, str):
        out["image"] = img.strip() or None

    return out


def _stringify_location_generic(venue: Any) -> Optional[str]:
    if isinstance(venue, str):
        return clean_whitespace(venue)

    if not isinstance(venue, dict):
        return None

    parts: List[str] = []

    name = venue.get("name")
    if isinstance(name, str) and name.strip():
        parts.append(name.strip())

    address = venue.get("address") or venue.get("address_display") or venue.get("venue_address") or {}
    if isinstance(address, str) and address.strip():
        parts.append(clean_whitespace(address))
    elif isinstance(address, dict):
        for k in ["address_1", "address_2", "city", "region", "postal_code", "country"]:
            v = address.get(k)
            if isinstance(v, str) and v.strip():
                parts.append(v.strip())

        # schema org style keys
        for k in ["streetAddress", "addressLocality", "addressRegion", "postalCode", "addressCountry"]:
            v = address.get(k)
            if isinstance(v, str) and v.strip():
                parts.append(v.strip())

    if parts:
        return ", ".join(parts)

    return None


def _pick_first_str(*values: Any) -> Optional[str]:
    for v in values:
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _nested_str_any(obj: Dict[str, Any], path: List[str]) -> Optional[str]:
    cur: Any = obj
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    if isinstance(cur, str) and cur.strip():
        return cur.strip()
    return None

def extract_structured_location(html: str, url: str) -> str | None:
    base_url = get_base_url(html, url)
    data = extruct.extract(html, base_url=base_url, syntaxes=["json-ld", "microdata", "rdfa"])

    for item in data.get("json-ld", []) or []:
        for obj in _iter_dicts(item):
            t = obj.get("@type")
            types = t if isinstance(t, list) else [t]
            if not any(str(x).lower() == "event" for x in types if x):
                continue

            loc = obj.get("location")
            if isinstance(loc, dict):
                name = loc.get("name")
                addr = loc.get("address")

                if name and str(name).strip():
                    return str(name).strip()[:80]

                if isinstance(addr, dict):
                    parts = [addr.get(k) for k in ["streetAddress", "addressLocality", "addressRegion"]]
                    parts = [str(p).strip() for p in parts if p]
                    if parts:
                        return ", ".join(parts)[:80]

    return None

def parse_event(url: str, pw: PlaywrightSession) -> EventCard:

    # ----------------------------
    # FETCH STRATEGY
    # ----------------------------
    has_fragment = bool(urlsplit(url).fragment)
    status, req_html = fetch_with_requests(url)

    if has_fragment or status in (403, 429) or looks_blocked(req_html):
        html, extracted = pw.fetch(url)
        source = "playwright"
    else:
        html, extracted = req_html, None
        source = "requests"

    soup = BeautifulSoup(html, "html.parser")
    card = EventCard(url=url, source=source)


    # ----------------------------
    # NETWORK JSON (Playwright adapters)
    # ----------------------------
    if extracted:
        card.title = extracted.get("title") or card.title
        card.description = extracted.get("description") or card.description
        card.start_date = extracted.get("start_date") or card.start_date
        card.end_date = extracted.get("end_date") or card.end_date
        card.location = extracted.get("location") or card.location
        card.image = extracted.get("image") or card.image

    card.start_date = normalize_iso_no_tz(card.start_date)
    card.end_date = normalize_iso_no_tz(card.end_date)


    # ----------------------------
    # JSON LD STRUCTURED DATA
    # ----------------------------
    ld = extract_from_ld_json(soup)

    if ld:
        card.title = card.title or ld.get("name")
        card.description = card.description or ld.get("description")
        card.start_date = card.start_date or ld.get("startDate")
        card.end_date = card.end_date or ld.get("endDate")

        card.start_date = normalize_iso_no_tz(card.start_date)
        card.end_date = normalize_iso_no_tz(card.end_date)

        loc = ld.get("location")
        if isinstance(loc, dict):
            card.location = card.location or loc.get("name") or format_address(loc.get("address"))
        elif isinstance(loc, list) and loc and isinstance(loc[0], dict):
            card.location = card.location or loc[0].get("name") or format_address(loc[0].get("address"))

        img = ld.get("image")
        if isinstance(img, str):
            card.image = card.image or img
        elif isinstance(img, list) and img:
            card.image = card.image or (img[0] if isinstance(img[0], str) else None)


    # ----------------------------
    # NEXT DATA (React / __NEXT_DATA__)
    # ----------------------------
    nx = extract_from_next_data_generic(soup)

    if nx:
        card.title = card.title or nx.get("title")
        card.description = card.description or nx.get("description")
        card.start_date = card.start_date or nx.get("start_date")
        card.end_date = card.end_date or nx.get("end_date")
        card.location = card.location or nx.get("location")
        card.image = card.image or nx.get("image")

        card.start_date = normalize_iso_no_tz(card.start_date)
        card.end_date = normalize_iso_no_tz(card.end_date)


    # ----------------------------
    # OPEN GRAPH + BASIC TITLE FALLBACK
    # ----------------------------
    og = extract_open_graph(soup)

    if not card.title:
        el = soup.select_one("[itemprop='name']")
        if el:
            txt = el.get("content") if el.name == "meta" else el.get_text(" ", strip=True)
            card.title = txt if txt else None

    card.title = card.title or og.get("title") or (soup.title.get_text(strip=True) if soup.title else None)
    card.description = card.description or og.get("description")
    card.image = card.image or og.get("image")


    # ----------------------------
    # MAIN CONTENT TEXT
    # ----------------------------
    main = extract_main_text(soup, limit=600)

    if not card.description and main:
        card.description = main[:280]

    fallback_text = card.description or main or ""


    # ----------------------------
    # STRUCTURAL DATE TAGS
    # ----------------------------
    if not card.start_date:
        t = soup.select_one("time[datetime]")
        if t and t.get("datetime"):
            card.start_date = normalize_iso_no_tz(t["datetime"].strip())


    # ----------------------------
    # ARIA LABEL DATE (Timely style calendars)
    # ----------------------------
    if not card.start_date:
        for el in soup.select("[aria-label]"):
            aria = (el.get("aria-label") or "").strip()
            if 0 < len(aria) <= 80 and any(
                m in aria.lower()
                for m in ["jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec"]
            ):
                dt = extract_start_date_from_text(aria)
                if dt:
                    card.start_date = dt
                    break


    # ----------------------------
    # TEXT DATE FALLBACK
    # ----------------------------
    if not card.start_date:
        card.start_date = extract_start_date_from_text(fallback_text)


    # ----------------------------
    # STRUCTURAL LOCATION TAGS
    # ----------------------------
    if not card.location:
        el = soup.select_one("[itemprop='location'], [itemprop='address']")
        if el:
            txt = el.get_text(" ", strip=True)
            card.location = txt if txt else None

    if not card.location:
        el = soup.select_one("[data-testid*='location' i], [data-testid*='venue' i], [data-testid*='address' i]")
        if el:
            txt = el.get_text(" ", strip=True)
            card.location = txt if txt else None

    if not card.location:
        parts = []
        if og.get("place"):
            parts.append(og["place"])
        if og.get("street_address"):
            parts.append(og["street_address"])
        if og.get("locality"):
            parts.append(og["locality"])
        if og.get("region"):
            parts.append(og["region"])
        if og.get("country"):
            parts.append(og["country"])
        if parts:
            card.location = ", ".join([p for p in parts if p])


    # ----------------------------
    # FINAL STRUCTURED LOCATION FALLBACK (extruct)
    # ----------------------------
    if not card.location:
        card.location = extract_structured_location(html, url)


    # ----------------------------
    # CLEANUP + NORMALIZATION
    # ----------------------------
    for f in ["title", "description", "start_date", "end_date", "location", "image"]:
        v = getattr(card, f)
        if isinstance(v, str):
            v2 = v.strip()
            setattr(card, f, v2 if v2 else None)

    if card.title:
        card.title = html_lib.unescape(card.title)

    if card.description:
        card.description = html_lib.unescape(card.description)
        card.description = card.description.replace("\\n", " ")
        card.description = BeautifulSoup(card.description, "html.parser").get_text(" ", strip=True)

    if card.location:
        card.location = html_lib.unescape(card.location)

    if card.image and card.image.startswith("/"):
        card.image = urljoin(card.url, card.image)

    card.description = clean_whitespace(card.description)
    card.title = clean_whitespace(card.title)
    card.location = clean_whitespace(card.location)

    if card.title and any(k in card.title.lower() for k in BLOCKED_KEYWORDS):
        card.source = "blocked"

    return card