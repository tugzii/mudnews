from fastapi import APIRouter, Depends, Query, HTTPException, Request
from fastapi.responses import JSONResponse

from app.dependencies import require_auth
from app.db import get_conn, select_article_pool
from app.limiter import limiter
import app.scoring as scoring

router = APIRouter()


@router.get("/rss-stories")
@limiter.limit("30/minute")
async def fetch_story(
    request:     Request,
    mode:        str = Query(...),
    user_id:     int = Query(...),
    exclude_ids: str = Query(default=""),
    user:        str = Depends(require_auth),
):
    if mode not in ("latest", "top"):
        raise HTTPException(status_code=400, detail=f"Unknown mode: {mode}")

    extra_exclude = []
    if exclude_ids:
        try:
            extra_exclude = [int(x.strip()) for x in exclude_ids.split(",") if x.strip().isdigit()]
        except Exception:
            extra_exclude = []

    conn = get_conn()
    cur  = conn.cursor()

    cur.execute("SELECT id, COALESCE(borrows_scores_from, id) FROM users WHERE id = %s", (user_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=403, detail=f"User ID {user_id} not found")
    _resolved_user_id, score_user_id = row
    cur.close()

    candidates = select_article_pool(
        conn, score_user_id, mode, scoring,
        extra_exclude=extra_exclude, source_feed="AU",
    )
    conn.close()

    # In mudnews we only need content to be fetched (no voice_summary required)
    ready = [a for a in candidates if a["full_content"] is not None]

    if not ready:
        return JSONResponse({})

    best = ready[0]
    return JSONResponse({
        "article_id": best["article_id"],
        "title":      best["title"],
    })
