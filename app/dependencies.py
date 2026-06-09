import os
from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_auth(
    api_key: str | None = Security(api_key_header),
) -> str:
    """API key auth for Alexa and N8N callers."""
    expected = os.environ.get("ALEXA_API_KEY", "")
    if expected and api_key == expected:
        return "alexa"
    raise HTTPException(status_code=401, detail="Not authenticated")
