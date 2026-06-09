"""
Article content and image scraping helpers.

Kept separate from routers so the logic is testable and reusable
without importing FastAPI machinery.
"""

import re

import trafilatura
from bs4 import BeautifulSoup

CONTENT_MAX_CHARS = 20_000
MAX_IMAGES        = 5


def fetch_article_content(
    url: str,
    existing_content: str | None = None,
    existing_images: list | None = None,
) -> tuple[str, list[dict]]:
    """
    Fetch and extract the full text content and images from an article URL.

    If both `existing_content` and `existing_images` are already populated,
    the network fetch is skipped entirely — no request is made to the source site.

    If `existing_content` is populated but `existing_images` is None,
    text extraction is skipped but image extraction still runs
    (we still need to fetch the HTML for images).

    Returns (full_content, images) where:
        full_content — extracted article text, capped at CONTENT_MAX_CHARS
        images       — list of dicts with keys: url, alt, caption (up to MAX_IMAGES)
    """
    # If we already have everything, skip the network hit entirely
    if existing_content and existing_images is not None:
        return existing_content[:CONTENT_MAX_CHARS], existing_images

    html = trafilatura.fetch_url(url)

    # ── Text ─────────────────────────────────────────────────────────────────
    if existing_content:
        full_content = existing_content
    else:
        full_content = trafilatura.extract(html) or "" if html else ""

    # ── Images ───────────────────────────────────────────────────────────────
    images = []
    if html:
        soup = BeautifulSoup(html, "html.parser")
        article_body = (
            soup.find("div", itemprop="articleBody")
            or soup.find("div", {"class": re.compile(r"article-text|articleBody|mol-col|article-body", re.I)})
            or soup.find("article")
            or soup.find("div", id=re.compile(r"article", re.I))
            or soup.body
        )
        seen_urls = set()
        for img in (article_body or soup).find_all("img"):
            img_url = (
                img.get("src")
                or img.get("data-src")
                or img.get("data-lazy-src")
                or img.get("data-original")
                or ""
            )
            if not img_url:
                continue
            if img_url.startswith("data:"):
                continue
            if re.search(r"\.(gif|svg)$", img_url, re.I):
                continue
            if re.search(r"(pixel|tracker|spacer|icon|logo|1x1|blank)", img_url, re.I):
                continue
            try:
                if int(img.get("width", 0)) < 100 or int(img.get("height", 0)) < 100:
                    continue
            except (ValueError, TypeError):
                pass
            if img_url in seen_urls:
                continue
            seen_urls.add(img_url)

            caption = ""
            figcaption = img.find_next("figcaption")
            if figcaption:
                caption = figcaption.get_text(strip=True)
            else:
                for ancestor in img.parents:
                    caption_el = ancestor.find_next_sibling("p", class_=re.compile(r"caption", re.I))
                    if caption_el:
                        caption = caption_el.get_text(strip=True)
                        break

            images.append({
                "url":     img_url,
                "alt":     img.get("alt") or "",
                "caption": caption,
            })
            if len(images) >= MAX_IMAGES:
                break

    return full_content[:CONTENT_MAX_CHARS], images
