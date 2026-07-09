"""Location enrichment: Google place details + the place's own website + a
general web search, with opening hours as the top-priority output.

Three legs that fail independently: place details are the backbone (errors
propagate), while website and web-search failures degrade to per-leg error
fields instead of breaking the tool.
"""

import logging
import re
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup
from ddgs import DDGS
from pydantic import BaseModel, Field, model_validator

from client import fetch_place_details, resolve_place_id
from cost_control import cached_and_throttled

logger = logging.getLogger(__name__)

MAX_WEBSITE_BYTES = 2 * 1024 * 1024
TEXT_EXCERPT_CHARS = 5000
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

WEEK_MINUTES = 7 * 24 * 60

# Lines that look like opening hours: any clock time, or an open/closed/hours
# word combined with a day word ("Closed Tuesdays", "Hours: daily").
_TIME_RE = re.compile(r"(?i)\b\d{1,2}(:\d{2})?\s*(am|pm|a\.m\.|p\.m\.)|\b\d{1,2}:\d{2}\b"
                      r"|\bmidnight\b|\bnoon\b")
_DAY_RE = re.compile(r"(?i)\b(mon|tues?|wed(nes)?|thur?s?|fri|sat(ur)?|sun)(day)?s?\b"
                     r"|\bdaily\b|\bweekends?\b|\bevery day\b")
_STATUS_RE = re.compile(r"(?i)\b(hours?|open(s|ed)?|clos(ed|es|ing))\b")


class EnrichLocationRequest(BaseModel):
    place_id: str | None = None
    name: str | None = None
    address: str | None = Field(default=None, description="Narrows name resolution")
    visit_time: str | None = Field(
        default=None, description="ISO 8601 datetime, local time at the place")
    focus: str | None = Field(
        default=None,
        description="Free text: what extra info matters (price range, menu, vibe, ...)")
    max_search_results: int = Field(default=5, ge=1, le=10)

    @model_validator(mode="after")
    def require_identity(self):
        if not self.place_id and not self.name:
            raise ValueError("Provide place_id, or name (optionally with address)")
        return self


def extract_hours_snippets(text: str, limit: int = 10) -> list[str]:
    snippets = []
    for line in text.splitlines():
        line = line.strip()
        if not line or len(line) > 200 or line in snippets:
            continue
        if _TIME_RE.search(line) or (_STATUS_RE.search(line) and _DAY_RE.search(line)):
            snippets.append(line)
            if len(snippets) >= limit:
                break
    return snippets


def _parse_visit(visit_time: str, utc_offset_minutes: int | None) -> datetime:
    dt = datetime.fromisoformat(visit_time)
    if dt.tzinfo is not None:
        if utc_offset_minutes is not None:
            dt = dt.astimezone(timezone(timedelta(minutes=utc_offset_minutes)))
        dt = dt.replace(tzinfo=None)
    return dt


def _week_minutes(day: int, hour: int, minute: int) -> int:
    return day * 1440 + hour * 60 + minute


def _current_hours_verdict(current_hours: dict, visit: datetime) -> dict | None:
    """Judge from date-stamped periods (next ~7 days, includes holiday overrides).
    Returns None when the visit falls outside the dates these periods cover."""
    intervals = []
    for period in current_hours.get("periods", []):
        opening, closing = period.get("open"), period.get("close")
        if not opening or "date" not in opening:
            continue
        o_date = opening["date"]
        start = datetime(o_date["year"], o_date["month"], o_date["day"],
                         opening.get("hour", 0), opening.get("minute", 0))
        end = None
        if closing and "date" in closing:
            c_date = closing["date"]
            end = datetime(c_date["year"], c_date["month"], c_date["day"],
                           closing.get("hour", 0), closing.get("minute", 0))
        intervals.append((start, end))

    if not intervals:
        return None
    window_start = min(start.date() for start, _ in intervals)
    window_end = max((end or start).date() for start, end in intervals)
    if not (window_start <= visit.date() <= window_end):
        return None

    for start, end in intervals:
        if start <= visit and (end is None or visit < end):
            return {"verdict": "open", "source": "current_opening_hours",
                    "detail": f"open {start.isoformat(' ')} to "
                              f"{end.isoformat(' ') if end else 'open-ended'}"}
    return {"verdict": "closed", "source": "current_opening_hours",
            "detail": f"no opening period on {visit.date().isoformat()} covers "
                      f"{visit.strftime('%H:%M')} (holiday-adjusted hours)"}


