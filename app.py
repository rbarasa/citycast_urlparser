import re
from dataclasses import asdict
from typing import List, Dict, Any, Optional

import pandas as pd
import streamlit as st

from url_parser import PlaywrightSession, parse_event, EventCard

REQUIRED_FIELDS = ["title", "description"]

OPTIONAL_FIELDS = ["start_date", "end_date", "location"]

REVIEW_KEYWORDS = [
    "error", "404", "404 error", "page not found", "not found",
    "access denied", "request could not be satisfied",
    "just a moment", "verify", "identity", "captcha",
    "cloudfront", "akamai", "bot detection", "forbidden", "403",
]

TAG_RULES = {
    "18+": ["18+", "adults only", "age restriction"],
    "21+": ["21+", "alcohol", "bar", "nightclub", "strip club", "casino", "club"],
    "All Ages": ["all ages", "family-friendly", "kids", "children"],
    "Free": ["free entry", "free event", "free admission", "no cover", "free to attend"],
    "Ticketed": ["ticket", "tickets", "rsvp", "admission required", "buy", "purchase", "register", "fee", "pay", "donation", "price", "cost"],
}

PASTEL = {
    "ok": "#d9f2e1",       # soft green
    "check": "#fff3c4",    # soft yellow
    "review": "#ffd6d6",   # soft red
}

def classify_row(row: pd.Series) -> str:
    source = str(row.get("source", "")).lower()

    # hard rule: blocked pages
    if source == "blocked":
        return "review"

    title = str(row.get("title") or "")
    desc = str(row.get("description") or "")
    blob = f"{title} {desc}".lower()

    # hard rule: obvious error pages
    if any(k in blob for k in REVIEW_KEYWORDS):
        return "review"

    required_missing = [f for f in REQUIRED_FIELDS if not row.get(f)]
    optional_missing = [f for f in OPTIONAL_FIELDS if not row.get(f)]

    # if both required fields missing -> review
    if len(required_missing) == len(REQUIRED_FIELDS):
        return "review"

    # if required fields are filled
    if len(required_missing) == 0:
        # if all optional missing -> check
        if len(optional_missing) == len(OPTIONAL_FIELDS):
            return "check"
        # at least one optional present -> ok
        return "ok"

    # one required missing -> check
    return "check"


def infer_tags_from_row(row: pd.Series) -> List[str]:
    description = str(row.get("description") or "").lower()
    tags = []

    for tag, keywords in TAG_RULES.items():
        if any(keyword in description for keyword in keywords):
            tags.append(tag)

    return tags

def style_by_quality(df: pd.DataFrame):
    def row_style(row: pd.Series):
        color = PASTEL.get(row["quality"], "#ffffff")
        return [f"background-color: {color}"] * len(row)

    return df.style.apply(row_style, axis=1)

DAY_HEADER_RE = re.compile(
    r"^(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s*,\s*"
    r"(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2}\s*$",
    re.IGNORECASE,
)

URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)

def get_playwright_session():
    return PlaywrightSession(headless=True)

def parse_pasted_text(text: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    current_day: Optional[str] = None
    pending_notes: List[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if DAY_HEADER_RE.match(line):
            current_day = line
            pending_notes = []
            continue

        if URL_RE.match(line):
            note = " ".join(pending_notes).strip() if pending_notes else None
            pending_notes = []
            rows.append({"date_label": current_day, "url": line, "note": note})
            continue

        # any non url line under a day becomes a note that applies to the next url
        pending_notes.append(line)

    return rows


def card_to_row(card: EventCard, date_label: Optional[str]) -> Dict[str, Any]:
    d = asdict(card)
    d["date_label"] = date_label
    return d


st.set_page_config(page_title="Event Link Dropbox", layout="wide")
st.title("Event Link Dropbox")

st.write(
    "Paste your schedule of events below, then click 'parse and fetch event data' "
    "to extract context for each event."
)

if "text" not in st.session_state:
    st.session_state["text"] = ""

if "items" not in st.session_state:
    st.session_state["items"] = []

if "df" not in st.session_state:
    st.session_state["df"] = None

text = st.text_area(
    "Paste links",
    key="text",
    placeholder="Monday, December 1\nhttps://...\nhttps://...\n\nTuesday, December 2\nhttps://...\n",
    height=300,
)

col1, col2 = st.columns([1, 1])

with col1:
    do_parse = st.button("Parse list")

with col2:
    do_fetch = st.button("Parse and fetch event data")

if do_parse or do_fetch:
    items = parse_pasted_text(st.session_state["text"])
    st.session_state["items"] = items

    if not items:
        st.warning("no urls found")
    else:
        st.success(f"found {len(items)} url(s)")
        st.dataframe(pd.DataFrame(items), width='stretch')

if do_fetch:
    items = st.session_state["items"] or parse_pasted_text(st.session_state["text"])

    out_rows: List[Dict[str, Any]] = []
    progress = st.progress(0)

    pw = get_playwright_session()
    try:
        for i, item in enumerate(items):
            url = item["url"]
            date_label = item.get("date_label")

            try:
                card = parse_event(url, pw)
                out_rows.append(card_to_row(card, date_label))
            except Exception as e:
                out_rows.append(
                    {
                        "url": url,
                        "date_label": date_label,
                        "title": None,
                        "description": None,
                        "start_date": None,
                        "end_date": None,
                        "location": None,
                        "image": None,
                        "source": None,
                        "error": str(e),
                    }
                )

            progress.progress((i + 1) / len(items))
    finally:
        pw.close()

    df = pd.DataFrame(out_rows)
    df["quality"] = df.apply(classify_row, axis=1)
    df["tags"] = df.apply(infer_tags_from_row, axis=1)
    df = df.drop(columns=["image"], errors="ignore")

    st.session_state.df = df

df = st.session_state.df
if df is not None:
    st.subheader("results")
    st.dataframe(style_by_quality(df), width='stretch')

    csv_bytes = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "download csv",
        data=csv_bytes,
        file_name="events.csv",
        mime="text/csv",
    )
