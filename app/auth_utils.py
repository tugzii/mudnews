"""JWT and password utilities — no project-specific logic."""
import os
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
from pwdlib import PasswordHash
from fastapi import HTTPException

_pwd = PasswordHash.recommended()   # Argon2id with recommended parameters

# Pre-computed hash of a throwaway string — always verified when user not found
# so response timing is indistinguishable from a wrong-password response.
DUMMY_HASH = "$argon2id$v=19$m=65536,t=3,p=4$gTzMyygwlcdq+3BdLJR1Lw$pWLO5iAeFxeH+6dfeqsx7I/MAlIV7pNi7qwUBPqtuiw"


def _secret() -> str:
    s = os.environ.get("APP_SECRET_KEY", "")
    if not s or len(s) < 32:
        raise RuntimeError("APP_SECRET_KEY must be set and at least 32 characters")
    return s


def hash_password(plain: str) -> str:
    return _pwd.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd.verify(plain, hashed)


def create_access_token(name: str, email: str, expire_hours: int = 24) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": name,
        "email": email,
        "exp": now + timedelta(hours=expire_hours),
        "iat": now,
    }
    return pyjwt.encode(payload, _secret(), algorithm="HS256")


def decode_token(token: str) -> dict:
    try:
        return pyjwt.decode(token, _secret(), algorithms=["HS256"])
    except pyjwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
