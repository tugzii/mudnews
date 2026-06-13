"""
Feed ingestion endpoints.

POST /n8n/ingest-feed
    Called by n8n (or any API-key caller) to fetch, parse, and store one
    RSS feed. Pass the feed URL in the request body.
    Auth: X-API-Key header (same ALEXA_API_KEY used by other n8n endpoints).

POST /mudnews/capture-feeds
    Called from the web UI ("Capture" button). Fetches both Daily Mail
    feeds (UK + AU) and upserts their entries, exactly like the old n8n
    "RSS Feed Scraper" schedule trigger did — just on demand instead of
    on a timer.
    Auth: Bearer session token (web UI login).
"""

import logging

import feedparser
import httpx
from dateutil import parser as dateutil_parser
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.auth import require_session
from app.db import cleanup_old_articles, get_conn, upsert_articles
from app.dependencies import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/n8n", tags=["n8n"])
capture_router = APIRouter(prefix="/mudnews", tags=["capture"])


# ---------------------------------------------------------------------------
# Feed definitions
# ---------------------------------------------------------------------------
# These are the same two feeds the old n8n "RSS Feed Scraper" schedule
# trigger fanned out to (one ingest-feed HTTP node per feed).
DAILY_MAIL_FEEDS = [
    {"source_label": "UK", "feed_url": "https://www.dailymail.com/home/index.rss"},
    {"source_label": "AU", "feed_url": "https://www.dailymail.com/auhome/index.rss"},
]

DEFAULT_CLEANUP_MONTHS = 3


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _fetch_feed_rows(feed_url: str) -> list[tuple]:
    """
    Fetch an RSS feed and return a list of
    (title, description, url, published_at) tuples.

    Redirects are followed automatically (handles sites like Daily Mail that
    move their feed URLs between domains).

    Raises HTTPException(502) on fetch failure.
    """
    try:
        resp = httpx.get(
            feed_url,
            timeout=15,
            follow_redirects=True,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("Feed fetch failed for %s: %s", feed_url, exc)
        raise HTTPException(status_code=502, detail=f"Feed fetch failed: {exc}")

    feed = feedparser.parse(resp.text)

    rows = []
    for entry in feed.entries:
        pub_str = entry.get("published")
        try:
            published_at = dateutil_parser.parse(pub_str) if pub_str else None
        except Exception:
            published_at = None

        rows.append((
            entry.get("title"),
            entry.get("summary"),
            entry.get("link"),
            published_at,
        ))

    return rows


def _ingest_rows(conn, rows: list[tuple], source_label: str,
                 cleanup_months: int = DEFAULT_CLEANUP_MONTHS) -> dict:
    """Upsert already-parsed rows + run the cleanup policy. Commits."""
    counts  = upsert_articles(conn, rows, source_feed=source_label)
    deleted = cleanup_old_articles(conn, months=cleanup_months)
    conn.commit()
    return {
        "inserted":      counts["inserted"],
        "updated":       counts["updated"],
        "deleted":       deleted,
        "total_in_feed": len(rows),
    }


# ---------------------------------------------------------------------------
# POST /n8n/ingest-feed
# ---------------------------------------------------------------------------
class IngestFeedRequest(BaseModel):
    feed_url:       str
    source_label:   str = ""  # e.g. "AU", "UK" — tags articles for source filtering
    cleanup_months: int = DEFAULT_CLEANUP_MONTHS


@router.post("/ingest-feed")
async def ingest_feed(
    body: IngestFeedRequest,
    user: str = Depends(require_auth),
):
    """
    Fetch an RSS feed and upsert its entries into the articles table.
    """
    rows = _fetch_feed_rows(body.feed_url)

    if not rows:
        logger.warning("No entries found in feed: %s", body.feed_url)
        return JSONResponse({
            "feed_url": body.feed_url,
            "inserted": 0,
            "updated":  0,
            "deleted":  0,
            "total_in_feed": 0,
        })

    conn = get_conn()
    try:
        result = _ingest_rows(conn, rows, body.source_label, body.cleanup_months)
    finally:
        conn.close()

    return JSONResponse({"feed_url": body.feed_url, **result})


# ---------------------------------------------------------------------------
# POST /mudnews/capture-feeds
# ---------------------------------------------------------------------------
@capture_router.post("/capture-feeds")
async def capture_feeds(
    user: str = Depends(require_session),
):
    """
    On-demand replacement for the n8n "RSS Feed Scraper" schedule trigger.

    Fetches both Daily Mail feeds (UK + AU), upserts new/changed articles,
    and runs the cleanup policy. Triggered by the "Capture" button in the
    web UI.
    """
    results = []

    for feed in DAILY_MAIL_FEEDS:
        source_label = feed["source_label"]
        feed_url     = feed["feed_url"]

        try:
            rows = _fetch_feed_rows(feed_url)
        except HTTPException as exc:
            logger.error("capture-feeds: %s feed failed — %s", source_label, exc.detail)
            results.append({
                "source_label": source_label,
                "feed_url":      feed_url,
                "error":         str(exc.detail),
            })
            continue

        if not rows:
            results.append({
                "source_label":  source_label,
                "feed_url":      feed_url,
                "inserted":      0,
                "updated":       0,
                "deleted":       0,
                "total_in_feed": 0,
            })
            continue

        conn = get_conn()
        try:
            result = _ingest_rows(conn, rows, source_label)
        finally:
            conn.close()

        results.append({
            "source_label": source_label,
            "feed_url":     feed_url,
            **result,
        })

    total_inserted = sum(r.get("inserted", 0) for r in results)
    total_updated  = sum(r.get("updated", 0) for r in results)
    total_errors   = sum(1 for r in results if "error" in r)

    logger.info(
        "capture-feeds: user=%s inserted=%d updated=%d errors=%d",
        user, total_inserted, total_updated, total_errors,
    )

    return JSONResponse({
        "status":         "ok" if total_errors == 0 else "partial",
        "results":        results,
        "total_inserted": total_inserted,
        "total_updated":  total_updated,
    })
