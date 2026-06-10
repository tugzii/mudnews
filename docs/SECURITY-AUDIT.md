# MudNews — WAN Exposure Security Audit

**Audited by:** Opus 4.8
**Date:** 2026-06-10
**Scope:** Opening port `9992` (`mudguts.duckdns.org:9992`) to the WAN, exposing exactly three Alexa endpoints.
**Verdict:** ✅ **Safe to open**, after applying the one recommended hardening (rate limiting). Low-priority items below are nice-to-haves.

---

## 1. Context

nginx conf `rproxy/nginx_root/etc/nginx/conf.d/9992-mudnews.conf` exposes to the WAN:

| Path | Method | Router | Auth |
|---|---|---|---|
| `/rss-stories` | GET | `routers/stories.py` | `require_auth` (X-API-Key) |
| `/read-article` | GET | `routers/article_read.py` | `require_auth` (X-API-Key) |
| `/mark-read` | POST | `routers/mark_read.py` | `require_auth` (X-API-Key) |

Everything else (`/mudnews/*` web UI, `/auth/*`, `/n8n/*`, docs) is `allow 192.168.1.0/24; deny all;` → **LAN only**.

The API key is `ALEXA_API_KEY` — a 64-char hex (256-bit) random secret. Brute force is infeasible; this is the load-bearing control for WAN exposure.

---

## 2. What was verified as correct (no action needed)

- **All three WAN endpoints enforce the API key** via `Depends(require_auth)` in `app/dependencies.py`.
- **SQL is fully parameterized** (psycopg2 `%s`) in all three endpoints — no injection vector.
- **nginx location precedence traced for every route**:
  - `/mudnews/mark-read` (browser) starts with `/mudnews`, not `/mark-read` → `location /` → LAN only. ✅
  - `/n8n/*` → own prefix block → LAN only. ✅
  - `/mudnews/docs`, `/mudnews/openapi.json` → under `/mudnews/` → LAN only (API schema not exposed). ✅
  - Only the three intended paths hit the open regex block.
- **Write surface is minimal**: only `/mark-read` writes, and only to `article_interactions`, gated by the server-side `VALID_STATUSES` whitelist plus a user-existence check. No auth bypass, no destructive operations reachable.
- **`/read-article` and `/rss-stories` are read-only.**

---

## 3. RECOMMENDED — do before opening the port

### 3.1 Add rate limiting to the three WAN endpoints  🔴 priority

**Problem:** `slowapi` is wired up (`app/limiter.py`, middleware in `main.py`) but `Limiter` has **no default limits** and none of the three endpoints carry a `@limiter.limit(...)` decorator → effectively **no rate limit**. Because `app/db.py:get_conn()` opens a **fresh Postgres connection per request** (no pool), a flood of requests to the open endpoints can exhaust DB connections and knock the Pi over. The API key prevents *data* theft but does nothing against *flooding* — nginx forwards every request to FastAPI before the key is even checked.

**Fix — two parts:**

**(a) Make the limiter use the real client IP** (behind nginx, `get_remote_address` sees the proxy/container IP, so all clients share one bucket). nginx already sets `X-Real-IP`. Update `app/limiter.py`:

```python
from slowapi import Limiter
from slowapi.util import get_remote_address


def _client_ip(request):
    # Behind the rproxy nginx, the real client IP is in X-Real-IP.
    # Fall back to X-Forwarded-For's first hop, then the socket peer.
    xri = request.headers.get("x-real-ip")
    if xri:
        return xri
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return get_remote_address(request)


limiter = Limiter(key_func=_client_ip)
```

**(b) Decorate the three WAN endpoints.** slowapi requires the handler signature to include `request: Request` (and it must be named `request`). Add the import and decorator to each:

- `app/routers/stories.py` → `fetch_story` (`/rss-stories`)
- `app/routers/article_read.py` → `read_article` (`/read-article`)
- `app/routers/mark_read.py` → `mark_read_alexa` (`/mark-read`)

Example (apply the same pattern to all three; keep existing params):

