from fastapi import Request, HTTPException

def require_session(request: Request) -> str:
    user = request.headers.get("X-authentik-username")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user