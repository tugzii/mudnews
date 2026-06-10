"""
Gemini batch scorer for mudnews.

Scores many articles in a single Google Gemini 2.5 Flash call and returns one
result per article.  Two design choices make this bulletproof where the old
Ollama per-article flow was not:

1. INDEX-BASED IDS — the model never sees or returns a real article_id.  Each
   article in a batch is given a 1-based position number; the model returns that
   number back.  The backend maps position -> real article_id afterwards, so the
   model physically cannot hallucinate an id that doesn't exist.

2. SCHEMA-CONSTRAINED JSON — the request uses Gemini's structured-output mode
   (responseMimeType=application/json + responseSchema with enums).  The model
   cannot emit prose, code fences, an invalid category, or an invalid decay
   value.  The response is always a parseable JSON array.

Public API
    await score_batch(articles, scoring_prompt) -> list[dict]
        articles: [{"article_id": int, "title": str, "description": str}, ...]
        returns:  [{"article_id", "score", "reason", "category", "decay"}, ...]
"""

import json
import logging
import os

import httpx

logger = logging.getLogger(__name__)

GEMINI_MODEL   = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)
GEMINI_TIMEOUT = float(os.environ.get("GEMINI_TIMEOUT", "120"))

# Must match the `categories` table exactly (db: SELECT name FROM categories).
ALLOWED_CATEGORIES = [
    "World News", "Politics & Policy", "Business & Finance", "Technology",
    "AI & Automation", "Science & Health", "Consumer & Gadgets",
    "Entertainment & Celebrity", "Lifestyle & Wellness", "Sport",
    "Crime & Justice", "Weird & Viral", "Opinion & Analysis", "Other",
]
_VALID_CATEGORIES = frozenset(ALLOWED_CATEGORIES)
_VALID_DECAY      = frozenset({"fast", "moderate", "slow"})

# Structured-output schema. Gemini enforces enums and required fields, so the
# response is guaranteed to be a JSON array of well-formed objects.
_RESPONSE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "index":    {"type": "INTEGER"},
            "score":    {"type": "INTEGER"},
            "reason":   {"type": "STRING"},
            "category": {"type": "STRING", "enum": ALLOWED_CATEGORIES},
            "decay":    {"type": "STRING", "enum": ["fast", "moderate", "slow"]},
        },
        "required": ["index", "score", "reason", "category", "decay"],
        "propertyOrdering": ["index", "score", "reason", "category", "decay"],
    },
}

# The scoring rubric, carried over verbatim from the old Ollama LLM-chain prompt
# so scoring behaviour is unchanged. The per-user `scoring_prompt` is injected
# into {scoring_prompt}; the article list into {article_list}.
_SYSTEM_TEMPLATE = """\
You are a news scoring assistant. You will be given a numbered list of news \
articles. Score every article in the list.

Return one JSON object per article. Use the article's NUMBER as the "index" \
field — copy it exactly. Do not invent indexes and do not skip any article.

SCORING GUIDANCE (what this particular reader cares about):
{scoring_prompt}

SCORE RUBRIC (0-100 integer):
0-39  = low interest — do not prioritise
40-74 = moderate interest — serve if buffer needs filling
75-100 = high priority — must hear

DECAY RUBRIC:
fast     = stale within 24h — breaking news, sports results, weather events,
           market moves, political flashpoints, anything with a specific date
           ("tomorrow", "today", "tonight"), celebrity moments tied to a live
           event (red carpet, awards night)
moderate = relevant for 2-4 days — crime stories still developing, ongoing
           legal cases, celebrity scandals with new details, earnings reports,
           short-term policy news
slow     = valid for weeks or longer — policy changes and new laws, economic
           trend analysis, health and science research, tech releases and
           product launches, geopolitical analysis, human interest stories,
           personal finance, property explainers, anything framed as "why this
           matters" rather than "what just happened"

DECAY TIE-BREAKER: a specific time reference ("tomorrow", "last night") leans
fast; an analysis/explainer ("why", "how", "the truth about") leans slow; when
genuinely unsure prefer slow.

REASON: a single sentence, 20 words max, explaining the score.
CATEGORY: choose the single best fit from the allowed list."""


