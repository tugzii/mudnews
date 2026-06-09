"""Web UI session auth — validates Bearer JWT, returns user name string."""
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth_utils import decode_token

_security = HTTPBearer(auto_error=False)


def require_session(
    credentials: HTTPAuthorizationCredentials | None = Depends(_security),
) -> str:
    """Validate Bearer JWT; return user name (matches users.name column)."""
    if credentials is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(credentials.credentials)
    name = payload.get("sub")
    if not name:
        raise HTTPException(status_code=401, detail="Invalid token")
    return name
