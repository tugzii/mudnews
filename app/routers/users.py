from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.auth import require_session
from app.db import get_conn

router = APIRouter()


@router.get("/mudnews/get-users")
async def get_users(user: str = Depends(require_session)):
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("SELECT id, name, borrows_scores_from FROM users ORDER BY id")
    users = [{"id": r[0], "name": r[1], "borrows_scores_from": r[2]} for r in cur.fetchall()]
    cur.close()
    conn.close()
    return JSONResponse({"users": users})


@router.get("/mudnews/me")
async def get_me(user: str = Depends(require_session)):
    return {"username": user}
