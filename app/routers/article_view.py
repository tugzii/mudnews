"""
Full-article viewer endpoints for mudnews web UI.

GET  /mudnews/article/<id>         — serves the article HTML page
GET  /mudnews/article-data/<id>    — JSON: full_content, images, title, url, scores
                                     ?skip_images=true  — skip image fetch (Alexa path)
"""

import json
from pathlib import Path
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from app.auth import require_session
from app.db import get_conn, update_article_content
from app.scraper import fetch_article_content as _scrape

router = APIRouter()

ARTICLE_PAGE_PATH = Path("/app/app/static/mudnews-article.html")


@router.get("/mudnews/article/{article_id}", response_class=HTMLResponse)
async def serve_article_page(article_id: int):
    return ARTICLE_PAGE_PATH.read_text()


def _bg_fetch_images(article_id: int, url: str, full_content: str) -> None:
    """Background task: fetch images for an article that already has content.
    Saves even an empty list so NULL→[] distinguishes 'never tried' from 'tried, none found'.
    """
    _, images = _scrape(url, full_content, None)
    conn = get_conn()
    try:
        update_article_content(conn, article_id, full_content, images)
    finally:
        conn.close()


@router.get("/mudnews/article-data/{article_id}")
async def get_article_data(
    article_id: int,
    background_tasks: BackgroundTasks,
    skip_images: bool = False,
    user: str = Depends(require_session),
):
    conn = get_conn()
    cur  = conn.cursor()

    cur.execute("SELECT id FROM users WHERE name = %s", (user,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="User not found")

    cur.execute("""
        SELECT
            a.id,
            a.title,
            a.full_content,
            a.url,
            a.images,
            a.created_at,
            a.published_at,
            aus.ai_score,
            aus.ai_reason,
            a.decay,
            c.name AS category
        FROM articles a
        LEFT JOIN article_user_scores aus ON aus.article_id = a.id AND aus.user_id = %s
        LEFT JOIN categories c            ON c.id = aus.category_id
        WHERE a.id = %s
    """, (row[0], article_id))

    rec = cur.fetchone()
    cur.close()
    conn.close()

    if not rec:
        raise HTTPException(status_code=404, detail="Article not found")

    (aid, title, full_content, url, images_raw,
     created_at, published_at, ai_score, ai_reason,
     decay, category) = rec

    # Preserve NULL vs [] distinction:
    #   None  = never attempted image fetch
    #   []    = attempted, none found — do not retry
    #   [...] = has images
    if images_raw is None:
        images = None
    elif isinstance(images_raw, list):
        images = images_raw
    elif isinstance(images_raw, str):
        try:
            images = json.loads(images_raw)
        except Exception:
            images = None
    else:
        images = None

    if url and not full_content:
        # Content missing — scrape synchronously so text renders immediately
        full_content, images = _scrape(url, None, images)
        conn2 = get_conn()
        try:
            update_article_content(conn2, aid, full_content, images if images is not None else [])
        finally:
            conn2.close()
    elif url and images is None and not skip_images:
        # Content present, images never attempted — kick off background fetch.
        # Response returns immediately; images available on next expand.
        background_tasks.add_task(_bg_fetch_images, aid, url, full_content)

    return JSONResponse({
        "article_id":   aid,
        "title":        title or "Untitled",
        "full_content": full_content or "",
        "url":          url or "",
        "images":       images or [],
        "created_at":   created_at.isoformat() if created_at else None,
        "published_at": published_at.isoformat() if published_at else None,
        "ai_score":     ai_score,
        "ai_reason":    ai_reason or "",
        "decay":        decay or "moderate",
        "category":     category or "Other",
    })
