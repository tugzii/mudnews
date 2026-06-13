"""
n8n integration endpoints for mudnews.

No summarisation — articles are scored and content is fetched, then Alexa
reads the full text on demand with pagination.

Endpoints
  GET  /n8n/unscored-articles          → articles needing AI scoring
  POST /n8n/import-score               → save AI score for an article (legacy per-article)
  POST /n8n/score-batch                → fetch + score all unscored articles via Gemini (batched)
  POST /n8n/fetch-article-content      → fetch + store full text for one article
  GET  /n8n/articles-needing-content   → scored articles without full_content yet
"""

import asyncio
import logging
from itertools import groupby
from operator import itemgetter

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.auth import require_session
from app.dependencies import require_auth
from app.db import (
    get_conn, get_unscored_articles, insert_article_score,
    update_article_content,
)
from app.scoring import parse_ai_score_payload
from app import gemini

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/n8n", tags=["n8n"])
ui_router = APIRouter(prefix="/mudnews", tags=["score"])


# ---------------------------------------------------------------------------
# GET /n8n/unscored-articles
# ---------------------------------------------------------------------------
@router.get("/unscored-articles")
async def unscored_articles(
    limit: int  = 200,
    user:  str  = Depends(require_auth),
):
    conn = get_conn()
    try:
        rows = get_unscored_articles(conn, limit=limit)
    finally:
        conn.close()
    return JSONResponse({"articles": rows, "count": len(rows)})


# ---------------------------------------------------------------------------
# POST /n8n/import-score
# ---------------------------------------------------------------------------
class ImportScoreRequest(BaseModel):
    raw_response: str


@router.post("/import-score")
async def import_score(
    body: ImportScoreRequest,
    user: str = Depends(require_auth),
):
    try:
        parsed = parse_ai_score_payload(body.raw_response)
    except ValueError as exc:
        logger.error("Score parse failed: %s | raw: %.200s", exc, body.raw_response)
        raise HTTPException(status_code=422, detail=str(exc))

    conn = get_conn()
    try:
        result = insert_article_score(
            conn,
            article_id = parsed["article_id"],
            user_id    = parsed["user_id"],
            score      = parsed["score"],
            reason     = parsed["reason"],
            category   = parsed["category"],
            decay      = parsed["decay"],
        )
    finally:
        conn.close()

    if not result["inserted"]:
        logger.warning(
            "Score conflict — article_id=%d user_id=%d already scored, skipped.",
            parsed["article_id"], parsed["user_id"],
        )

    return JSONResponse({
        "article_id":    parsed["article_id"],
        "user_id":       parsed["user_id"],
        "score":         parsed["score"],
        "category":      parsed["category"],
        "decay":         parsed["decay"],
        "inserted":      result["inserted"],
        "decay_updated": result["decay_updated"],
        "status":        "ok" if result["inserted"] else "skipped",
    })


# ---------------------------------------------------------------------------
# POST /n8n/score-batch
# ---------------------------------------------------------------------------
# Self-contained batch scorer. n8n calls this ONE endpoint on a schedule; the
# backend does everything: fetch unscored articles for every non-borrowing
# user, chunk them for the scorer, and upsert the results. The scorer may run
# one article at a time for local Ollama or use a batched provider later.
#
# Articles are grouped per user so each reader is scored with their own
# scoring_prompt. Borrowing users (borrows_scores_from IS NOT NULL) are excluded
# by get_unscored_articles, so they cost nothing — they read the lender's scores.
class ScoreBatchRequest(BaseModel):
    batch_size:    int = 100   # articles per scorer chunk
    max_articles:  int = 300   # safety cap on one invocation (Pi RAM + run time)
    source_feed:   str | None = None  # AU, UK, or omitted for both


def _chunked(seq: list, size: int):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


