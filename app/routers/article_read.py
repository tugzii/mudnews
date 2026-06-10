"""
Article chunk-reading endpoint for Alexa pagination.

GET /read-article?article_id=<id>&offset=<n>

Returns a ~2000-character chunk of full_content, breaking at the last
sentence boundary before the limit so Alexa never cuts mid-sentence.
Response:
  {
    "article_id": int,
    "content":    str,
    "has_more":   bool,
    "next_offset": int
  }
"""

import re
from fastapi import APIRouter, Depends, Query, HTTPException, Request
from fastapi.responses import JSONResponse

from app.dependencies import require_auth
from app.db import get_conn
from app.limiter import limiter

router = APIRouter()

CHUNK_SIZE = 2000


def _sentence_chunk(text: str, offset: int, size: int) -> tuple[str, bool, int]:
    """
    Return (chunk, has_more, next_offset).
    Cuts at the last sentence-ending punctuation (. ! ?) before offset+size.
    Falls back to a word boundary, then hard cut if no better split found.
    """
    segment = text[offset:offset + size]

    if len(segment) < size:
        # Reached end of text
        return segment.strip(), False, offset + len(segment)

    # Find last sentence boundary in the segment
    match = None
    for m in re.finditer(r'[.!?][\s"\')\]]*', segment):
        match = m
    if match:
        cut = match.end()
        chunk = segment[:cut].strip()
        next_offset = offset + cut
        return chunk, next_offset < len(text), next_offset

    # Fall back to last whitespace
    last_space = segment.rfind(' ')
    if last_space > size // 2:
        chunk = segment[:last_space].strip()
        next_offset = offset + last_space + 1
        return chunk, next_offset < len(text), next_offset

    # Hard cut
    return segment.strip(), True, offset + size


@router.get("/read-article")
@limiter.limit("60/minute")
async def read_article(
    request:    Request,
    article_id: int = Query(...),
    offset:     int = Query(default=0, ge=0),
    user:       str = Depends(require_auth),
):
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute(
        "SELECT full_content FROM articles WHERE id = %s",
        (article_id,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Article not found")

    full_content = row[0] or ""
    if not full_content:
        return JSONResponse({
            "article_id":  article_id,
            "content":     "No content available for this article.",
            "has_more":    False,
            "next_offset": 0,
        })

    chunk, has_more, next_offset = _sentence_chunk(full_content, offset, CHUNK_SIZE)

    return JSONResponse({
        "article_id":  article_id,
        "content":     chunk,
        "has_more":    has_more,
        "next_offset": next_offset,
    })
