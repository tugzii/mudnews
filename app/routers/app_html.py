from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter()

HTML_PATH  = Path("/app/app/static/mudnews-viewer.html")
LOGIN_PATH = Path("/app/app/static/mudnews-login.html")


@router.get("/mudnews")
@router.get("/mudnews/")
async def redirect_to_app():
    return RedirectResponse(url="/mudnews/app")


@router.get("/mudnews/app", response_class=HTMLResponse)
async def serve_app():
    return HTML_PATH.read_text()


@router.get("/mudnews/login", response_class=HTMLResponse)
async def serve_login():
    return LOGIN_PATH.read_text()
