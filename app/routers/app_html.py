from pathlib import Path
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth import require_session

router = APIRouter()

HTML_PATH = Path("/app/app/static/mudnews-viewer.html")


@router.get("/mudnews")
@router.get("/mudnews/")
async def redirect_to_app():
    return RedirectResponse(url="/mudnews/app")


@router.get("/mudnews/app", response_class=HTMLResponse)
async def serve_app(user: str = Depends(require_session)):
    return HTML_PATH.read_text()
