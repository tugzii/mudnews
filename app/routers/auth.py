"""
Authentication endpoints for mudnews web UI.

POST /auth/login            — email + password → JWT (rate limited, timing-safe)
POST /auth/change-password  — authenticated; current password required
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.auth_utils import (
    verify_password, hash_password, create_access_token,
    decode_token, DUMMY_HASH,
)
from app.limiter import limiter
from app.db import get_conn

logger = logging.getLogger(__name__)
router = APIRouter()
_security = HTTPBearer(auto_error=False)


class LoginRequest(BaseModel):
    email: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@router.post("/auth/login")
@limiter.limit("10/minute")
async def login(request: Request, data: LoginRequest):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id, name, password_hash, is_active FROM users WHERE email = %s",
            (data.email.lower().strip(),),
        )
        row = cur.fetchone()
    finally:
        cur.close()
        conn.close()

    # Always run verify_password — prevents timing-based user enumeration.
    # Argon2id is slow; skipping it when the user doesn't exist would make
    # "not found" responses measurably faster than "wrong password" responses.
    stored_hash = row[2] if (row and row[2]) else DUMMY_HASH
    try:
        password_ok = verify_password(data.password, stored_hash)
    except Exception:
        password_ok = False  # NULL password_hash raises instead of returning False

    if not row or not password_ok:
        raise HTTPException(status_code=401, detail="Incorrect email or password.")

    if not row[3]:  # is_active
        raise HTTPException(status_code=403, detail="Account is not active.")

    token = create_access_token(name=row[1], email=data.email.lower().strip())
    return {"access_token": token, "token_type": "bearer"}


@router.post("/auth/change-password")
@limiter.limit("10/minute")
async def change_password(
    request: Request,
    data: ChangePasswordRequest,
    credentials: HTTPAuthorizationCredentials | None = Depends(_security),
):
    if credentials is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = decode_token(credentials.credentials)
    name = payload.get("sub")
    if not name:
        raise HTTPException(status_code=401, detail="Invalid token")

    if len(data.new_password) < 8:
        raise HTTPException(status_code=422, detail="New password must be at least 8 characters.")

    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, password_hash FROM users WHERE name = %s", (name,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found.")

        try:
            ok = verify_password(data.current_password, row[1] or DUMMY_HASH)
        except Exception:
            ok = False

        if not ok:
            raise HTTPException(status_code=401, detail="Current password is incorrect.")

        cur.execute(
            "UPDATE users SET password_hash = %s WHERE id = %s",
            (hash_password(data.new_password), row[0]),
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()

    return {"detail": "Password updated."}
