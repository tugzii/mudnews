"""
Explore tab endpoints for mudnews.

GET  /mudnews/recent-articles?limit=<n>
GET  /mudnews/search-articles?q=<term>&limit=<n>
POST /mudnews/rescue-article
"""

import json
from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.auth import require_session
from app.db import get_conn

router = APIRouter()


@router.get("/mudnews/search-articles")
async def search_articles(
    q:     str = Query(..., min_length=2),
    limit: int = Query(default=50, ge=1, le=200),
    user:  str = Depends(require_session),
):
    conn = get_conn()
    cur  = conn.cursor()

    cur.execute("SELECT id FROM users WHERE name = %s", (user,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="User not found")
    user_id = row[0]

    pattern = f"%{q}%"
    cur.execute(
        """
        SELECT
            a.id, a.title, a.description, a.url,
            a.published_at, a.created_at, a.decay,
            a.full_content IS NOT NULL  AS content_fetched,
            a.images,
            aus.ai_score,
            aus.ai_reason,
            aus.rescued_at,
            c.name                       AS category,
            ai.status                    AS interaction_status
        FROM articles a
        LEFT JOIN article_user_scores aus
               ON aus.article_id = a.id AND aus.user_id = %s
        LEFT JOIN categories c
               ON c.id = aus.category_id
        LEFT JOIN article_interactions ai
               ON ai.article_id = a.id AND ai.user_id = %s
        WHERE (a.title ILIKE %s OR a.description ILIKE %s)
        ORDER BY
            CASE
                WHEN aus.rescued_at IS NOT NULL
                 AND (ai.status IS NULL OR ai.status = 'presented')
                THEN 1
                WHEN ai.status = 'read' THEN 2
                ELSE 3
            END,
            a.created_at DESC
        LIMIT %s
        """,
        (user_id, user_id, pattern, pattern, limit),
    )

    cols = [
        "article_id", "title", "description", "url",
        "published_at", "created_at", "decay",
        "content_fetched", "images",
        "ai_score", "ai_reason", "rescued_at",
        "category", "interaction_status",
    ]

    import json as _json
    articles = []
    for r in cur.fetchall():
        rec = dict(zip(cols, r))
        for k in ("published_at", "created_at", "rescued_at"):
            if rec[k]:
                rec[k] = rec[k].isoformat()
        if rec["images"] is None:
            rec["images"] = []
        elif isinstance(rec["images"], str):
            try:
                rec["images"] = _json.loads(rec["images"])
            except Exception:
                rec["images"] = []
        articles.append(rec)

    cur.close()
    conn.close()
    return JSONResponse({"articles": articles, "query": q})


@router.get("/mudnews/recent-articles")
async def recent_articles(
    limit: int = Query(default=50, ge=1, le=200),
    user:  str = Depends(require_session),
):
    conn = get_conn()
    cur  = conn.cursor()

    cur.execute("SELECT id FROM users WHERE name = %s", (user,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="User not found")
    user_id = row[0]

    cur.execute(
        """
        SELECT
            a.id, a.title, a.description, a.url,
            a.published_at, a.created_at, a.decay,
            a.full_content IS NOT NULL  AS content_fetched,
            a.images,
            aus.ai_score,
            aus.ai_reason,
            aus.rescued_at,
            c.name                       AS category,
            ai.status                    AS interaction_status
        FROM articles a
        LEFT JOIN article_user_scores aus
               ON aus.article_id = a.id AND aus.user_id = %s
        LEFT JOIN categories c
               ON c.id = aus.category_id
        LEFT JOIN article_interactions ai
               ON ai.article_id = a.id AND ai.user_id = %s
        ORDER BY
            CASE
                WHEN aus.rescued_at IS NOT NULL
                 AND (ai.status IS NULL OR ai.status = 'presented')
                THEN 0
                WHEN ai.status IS NULL OR ai.status = 'presented' THEN 1
                WHEN ai.status = 'read' THEN 2
                ELSE 3
            END,
            a.created_at DESC
        LIMIT %s
        """,
        (user_id, user_id, limit),
    )

    cols = [
        "article_id", "title", "description", "url",
        "published_at", "created_at", "decay",
        "content_fetched", "images",
        "ai_score", "ai_reason", "rescued_at",
        "category", "interaction_status",
    ]

    import json as _json
    articles = []
    for r in cur.fetchall():
        rec = dict(zip(cols, r))
        for k in ("published_at", "created_at", "rescued_at"):
            if rec[k]:
                rec[k] = rec[k].isoformat()
        if rec["images"] is None:
            rec["images"] = []
        elif isinstance(rec["images"], str):
            try:
                rec["images"] = _json.loads(rec["images"])
            except Exception:
                rec["images"] = []
        articles.append(rec)

    cur.close()
    conn.close()
    return JSONResponse({"articles": articles})


class RescueRequest(BaseModel):
    article_id: int


@router.post("/mudnews/rescue-article")
async def rescue_article(
    body: RescueRequest,
    user: str  = Depends(require_session),
):
    conn = get_conn()
    cur  = conn.cursor()

    cur.execute("SELECT id FROM users WHERE name = %s", (user,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="User not found")
    user_id = row[0]

    cur.execute(
        """
        INSERT INTO article_user_scores (article_id, user_id, ai_score, rescued_at)
        VALUES (%s, %s, NULL, NOW())
        ON CONFLICT (article_id, user_id) DO UPDATE
            SET rescued_at = NOW()
        """,
        (body.article_id, user_id),
    )

    cur.execute(
        """
        DELETE FROM article_interactions
        WHERE user_id    = %s
          AND article_id = %s
          AND status IN ('read', 'skipped', 'declined')
        """,
        (user_id, body.article_id),
    )

    conn.commit()

    cur.execute(
        """
        SELECT
            a.full_content IS NOT NULL AS content_fetched,
            aus.ai_score,
            aus.rescued_at
        FROM articles a
        JOIN article_user_scores aus ON aus.article_id = a.id AND aus.user_id = %s
        WHERE a.id = %s
        """,
        (user_id, body.article_id),
    )
    state = cur.fetchone()
    cur.close()
    conn.close()

    return JSONResponse({
        "status":          "rescued",
        "article_id":      body.article_id,
        "content_fetched": state[0] if state else False,
        "ai_score":        state[1] if state else None,
        "rescued_at":      state[2].isoformat() if state and state[2] else None,
    })
