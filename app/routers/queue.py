import json
from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import JSONResponse

from app.auth import require_session
from app.db import get_conn, select_article_pool, LATEST_SCORE_MIN, TOP_SCORE_MIN
import app.scoring as scoring

router = APIRouter()


@router.get("/mudnews/get-queue")
async def get_queue(
    mode: str = Query(...),
    user: str = Depends(require_session),
):
    if mode not in ("latest", "top"):
        raise HTTPException(status_code=400, detail=f"Unknown mode: {mode}")

    conn = get_conn()
    cur  = conn.cursor()

    cur.execute("SELECT id, COALESCE(borrows_scores_from, id) FROM users WHERE name = %s", (user,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="User not found")
    user_id, score_user_id = row
    cur.close()

    articles_raw = select_article_pool(conn, score_user_id, mode, scoring)
    conn.close()

    articles = []
    for art in articles_raw:
        rec = {
            "article_id":      art["article_id"],
            "title":           art["title"],
            "description":     art.get("description") or "",
            "url":             art["url"],
            "decay":           art["decay"],
            "created_at":      art["created_at"].isoformat(),
            "published_at":    art["published_at"].isoformat() if art.get("published_at") else art["created_at"].isoformat(),
            "ai_score":        art["ai_score"],
            "effective_score": art["effective_score"],
            "category":        art["category"],
            "images":          art["images"],
        }

        if rec["images"] is None:
            rec["images"] = []
        elif isinstance(rec["images"], str):
            try:
                rec["images"] = json.loads(rec["images"])
            except Exception:
                rec["images"] = []

        articles.append(rec)

    return JSONResponse({"articles": articles})


@router.get("/mudnews/explore-articles")
async def explore_articles(
    q:     str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=200),
    user:  str = Depends(require_session),
):
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("SELECT id FROM users WHERE name = %s", (user,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="User not found")
    user_id = row[0]
    cur.close()

    cur = conn.cursor()
    if q.strip():
        cur.execute(
            """
            SELECT a.id, a.title, a.description, a.url, a.published_at, a.created_at,
                   a.decay, a.images, aus.ai_score, c.name AS category
            FROM articles a
            LEFT JOIN article_user_scores aus ON aus.article_id = a.id AND aus.user_id = %s
            LEFT JOIN categories c ON c.id = aus.category_id
            WHERE a.search_vector @@ websearch_to_tsquery('english', %s)
            ORDER BY ts_rank_cd(a.search_vector, websearch_to_tsquery('english', %s)) DESC,
                     a.published_at DESC
            LIMIT %s
            """,
            (user_id, q, q, limit),
        )
    else:
        cur.execute(
            """
            SELECT a.id, a.title, a.description, a.url, a.published_at, a.created_at,
                   a.decay, a.images, aus.ai_score, c.name AS category
            FROM articles a
            LEFT JOIN article_user_scores aus ON aus.article_id = a.id AND aus.user_id = %s
            LEFT JOIN categories c ON c.id = aus.category_id
            ORDER BY a.published_at DESC
            LIMIT %s
            """,
            (user_id, limit),
        )

    articles = []
    for row in cur.fetchall():
        aid, title, description, url, published_at, created_at, decay, images, ai_score, category = row
        if isinstance(images, list):
            imgs = images
        elif images:
            try:
                imgs = json.loads(images)
            except Exception:
                imgs = []
        else:
            imgs = []
        articles.append({
            "article_id":   aid,
            "title":        title or "",
            "description":  description or "",
            "url":          url,
            "published_at": published_at.isoformat() if published_at else None,
            "created_at":   created_at.isoformat() if created_at else None,
            "decay":        decay or "moderate",
            "images":       imgs,
            "ai_score":     ai_score,
            "category":     category,
        })
    cur.close()
    conn.close()
    return JSONResponse({"articles": articles, "count": len(articles)})
