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
