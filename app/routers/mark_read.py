from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from app.dependencies import require_auth
from app.auth import require_session
from app.db import get_conn

router = APIRouter()

VALID_STATUSES = {"read", "declined", "skipped", "presented"}


class MarkReadRequest(BaseModel):
    article_id: int
    status:     str = "read"


class AlexaMarkReadRequest(BaseModel):
    article_id: int
    status:     str = "read"
    user_id:    int


async def _mark_read_by_user_id(article_id: int, status: str, user_id: int):
    if status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("SELECT id FROM users WHERE id = %s", (user_id,))
    if not cur.fetchone():
        raise HTTPException(status_code=403, detail=f"User ID {user_id} not found")
    cur.execute("""
        INSERT INTO article_interactions (user_id, article_id, status)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id, article_id) DO UPDATE
            SET status = EXCLUDED.status,
                actioned_at = NOW()
    """, (user_id, article_id, status))
    conn.commit()
    cur.close()
    conn.close()
    return JSONResponse({"status": status, "user_id": user_id, "article_id": article_id})


async def _mark_read_by_username(article_id: int, status: str, username: str):
    if status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("SELECT id FROM users WHERE name = %s", (username,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="User not found")
    user_id = row[0]
    cur.execute("""
        INSERT INTO article_interactions (user_id, article_id, status)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id, article_id) DO UPDATE
            SET status = EXCLUDED.status,
                actioned_at = NOW()
    """, (user_id, article_id, status))
    conn.commit()
    cur.close()
    conn.close()
    return JSONResponse({"status": status, "user_id": user_id, "article_id": article_id})


@router.post("/mark-read")
async def mark_read_alexa(
    body: AlexaMarkReadRequest,
    user: str = Depends(require_auth),
):
    return await _mark_read_by_user_id(body.article_id, body.status, body.user_id)


@router.post("/mudnews/mark-read")
async def mark_read_browser(
    body: MarkReadRequest,
    user: str = Depends(require_session),
):
    return await _mark_read_by_username(body.article_id, body.status, user)
