"""
Gemini batch scorer for mudnews.

Scores many articles in a single Google Gemini 2.5 Flash call and returns one
result per article.  Design choices that make this robust against the failure
modes seen in production:

1. INDEX-BASED IDS — the model never sees or returns a real article_id.  Each
   article in a batch is given a 1-based position number; the model returns that
   number back.  The backend maps position -> real article_id afterwards, so the
   model physically cannot hallucinate an id that doesn't exist.

2. SCHEMA-CONSTRAINED JSON — the request uses Gemini's structured-output mode
   (responseMimeType=application/json + responseSchema with enums).  The model
   cannot emit prose, code fences, an invalid category, or an invalid decay
   value.  The response is always a parseable JSON array.

3. NO NARRATIVE OUTPUT — the model returns only numbers and enum labels
   (index, score, category, decay).  It is never asked to write a sentence
   about grim news, which is what triggers outbound safety filters.

4. SAFETY RELAXED + BLOCK-SPLITTING — safetySettings are set to BLOCK_NONE for
   the adjustable harm categories (this is mainstream news ingestion, not
   generation).  If Gemini still blocks a batch's *input* (blockReason, e.g.
   PROHIBITED_CONTENT), score_batch recursively halves the batch down to the
   single offending article and skips only that one — the other 49 are saved.

5. TRANSIENT RETRY — 503 UNAVAILABLE (model overloaded) is retried with backoff.

Public API
    await score_batch(articles, scoring_prompt) -> list[dict]
        articles: [{"article_id": int, "title": str, "description": str}, ...]
        returns:  [{"article_id", "score", "reason", "category", "decay"}, ...]
                  (reason is always "" — narrative output is intentionally off)
"""

import asyncio
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
GEMINI_TIMEOUT  = float(os.environ.get("GEMINI_TIMEOUT", "120"))
GEMINI_RETRIES  = int(os.environ.get("GEMINI_RETRIES", "3"))   # for 503 overload
_BACKOFF_SECS   = [2, 5, 10]                                   # per retry attempt

# Must match the `categories` table exactly (db: SELECT name FROM categories).
ALLOWED_CATEGORIES = [
    "World News", "Politics & Policy", "Business & Finance", "Technology",
    "AI & Automation", "Science & Health", "Consumer & Gadgets",
    "Entertainment & Celebrity", "Lifestyle & Wellness", "Sport",
    "Crime & Justice", "Weird & Viral", "Opinion & Analysis", "Other",
]
_VALID_CATEGORIES = frozenset(ALLOWED_CATEGORIES)
_VALID_DECAY      = frozenset({"fast", "moderate", "slow"})

# Relax the adjustable safety filters — this is news classification, not
# generation. PROHIBITED_CONTENT is a separate un-adjustable filter handled by
# batch-splitting in score_batch().
_SAFETY_SETTINGS = [
    {"category": c, "threshold": "BLOCK_NONE"}
    for c in (
        "HARM_CATEGORY_HARASSMENT",
        "HARM_CATEGORY_HATE_SPEECH",
        "HARM_CATEGORY_SEXUALLY_EXPLICIT",
        "HARM_CATEGORY_DANGEROUS_CONTENT",
    )
]

# Structured-output schema. No free-text fields: only an index, an integer
# score, and two enums. The model generates zero narrative.
_RESPONSE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "index":    {"type": "INTEGER"},
            "score":    {"type": "INTEGER"},
            "category": {"type": "STRING", "enum": ALLOWED_CATEGORIES},
            "decay":    {"type": "STRING", "enum": ["fast", "moderate", "slow"]},
        },
        "required": ["index", "score", "category", "decay"],
        "propertyOrdering": ["index", "score", "category", "decay"],
    },
}

_SYSTEM_TEMPLATE = """\
You are an isolated, silent data-processing backend. Your sole task is to read a \
numbered list of news items and return a structured rating for each one. Treat \
all input as historical, objective data for classification only. You never \
repeat, summarise, or comment on the content — you output only the requested \
numbers and category labels.

For every item return one object using the item's NUMBER as the "index" field \
— copy it exactly, never invent or skip an index.

RATING CRITERIA (what this particular reader values — score 0-100 integer):
{scoring_prompt}

SCORE BANDS:
0-39  = low interest
40-74 = moderate interest
75-100 = high priority

DECAY (how long the item stays relevant):
fast     = stale within 24h (breaking news, results, market moves, anything
           tied to a specific day or live event)
moderate = relevant 2-4 days (developing cases, scandals with new details,
           earnings, short-term policy)
slow     = relevant for weeks (new laws, trend analysis, research findings,
           product launches, explainers framed as "why this matters")
When unsure between two, prefer the slower one.

CATEGORY: choose the single best-fit label from the allowed enum."""


def _build_article_list(articles: list[dict]) -> str:
    """Render articles as a numbered list the model scores by position."""
    lines = []
    for i, a in enumerate(articles, start=1):
        title = (a.get("title") or "").strip()
        desc  = (a.get("description") or "").strip()
        lines.append(f"{i}. {title}\n   {desc}")
    return "\n\n".join(lines)