def _regular_hours_verdict(periods: list, visit: datetime) -> dict | None:
    if not periods:
        return None
    google_day = (visit.weekday() + 1) % 7  # python Mon=0 -> Places API Sun=0
    visit_min = _week_minutes(google_day, visit.hour, visit.minute)

    for period in periods:
        opening, closing = period["open"], period.get("close")
        if closing is None:  # Places API convention for always-open
            return {"verdict": "open", "source": "regular_opening_hours",
                    "detail": "open 24/7"}
        start = _week_minutes(opening["day"], opening.get("hour", 0), opening.get("minute", 0))
        end = _week_minutes(closing["day"], closing.get("hour", 0), closing.get("minute", 0))
        if end <= start:  # wraps past Saturday midnight
            end += WEEK_MINUTES
        if start <= visit_min < end or start <= visit_min + WEEK_MINUTES < end:
            return {"verdict": "open", "source": "regular_opening_hours",
                    "detail": f"weekly period day {opening['day']} "
                              f"{opening.get('hour', 0):02d}:{opening.get('minute', 0):02d}"
                              f"-day {closing['day']} "
                              f"{closing.get('hour', 0):02d}:{closing.get('minute', 0):02d} covers the visit"}
    return {"verdict": "closed", "source": "regular_opening_hours",
            "detail": f"no weekly opening period covers {visit.strftime('%A %H:%M')}"}


def hours_verdict(visit_time: str, regular_periods: list | None = None,
                  current_hours: dict | None = None,
                  utc_offset_minutes: int | None = None) -> dict:
    """Deterministic open/closed/unknown verdict for a visit time (local to the
    place; tz-aware inputs are converted using the place's UTC offset)."""
    visit = _parse_visit(visit_time, utc_offset_minutes)
    verdict = None
    if current_hours:
        verdict = _current_hours_verdict(current_hours, visit)
    if verdict is None:
        verdict = _regular_hours_verdict(regular_periods or [], visit)
    if verdict is None:
        verdict = {"verdict": "unknown", "source": None,
                   "detail": "Google lists no structured opening hours; check the "
                             "website evidence and web search results instead"}
    verdict["visit_time"] = visit.isoformat()
    return verdict


@cached_and_throttled(ttl_seconds=3600, min_interval_seconds=1.0)
def fetch_website_extract(url: str) -> dict:
    resp = requests.get(url, timeout=10, stream=True,
                        headers={"User-Agent": USER_AGENT, "Accept-Language": "en"})
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "")
    if content_type and "html" not in content_type:
        raise ValueError(f"not an HTML page: {content_type}")

    chunks, total = [], 0
    for chunk in resp.iter_content(65536):
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_WEBSITE_BYTES:
            break

    soup = BeautifulSoup(b"".join(chunks), "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "template"]):
        tag.decompose()
    title = soup.title.get_text(strip=True) if soup.title else None
    meta = (soup.find("meta", attrs={"name": "description"})
            or soup.find("meta", attrs={"property": "og:description"}))
    lines = [line.strip() for line in soup.get_text("\n").splitlines() if line.strip()]
    text = "\n".join(lines)

    return {
        "url": url,
        "title": title,
        "meta_description": meta.get("content") if meta else None,
        "hours_snippets": extract_hours_snippets(text),
        "text_excerpt": text[:TEXT_EXCERPT_CHARS],
    }


@cached_and_throttled(ttl_seconds=3600, min_interval_seconds=1.0)
def web_search(query: str, max_results: int = 5) -> list[dict]:
    results = DDGS().text(query, max_results=max_results)
    return [{"title": r.get("title"), "url": r.get("href"), "snippet": r.get("body")}
            for r in results]


def enrich_location_data(request: EnrichLocationRequest) -> dict:
    place_id = request.place_id or resolve_place_id(
        " ".join(filter(None, [request.name, request.address])))
    details = fetch_place_details(place_id)

    website = None
    if details.website:
        try:
            website = fetch_website_extract(details.website)
        except Exception as e:
            logger.warning("Website fetch failed for %s: %s", details.website, e)
            website = {"url": details.website, "error": f"could not fetch website: {e}"}
    else:
        website = {"error": "Google lists no website for this place"}

    # dict order is the priority order: hours lead
    result: dict = {
        "hours": {
            "weekday_descriptions": details.opening_hours,
            "periods": details.opening_periods,
            "current": details.current_opening_hours,
            "utc_offset_minutes": details.utc_offset_minutes,
        },
    }
    if request.visit_time:
        try:
            verdict = hours_verdict(request.visit_time, details.opening_periods,
                                    details.current_opening_hours,
                                    details.utc_offset_minutes)
        except ValueError as e:
            verdict = {"verdict": "unknown", "detail": f"could not parse visit_time: {e}"}
        verdict["website_evidence"] = website.get("hours_snippets", [])
        result["open_at_visit_time"] = verdict

    result["place"] = details.model_dump()
    result["website"] = website

    query = " ".join(filter(None, [details.name, details.formatted_address, request.focus]))
    try:
        result["web_search"] = web_search(query, request.max_search_results)
    except Exception as e:
        logger.warning("Web search failed for %r: %s", query, e)
        result["web_search"] = {"error": f"web search unavailable: {e}"}

    if request.focus:
        result["focus"] = request.focus
    return result