```python
from fastapi import Request
from app.limiter import limiter

@router.get("/rss-stories")
@limiter.limit("30/minute")
async def fetch_story(
    request: Request,
    mode: str = Query(...),
    user_id: int = Query(...),
    exclude_ids: str = Query(default=""),
    user: str = Depends(require_auth),
):
    ...
```

Suggested limits (generous for a single Alexa device, brutal for a flood):
- `/rss-stories`: `30/minute`
- `/read-article`: `60/minute` (paginated chunks — a long article fetches several in a row)
- `/mark-read`: `30/minute`

**Verify after deploy:**
```bash
# From a WAN-ish context (or just hammer it locally bypassing nginx):
for i in $(seq 1 40); do
  curl -s -o /dev/null -w "%{http_code}\n" \
    -H "X-API-Key: $ALEXA_API_KEY" \
    "http://192.168.1.10:8002/rss-stories?mode=latest&user_id=1"
done | sort | uniq -c
# Expect a batch of 200s then 429s once the limit trips.
```

---

## 4. LOW PRIORITY — include per user request (not blockers)

### 4.1 Constant-time API key comparison

`app/dependencies.py` uses `api_key == expected`, which is not constant-time. With a 256-bit key over the internet a timing attack is unrealistic, but the fix is a one-line swap:

```python
import os
import secrets
from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_auth(
    api_key: str | None = Security(api_key_header),
) -> str:
    """API key auth for Alexa and N8N callers."""
    expected = os.environ.get("ALEXA_API_KEY", "")
    if expected and api_key and secrets.compare_digest(api_key, expected):
        return "alexa"
    raise HTTPException(status_code=401, detail="Not authenticated")
```

### 4.2 Anchor the nginx regex

`rproxy/nginx_root/etc/nginx/conf.d/9992-mudnews.conf` has:

```nginx
location ~ ^/(rss-stories|read-article|mark-read) {
```

Unanchored — it would also match a future `/read-article-debug`, `/mark-read-internal`, etc., silently exposing them to WAN. Harmless today (no such routes exist) but remove the footgun by anchoring to a path boundary:

```nginx
location ~ ^/(rss-stories|read-article|mark-read)(/|$) {
```

After editing, validate + reload:
```bash
ssh pi 'docker exec rproxy-nginx-1 nginx -t && docker exec rproxy-nginx-1 nginx -s reload'
```

### 4.3 (Note only — no change required) CORS `allow_origins=["*"]`

`main.py` sets wide-open CORS. This is **acceptable here** because auth is via the `X-API-Key`/Bearer header, not cookies, and `allow_credentials` is not enabled — a malicious site can't read responses without already holding the key. Leave as-is unless cookie auth is ever introduced.

### 4.4 (Note only) Single shared API key = full access to all users

The one `ALEXA_API_KEY` lets its holder read/mark interactions for **any** `user_id`. Within the trust model (key = trusted Alexa device) this is fine. Just be aware: key compromise = read/write to every user's interaction rows. Acceptable for personal/family use; revisit if the user base widens.

---

## 5. Implementation checklist for Sonnet

- [ ] **3.1(a)** Update `app/limiter.py` with `X-Real-IP`-aware `key_func`.
- [ ] **3.1(b)** Add `request: Request` param + `@limiter.limit(...)` to `fetch_story`, `read_article`, `mark_read_alexa`. Import `Request` and `limiter` in each router.
- [ ] **4.1** Swap to `secrets.compare_digest` in `app/dependencies.py`.
- [ ] **4.2** Anchor the nginx regex in `9992-mudnews.conf`; `nginx -t` + reload.
- [ ] Backend auto-reloads (`--reload`); confirm container is healthy after edits.
- [ ] Run the 3.1 verification curl loop; confirm 429s appear.
- [ ] Commit + push `mudnews` repo; the nginx conf lives in the `rproxy` tree (commit there separately if versioned).
- [ ] Only **after** rate limiting is confirmed live: open port 9992 on the router/firewall.
