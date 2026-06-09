"""
Feed ingestion endpoint.

Called by n8n to fetch, parse, and store an RSS feed.
One HTTP Request node per feed URL — pass the URL in the request body.
Auth: X-API-Key header (same ALEXA_API_KEY used by other n8n endpoints).

Endpoint
--------
POST /n8n/ingest-feed
    Body: {"feed_url": "https://..."}
    Fetches the RSS, upserts articles, runs the cleanup policy.
    Returns counts of inserted/updated/deleted rows.
"""

import logging

import feedparser
import httpx
from dateutil import parser as dateutil_parser
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.db import cleanup_old_articles, get_conn, upsert_articles
from app.dependencies import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/n8n", tags=["n8n"])


class IngestFeedRequest(BaseModel):
    feed_url:       str
    cleanup_months: int = 3   # how far back to retain articles


@router.post("/ingest-feed")
async def ingest_feed(
    body: IngestFeedRequest,
    user: str = Depends(require_auth),
):
    """
    Fetch an RSS feed and upsert its entries into the articles table.

    Redirects are followed automatically (handles sites like Daily Mail that
    move their feed URLs between domains).
    """
    # ── Fetch RSS ────────────────────────────────────────────────────────────
    try:
        resp = httpx.get(
            body.feed_url,
            timeout=15,
            follow_redirects=True,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("Feed fetch failed for %s: %s", body.feed_url, exc)
        raise HTTPException(status_code=502, detail=f"Feed fetch failed: {exc}")

    feed = feedparser.parse(resp.text)

    if not feed.entries:
        logger.warning("No entries found in feed: %s", body.feed_url)
        return JSONResponse({
            "feed_url": body.feed_url,
            "inserted": 0,
            "updated":  0,
            "deleted":  0,
            "total_in_feed": 0,
        })

    # ── Build rows ───────────────────────────────────────────────────────────
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

    # ── Upsert + cleanup ─────────────────────────────────────────────────────
    conn = get_conn()
    try:
        counts  = upsert_articles(conn, rows)
        deleted = cleanup_old_articles(conn, months=body.cleanup_months)
        conn.commit()
    finally:
        conn.close()

    return JSONResponse({
        "feed_url":      body.feed_url,
        "inserted":      counts["inserted"],
        "updated":       counts["updated"],
        "deleted":       deleted,
        "total_in_feed": len(rows),
    })
