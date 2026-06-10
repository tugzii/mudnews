import json
import logging
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DECAY_RATE = {
    "fast":     1.0,
    "moderate": 0.5,
    "slow":     0.0,
}

VALID_DECAY_VALUES = frozenset(DECAY_RATE.keys())


def effective_score(ai_score: float, decay: str, created_at: datetime) -> float:
    now       = datetime.now(timezone.utc)
    hours_old = max(0, (now - created_at).total_seconds() / 3600)
    rate      = DECAY_RATE.get(decay or "moderate", 0.5)
    return max(0, ai_score - rate * hours_old)


def parse_ai_score_payload(raw: str) -> dict:
    """
    Extract and validate a score payload from a raw LLM response string.

    The LLM is expected to return a JSON object somewhere in its output, e.g.:
        {"score": 82, "reason": "...", "category": "Tech", "article_id": 5,
         "user_id": 3, "decay": "moderate"}

    Returns a dict with keys:
        article_id (int), user_id (int), score (int), reason (str),
        category (str), decay (str)

    Raises ValueError if the JSON cannot be found or required fields are missing.
    """
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if not match:
        raise ValueError("No JSON object found in LLM response")

    try:
        parsed = json.loads(match.group())
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in LLM response: {exc}") from exc

    try:
        article_id = int(parsed["article_id"])
        user_id    = int(parsed["user_id"])
        score      = int(parsed["score"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Missing or invalid required field: {exc}") from exc

    reason   = parsed.get("reason", "")
    category = parsed.get("category", "Other")
    decay    = parsed.get("decay", "moderate")

    if decay not in VALID_DECAY_VALUES:
        logger.warning(
            "Invalid decay value %r for article %d — defaulting to 'moderate'",
            decay, article_id,
        )
        decay = "moderate"

    return {
        "article_id": article_id,
        "user_id":    user_id,
        "score":      score,
        "reason":     reason,
        "category":   category,
        "decay":      decay,
    }


def parse_voice_summary(raw: str) -> tuple[int, str]:
    """
    Extract article_id and voice summary from a raw LLM response.
    ARTICLE_ID can appear anywhere in the text (start, middle, end, inline).
    Everything else becomes the voice summary.
    """
    if not raw or not raw.strip():
        raise ValueError("Empty LLM response")

    match = re.search(r'ARTICLE_ID\s*:\s*(\d+)', raw.strip(), re.IGNORECASE)
    if not match:
        raise ValueError(f"No valid ARTICLE_ID found in LLM output. Tail: {raw[-200:]}")

    article_id = int(match.group(1))
    voice_summary = re.sub(r'ARTICLE_ID\s*:\s*\d+', '', raw, flags=re.IGNORECASE)
    voice_summary = re.sub(r'\n{3,}', '\n\n', voice_summary).strip()

    if not voice_summary:
        raise ValueError(f"Empty voice summary for article_id={article_id}")

    return article_id, voice_summary


def parse_ai_scores_batch(raw: str) -> list[dict]:
    """
    Extract and validate a batch of score payloads from a raw LLM response.
    Expects a JSON array. Skips invalid items with a warning.
    Raises ValueError if no valid array is found.
    """
    match = re.search(r'\[.*\]', raw, re.DOTALL)
    if not match:
        raise ValueError("No JSON array found in LLM batch response")

    try:
        items = json.loads(match.group())
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON array in LLM batch response: {exc}") from exc

    if not isinstance(items, list):
        raise ValueError("LLM batch response is not a JSON array")

    results = []
    for item in items:
        try:
            article_id = int(item["article_id"])
            user_id    = int(item["user_id"])
            score      = int(item["score"])
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("Skipping batch item — bad fields: %s | %s", exc, item)
            continue

        decay = item.get("decay", "moderate")
        if decay not in VALID_DECAY_VALUES:
            decay = "moderate"

        results.append({
            "article_id": article_id,
            "user_id":    user_id,
            "score":      score,
            "reason":     item.get("reason", ""),
            "category":   item.get("category", "Other"),
            "decay":      decay,
        })

    return results  # empty list is valid — nothing to score this run
