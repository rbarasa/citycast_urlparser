import html as html_lib
import re
from typing import Any, Dict, Optional

_TIMELY_SLUG_RE = re.compile(r"calendar\.time\.ly/api/calendars/\d+/events/slug", re.I)
_TAG_RE = re.compile(r"<[^>]+>")

def match_response(url: str, content_type: str) -> bool:
    if "json" not in (content_type or "").lower():
        return False
    return bool(_TIMELY_SLUG_RE.search(url))

def _clean_text(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = html_lib.unescape(s)
    s = _TAG_RE.sub("", s)
    return " ".join(s.split()).strip() or None

def extract_event(payload: Dict[str, Any]) -> Dict[str, Optional[str]]:
    d = (payload or {}).get("data") or {}
    return {
        "title": _clean_text(d.get("title")),
        "description": _clean_text(d.get("description_short")),
    }
