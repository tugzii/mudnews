"""
n8n integration endpoints for mudnews.

No summarisation — articles are scored and content is fetched, then Alexa
reads the full text on demand with pagination.

Endpoints
  GET  /n8n/unscored-articles          → articles needing AI scoring
  POST /n8n/import-score               → save AI score for an article
  POST /n8n/fetch-article-content      → fetch + store full text for one article
  GET  /n8n/articles-needing-content   → scored articles without full_content yet
"""

import logging
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.dependencies import require_auth
from app.db import (
    get_conn, get_unscored_articles, insert_article_score,
    update_article_content,
)
from app.scoring import parse_ai_score_payload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/n8n", tags=["n8n"])


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
    article_id: int
    user_id:    int
    raw_response: str


@router.post("/import-score")
async def import_score(
    body: ImportScoreRequest,
    user: str = Depends(require_auth),
):
    try:
        parsed = parse_ai_score_payload(body.raw_response, body.article_id, body.user_id)
    except ValueError as exc:
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

    return JSONResponse({
        "article_id":    parsed["article_id"],
        "user_id":       parsed["user_id"],
        "score":         parsed["score"],
        "inserted":      result["inserted"],
        "decay_updated": result["decay_updated"],
    })


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
