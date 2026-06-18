from __future__ import annotations

import html
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator

import feedparser
import requests

log = logging.getLogger(__name__)


def _clean_title(s: str | None) -> str:
    """Decode HTML entities and collapse whitespace in feed-provided titles."""
    if not s:
        return ""
    return " ".join(html.unescape(s).split())


@dataclass
class RawItem:
    source: str
    url: str
    title: str
    # Date the source surfaced this item (HN submission for HN, feed pub for
    # RSS). Used for window filtering. The article's *own* publication date
    # comes from extract.py via trafilatura metadata.
    surfaced: str | None = None  # ISO date "YYYY-MM-DD" or None


# Algolia HN search. We filter by submission date server-side, then re-sort by
# points and truncate to `limit` client-side.
#
# NOTE: `points` is NOT in HN's `numericAttributesForFiltering`, so passing it in
# `numericFilters` makes Algolia return HTTP 400 ("invalid numeric attribute").
# Only `created_at_i` (and `num_comments`) are filterable server-side, so the
# `min_points` threshold is applied client-side below.
_HN_SEARCH = "https://hn.algolia.com/api/v1/search"


def fetch_hn(
    source_name: str = "Hacker News",
    limit: int = 10,
    since_hours: int = 48,
    min_points: int = 50,
) -> Iterator[RawItem]:
    since = int(time.time() - since_hours * 3600)
    params = {
        "tags": "story",
        "numericFilters": f"created_at_i>{since}",
        "hitsPerPage": 100,
    }
    try:
        r = requests.get(_HN_SEARCH, params=params, timeout=15)
        r.raise_for_status()
        hits = r.json().get("hits", [])
    except Exception as e:
        # Don't let an HN outage take down the whole ingest cycle — log and
        # yield nothing so the other sources still run.
        log.warning("fetch_hn failed, skipping Hacker News this cycle: %s", e)
        return

    # `points` can't be filtered server-side (see note above), so drop
    # below-threshold stories here, then keep the top `limit` by points.
    hits = [h for h in hits if (h.get("points") or 0) > min_points]
    hits.sort(key=lambda h: h.get("points", 0), reverse=True)

    for h in hits[:limit]:
        title = _clean_title(h.get("title"))
        if not title:
            continue
        url = h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}"
        ts = h.get("created_at_i")
        surfaced = (
            datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
            if ts else None
        )
        yield RawItem(source=source_name, url=url, title=title, surfaced=surfaced)


def fetch_wikipedia_events(
    source_name: str = "World news",
    days_back: int = 1,
) -> Iterator[RawItem]:
    """Yield one item per day of Wikipedia's Portal:Current_events.

    days_back=1 → just today. Increase to backfill recent days.
    """
    from datetime import date as _date, timedelta as _td
    from .wiki import current_events_url, current_events_title

    today = _date.today()
    for delta in range(days_back):
        day = today - _td(days=delta)
        yield RawItem(
            source=source_name,
            url=current_events_url(day),
            title=current_events_title(day),
            surfaced=day.isoformat(),
        )


def fetch_rss(source_name: str, feed_url: str, limit: int = 20) -> Iterator[RawItem]:
    d = feedparser.parse(feed_url)
    for entry in d.entries[:limit]:
        url = getattr(entry, "link", None)
        title = _clean_title(getattr(entry, "title", None))
        if not url or not title:
            continue
        parsed = (
            getattr(entry, "published_parsed", None)
            or getattr(entry, "updated_parsed", None)
        )
        surfaced = time.strftime("%Y-%m-%d", parsed) if parsed else None
        yield RawItem(source=source_name, url=url, title=title, surfaced=surfaced)