async def _score_unscored_articles(body: ScoreBatchRequest) -> JSONResponse:
    batch_size = max(1, min(body.batch_size, 100))
    source_feed = body.source_feed.strip().upper() if body.source_feed else None
    if source_feed not in (None, "", "AU", "UK"):
        raise HTTPException(status_code=422, detail="source_feed must be AU, UK, or omitted")

    conn = get_conn()
    try:
        rows = get_unscored_articles(conn, limit=body.max_articles, source_feed=source_feed)
    finally:
        conn.close()

    if not rows:
        return JSONResponse({
            "status": "empty", "fetched": 0, "scored": 0,
            "skipped": 0, "errors": 0, "batches": 0, "users": 0,
            "source_feed": source_feed or "all",
        })

    # Group rows by user so each user is scored with their own prompt.
    rows.sort(key=itemgetter("user_id"))
    user_groups = {
        uid: list(grp) for uid, grp in groupby(rows, key=itemgetter("user_id"))
    }

    total_scored = 0
    total_skipped = 0
    total_errors  = 0
    total_batches = 0

    for uid, user_rows in user_groups.items():
        scoring_prompt = user_rows[0].get("scoring_prompt") or ""

        for batch in _chunked(user_rows, batch_size):
            # Free tier: 5 RPM hard limit. 13 s between calls → ≤4.6 RPM.
            if total_batches > 0:
                await asyncio.sleep(13)
            total_batches += 1
            articles = [
                {"article_id": r["article_id"], "title": r["title"],
                 "description": r["description"]}
                for r in batch
            ]
            try:
                scored = await gemini.score_batch(articles, scoring_prompt)
            except RuntimeError as exc:
                # Misconfiguration (no API key) — fail the whole request loudly.
                logger.error("score-batch aborted: %s", exc)
                raise HTTPException(status_code=500, detail=str(exc))
            except ValueError as exc:
                # One bad model response — skip this batch, keep going.
                logger.error("score-batch: model batch failed for user %d — %s", uid, exc)
                total_errors += len(articles)
                continue

            if len(scored) < len(articles):
                total_errors += len(articles) - len(scored)

            conn = get_conn()
            try:
                for s in scored:
                    try:
                        result = insert_article_score(
                            conn,
                            article_id = s["article_id"],
                            user_id    = uid,
                            score      = s["score"],
                            reason     = s["reason"],
                            category   = s["category"],
                            decay      = s["decay"],
                        )
                        if result["inserted"]:
                            total_scored += 1
                        else:
                            total_skipped += 1
                    except Exception as item_exc:
                        logger.warning(
                            "score-batch: insert failed article_id=%s user_id=%d — %s",
                            s.get("article_id"), uid, item_exc,
                        )
                        conn.rollback()
                        total_errors += 1
            finally:
                conn.close()

    logger.info(
        "score-batch: source=%s fetched=%d scored=%d skipped=%d errors=%d batches=%d users=%d",
        source_feed or "all", len(rows), total_scored, total_skipped,
        total_errors, total_batches, len(user_groups),
    )
    return JSONResponse({
        "status":   "ok",
        "source_feed": source_feed or "all",
        "fetched":  len(rows),
        "scored":   total_scored,
        "skipped":  total_skipped,
        "errors":   total_errors,
        "batches":  total_batches,
        "users":    len(user_groups),
    })


@router.post("/score-batch")
async def score_batch_endpoint(
    body: ScoreBatchRequest = ScoreBatchRequest(),
    user: str = Depends(require_auth),
):
    return await _score_unscored_articles(body)


@ui_router.post("/score-feeds")
async def score_feeds_endpoint(
    body: ScoreBatchRequest = ScoreBatchRequest(),
    user: str = Depends(require_session),
):
    return await _score_unscored_articles(body)


# ---------------------------------------------------------------------------
# GET /n8n/articles-needing-content
# ---------------------------------------------------------------------------
@router.get("/articles-needing-content")
async def articles_needing_content(
    limit: int = 50,
    user:  str = Depends(require_auth),
):
    """
    Return scored articles that don't yet have full_content fetched.
    N8N splits these out and calls fetch-article-content for each.
    """
    conn = get_conn()
    cur  = conn.cursor()
    try:
        cur.execute(
            """
            SELECT DISTINCT a.id, a.url, a.title
            FROM articles a
            JOIN article_user_scores aus ON aus.article_id = a.id
            WHERE a.full_content IS NULL
              AND a.url IS NOT NULL
            ORDER BY a.id DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = [{"article_id": r[0], "url": r[1], "title": r[2]} for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()

    return JSONResponse({"articles": rows, "count": len(rows)})


# ---------------------------------------------------------------------------
# POST /n8n/fetch-article-content
# ---------------------------------------------------------------------------
class FetchContentRequest(BaseModel):
    article_id:   int
    url:          str
    title:        str
    full_content: str | None = None
    images:       list | None = None


@router.post("/fetch-article-content")
async def fetch_article_content(
    body: FetchContentRequest,
    user: str = Depends(require_auth),
):
    from app.scraper import fetch_article_content as _scrape

    full_content, images = _scrape(body.url, body.full_content, body.images)

    conn = get_conn()
    try:
        update_article_content(conn, body.article_id, full_content, images)
    finally:
        conn.close()

    return JSONResponse({
        "article_id":   body.article_id,
        "url":          body.url,
        "title":        body.title,
        "full_content": full_content,
        "images":       images,
    })
