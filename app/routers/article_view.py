"""
Full-article viewer endpoints for mudnews web UI.

GET  /mudnews/article/<id>         — serves the article HTML page
GET  /mudnews/article-data/<id>    — JSON: full_content, images, title, url, scores
"""

import json
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from app.auth import require_session
from app.db import get_conn

router = APIRouter()

ARTICLE_PAGE_PATH = Path("/app/app/static/mudnews-article.html")


@router.get("/mudnews/article/{article_id}", response_class=HTMLResponse)
async def serve_article_page(article_id: int):
    return ARTICLE_PAGE_PATH.read_text()


@router.get("/mudnews/article-data/{article_id}")
async def get_article_data(article_id: int, user: str = Depends(require_session)):
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

    if images_raw is None:
        images = []
    elif isinstance(images_raw, list):
        images = images_raw
    elif isinstance(images_raw, str):
        try:
            images = json.loads(images_raw)
        except Exception:
            images = []
    else:
        images = []

    return JSONResponse({
        "article_id":   aid,
        "title":        title or "Untitled",
        "full_content": full_content or "",
        "url":          url or "",
        "images":       images,
        "created_at":   created_at.isoformat() if created_at else None,
        "published_at": published_at.isoformat() if published_at else None,
        "ai_score":     ai_score,
        "ai_reason":    ai_reason or "",
        "decay":        decay or "moderate",
        "category":     category or "Other",
    })