def _build_article_list(articles: list[dict]) -> str:
    """Render articles as a numbered list the model scores by position."""
    lines = []
    for i, a in enumerate(articles, start=1):
        title = (a.get("title") or "").strip()
        desc  = (a.get("description") or "").strip()
        lines.append(f"{i}. {title}\n   {desc}")
    return "\n\n".join(lines)


async def score_batch(articles: list[dict], scoring_prompt: str) -> list[dict]:
    """
    Score a batch of articles in a single Gemini call.

    Returns a list of {"article_id", "score", "reason", "category", "decay"}.
    Items the model omits or returns with an out-of-range index are silently
    dropped (they simply stay unscored and get retried next run).

    Raises RuntimeError if GEMINI_API_KEY is unset, or ValueError if Gemini
    returns something unparseable (caller decides whether to skip the batch).
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set in the environment")
    if not articles:
        return []

    # position (1-based) -> real article_id. The model only ever sees positions.
    index_to_id = {i: a["article_id"] for i, a in enumerate(articles, start=1)}

    system_text = _SYSTEM_TEMPLATE.format(scoring_prompt=(scoring_prompt or "").strip())
    user_text   = "Score these articles:\n\n" + _build_article_list(articles)

    body = {
        "systemInstruction": {"parts": [{"text": system_text}]},
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": {
            "temperature":      0.2,
            "responseMimeType": "application/json",
            "responseSchema":   _RESPONSE_SCHEMA,
            "maxOutputTokens":  8192,
        },
    }

    async with httpx.AsyncClient(timeout=GEMINI_TIMEOUT) as client:
        resp = await client.post(
            GEMINI_API_URL,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=body,
        )

    if resp.status_code != 200:
        raise ValueError(f"Gemini HTTP {resp.status_code}: {resp.text[:300]}")

    payload = resp.json()
    try:
        candidate    = payload["candidates"][0]
        finish       = candidate.get("finishReason")
        raw_text     = candidate["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as exc:
        raise ValueError(f"Unexpected Gemini response shape: {payload!s:.300}") from exc

    if finish == "MAX_TOKENS":
        # Schema mode still returns valid-so-far JSON, but the tail may be cut.
        # Smaller batches are the fix; log loudly so it's visible.
        logger.warning("Gemini hit MAX_TOKENS on a %d-article batch — tail may be lost.",
                       len(articles))

    try:
        items = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Gemini returned non-JSON despite schema: {exc} | {raw_text[:300]}") from exc

    if not isinstance(items, list):
        raise ValueError("Gemini response was not a JSON array")

    results = []
    seen_indexes = set()
    for item in items:
        try:
            idx = int(item["index"])
        except (KeyError, TypeError, ValueError):
            logger.warning("Skipping Gemini item with bad index: %s", item)
            continue

        article_id = index_to_id.get(idx)
        if article_id is None:
            logger.warning("Gemini returned out-of-range index %s (batch size %d) — dropped.",
                           idx, len(articles))
            continue
        if idx in seen_indexes:
            logger.warning("Gemini returned duplicate index %s — keeping first.", idx)
            continue
        seen_indexes.add(idx)

        try:
            score = int(item["score"])
        except (KeyError, TypeError, ValueError):
            logger.warning("Skipping article_id=%s — bad score in %s", article_id, item)
            continue
        score = max(0, min(100, score))

        category = item.get("category", "Other")
        if category not in _VALID_CATEGORIES:
            category = "Other"
        decay = item.get("decay", "moderate")
        if decay not in _VALID_DECAY:
            decay = "moderate"

        results.append({
            "article_id": article_id,
            "score":      score,
            "reason":     (item.get("reason") or "").strip(),
            "category":   category,
            "decay":      decay,
        })

    missing = len(articles) - len(results)
    if missing:
        logger.info("Gemini batch: %d/%d articles scored (%d unscored, will retry next run).",
                    len(results), len(articles), missing)
    return results