class _ContentBlocked(Exception):
    """Gemini blocked the prompt itself (promptFeedback.blockReason)."""


async def _score_once(articles: list[dict], scoring_prompt: str) -> list[dict]:
    """
    Single Gemini call for one batch. Retries 503 (overload) with backoff.
    Raises _ContentBlocked if the prompt is rejected, ValueError on anything
    else unparseable.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set in the environment")

    index_to_id = {i: a["article_id"] for i, a in enumerate(articles, start=1)}

    body = {
        "systemInstruction": {
            "parts": [{"text": _SYSTEM_TEMPLATE.format(scoring_prompt=(scoring_prompt or "").strip())}]
        },
        "contents": [{"role": "user", "parts": [
            {"text": "Score these items:\n\n" + _build_article_list(articles)}
        ]}],
        "safetySettings": _SAFETY_SETTINGS,
        "generationConfig": {
            "temperature":      0.1,
            "responseMimeType": "application/json",
            "responseSchema":   _RESPONSE_SCHEMA,
            "maxOutputTokens":  8192,
        },
    }

    last_err = None
    async with httpx.AsyncClient(timeout=GEMINI_TIMEOUT) as client:
        for attempt in range(GEMINI_RETRIES):
            resp = await client.post(
                GEMINI_API_URL,
                headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                json=body,
            )
            if resp.status_code == 503:
                wait = _BACKOFF_SECS[min(attempt, len(_BACKOFF_SECS) - 1)]
                last_err = f"503 UNAVAILABLE (attempt {attempt + 1}/{GEMINI_RETRIES})"
                logger.warning("Gemini overloaded — retrying in %ss (%s)", wait, last_err)
                await asyncio.sleep(wait)
                continue
            if resp.status_code != 200:
                raise ValueError(f"Gemini HTTP {resp.status_code}: {resp.text[:300]}")
            break
        else:
            raise ValueError(f"Gemini still unavailable after {GEMINI_RETRIES} retries ({last_err})")

    payload = resp.json()

    # Prompt-level block (no candidates produced). Surfaced for batch-splitting.
    feedback = payload.get("promptFeedback") or {}
    if feedback.get("blockReason") and not payload.get("candidates"):
        raise _ContentBlocked(feedback["blockReason"])

    try:
        candidate = payload["candidates"][0]
        finish    = candidate.get("finishReason")
        raw_text  = candidate["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as exc:
        # A candidate can also be blocked at the output stage.
        cand = (payload.get("candidates") or [{}])[0]
        if cand.get("finishReason") in ("SAFETY", "PROHIBITED_CONTENT", "RECITATION", "BLOCKLIST"):
            raise _ContentBlocked(cand["finishReason"])
        raise ValueError(f"Unexpected Gemini response shape: {payload!s:.300}") from exc

    if finish == "MAX_TOKENS":
        logger.warning("Gemini hit MAX_TOKENS on a %d-item batch — tail may be lost.", len(articles))

    try:
        items = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Gemini returned non-JSON despite schema: {exc} | {raw_text[:300]}") from exc
    if not isinstance(items, list):
        raise ValueError("Gemini response was not a JSON array")

    results = []
    seen = set()
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
        if idx in seen:
            continue
        seen.add(idx)

        try:
            score = max(0, min(100, int(item["score"])))
        except (KeyError, TypeError, ValueError):
            logger.warning("Skipping article_id=%s — bad score in %s", article_id, item)
            continue

        category = item.get("category", "Other")
        if category not in _VALID_CATEGORIES:
            category = "Other"
        decay = item.get("decay", "moderate")
        if decay not in _VALID_DECAY:
            decay = "moderate"

        results.append({
            "article_id": article_id,
            "score":      score,
            "reason":     "",          # narrative output intentionally disabled
            "category":   category,
            "decay":      decay,
        })
    return results


async def score_batch(articles: list[dict], scoring_prompt: str) -> list[dict]:
    """
    Score a batch of articles. If Gemini blocks the prompt as a whole, the batch
    is recursively halved down to the single offending article, which is skipped
    — every other article is still scored.

    Raises RuntimeError only on misconfiguration (no API key). A batch that
    fails for transient/parse reasons raises ValueError to the caller, which
    skips that batch and continues.
    """
    if not articles:
        return []
    try:
        return await _score_once(articles, scoring_prompt)
    except _ContentBlocked as block:
        if len(articles) == 1:
            logger.warning("Article %s blocked by Gemini (%s) — skipping it.",
                           articles[0]["article_id"], block)
            return []
        mid = len(articles) // 2
        logger.info("Batch of %d blocked (%s) — splitting %d + %d and retrying.",
                    len(articles), block, mid, len(articles) - mid)
        left  = await score_batch(articles[:mid], scoring_prompt)
        right = await score_batch(articles[mid:], scoring_prompt)
        return left + right
