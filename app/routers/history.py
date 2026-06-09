import json
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.auth import require_session
from app.db import get_conn

router = APIRouter()


@router.get("/mudnews/get-history")
async def get_history(
    user: str = Depends(require_session),
):
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("SELECT id FROM users WHERE name = %s", (user,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="User not found")
    user_id = row[0]

    cur.execute("""
        SELECT
            a.id, a.title, a.url,
            a.decay, a.created_at, aus.ai_score,
            c.name, ai.status, ai.actioned_at, a.images
        FROM article_interactions ai
        JOIN articles a                   ON a.id = ai.article_id
        LEFT JOIN article_user_scores aus ON aus.article_id = a.id AND aus.user_id = %s
        LEFT JOIN categories c            ON c.id = aus.category_id
        WHERE ai.user_id = %s
          AND ai.status IN ('read', 'skipped', 'declined')
        ORDER BY ai.actioned_at DESC
        LIMIT 200
    """, (user_id, user_id))

    cols     = ["article_id", "title", "url", "decay", "created_at",
                "ai_score", "category", "status", "actioned_at", "images"]
    articles = []

    for row in cur.fetchall():
        rec = dict(zip(cols, row))
        for k in ("created_at", "actioned_at"):
            if rec[k]:
                rec[k] = rec[k].isoformat()
        if rec["images"] is None:
            rec["images"] = []
        elif isinstance(rec["images"], str):
            try:
                rec["images"] = json.loads(rec["images"])
            except Exception:
                rec["images"] = []
        articles.append(rec)

    cur.close()
    conn.close()
    return JSONResponse({"articles": articles})
