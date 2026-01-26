import html as html_lib
import re
from typing import Any, Dict, Optional

_TIMELY_SLUG_RE = re.compile(r"calendar\.time\.ly/api/calendars/\d+/events/slug", re.I)

_TAG_RE = re.compile(r"<[^>]+>")

def is_timely_slug_response(url: str) -> bool:
    return bool(_TIMELY_SLUG_RE.search(url))

def _clean_text(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = html_lib.unescape(s)
    s = _TAG_RE.sub("", s)
    return " ".join(s.split()).strip() or None

def extract_title_and_description(payload: Dict[str, Any]) -> Dict[str, Optional[str]]:
    d = (payload or {}).get("data") or {}

    title = d.get("title")
    description_short = d.get("description_short")

    return {
        "title": _clean_text(title),
        "description": _clean_text(description_short),
    }
