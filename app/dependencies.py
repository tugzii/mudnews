import os
from fastapi import Request, HTTPException, Security
from fastapi.security import APIKeyHeader

# Declaring this scheme makes Swagger UI show the Authorize button
# and include the key in Try it out requests automatically.
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_auth(
    request: Request,
    api_key: str | None = Security(api_key_header),
) -> str:
    # Authentik forward auth (browser users)
    user = request.headers.get("X-authentik-username")
    if user:
        return user

    # API key auth (Alexa, n8n, other service callers)
    expected = os.environ.get("ALEXA_API_KEY", "")
    if expected and api_key == expected:
        return "alexa"

    raise HTTPException(status_code=401, detail="Not authenticated")