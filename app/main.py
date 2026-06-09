import os
import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.limiter import limiter
from app.routers import app_html, users, queue, history, stories, mark_read, n8n, feeds, article_view, explore, article_read
from app.routers.auth import router as auth_router
from app.db import get_conn, fix_null_decay

def load_secrets():
    vault_url    = os.environ["VAULT_URL"]
    vault_bearer = os.environ["VAULT_BEARER"]
    resp = httpx.get(vault_url, headers={"Authorization": f"Bearer {vault_bearer}"}, timeout=10)
    resp.raise_for_status()
    secrets = resp.json()["secrets"]
    os.environ["DB_USER"] = secrets["postgres_user"]
    os.environ["DB_PASS"] = secrets["postgres_password"]

@asynccontextmanager
async def lifespan(app: FastAPI):
    load_secrets()
    conn = get_conn()
    updated = fix_null_decay(conn)
    conn.close()
    if updated:
        import logging
        logging.getLogger(__name__).info("fix_null_decay: patched %d article(s)", updated)
    yield

app = FastAPI(lifespan=lifespan, docs_url="/mudnews/docs", redoc_url="/mudnews/redoc", openapi_url="/mudnews/openapi.json")

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(app_html.router)
app.include_router(users.router)
app.include_router(queue.router)
app.include_router(history.router)
app.include_router(stories.router)
app.include_router(mark_read.router)
app.include_router(n8n.router)
app.include_router(feeds.router)
app.include_router(article_view.router)
app.include_router(explore.router)
app.include_router(article_read.router)
