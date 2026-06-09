import os
import psycopg2
from psycopg2.extras import RealDictCursor, execute_values


# ── Article-selection constants ────────────────────────────────────────────────
# Single source of truth for all pool thresholds and windows.
# queue.py and stories.py import these directly so there is only one place to
# change them.
LATEST_SCORE_MIN    = 50   # Pool A: minimum ai_score to be considered
LATEST_WINDOW_HOURS = 24   # Pool A: how far back to look (hours)
LATEST_POOL_SIZE    = 30   # Pool A: max articles per user after filtering

TOP_SCORE_MIN       = 70   # Pool B: minimum raw ai_score (no decay applied)
TOP_POOL_SIZE       = 40   # Pool B: max articles per user after filtering

# Default decay for articles where decay IS NULL.  Mirrors the scoring.py
# DECAY_RATE fallback and the UPDATE … WHERE decay IS NULL migration.
DEFAULT_DECAY       = "moderate"
# ──────────────────────────────────────────────────────────────────────────────


def get_conn():
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASS"],
    )


def get_dict_conn():
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASS"],
        cursor_factory=RealDictCursor,
    )


def get_unscored_articles(conn, limit: int = 200) -> list[dict]:
    """
    Return articles that have not yet been scored for every non-borrowing user.

    Each row is a dict with keys:
        article_id, title, description, user_id, scoring_prompt

    The limit acts as a memory safety net — the Pi has constrained RAM and
    n8n will fan out one item per row, so avoid fetching unbounded sets.
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            a.id          AS article_id,
            a.title,
            a.description,
            u.id          AS user_id,
            u.scoring_prompt
        FROM articles a
        CROSS JOIN users u
        WHERE u.borrows_scores_from IS NULL
          AND a.title       IS NOT NULL
          AND a.description IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM   article_user_scores aus
              WHERE  aus.article_id = a.id
                AND  aus.user_id    = u.id
          )
        ORDER BY a.id DESC
        LIMIT %s
        """,
        (limit,),
    )
    cols = ["article_id", "title", "description", "user_id", "scoring_prompt"]
    rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    cur.close()
    return rows


def insert_article_score(
    conn,
    article_id: int,
    user_id:    int,
    score:      int,
    reason:     str,
    category:   str,
    decay:      str,
) -> dict:
    """
    Write one AI score into article_user_scores and, if not already set,
    the decay value into articles.

    Returns a dict describing what happened:
        inserted (bool) — False means a score already existed (ON CONFLICT DO NOTHING).
        decay_updated (bool) — True if the articles.decay column was set this call.
    """
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO article_user_scores
            (article_id, user_id, ai_score, ai_reason, category_id, ai_scored_at)
        VALUES (
            %s, %s, %s, %s,
            (SELECT id FROM categories WHERE name = %s),
            NOW()
        )
        ON CONFLICT (article_id, user_id) DO NOTHING
        """,
        (article_id, user_id, score, reason, category),
    )
    inserted = cur.rowcount > 0

    # decay is article-level, not per-user.  First user to be scored wins;
    # subsequent runs are intentional no-ops here.
    cur.execute(
        """
        UPDATE articles
        SET    decay = %s
        WHERE  id    = %s
          AND  decay IS NULL
        """,
        (decay, article_id),
    )
    decay_updated = cur.rowcount > 0

    conn.commit()
    cur.close()
    return {"inserted": inserted, "decay_updated": decay_updated}


def upsert_articles(conn, rows: list[tuple]) -> dict:
    """
    Upsert a list of (title, description, url, published_at) tuples into articles.

    ON CONFLICT (url) updates title, description, and published_at.
    Returns {"inserted": int, "updated": int}.
    """
    if not rows:
        return {"inserted": 0, "updated": 0}

    cur = conn.cursor()
    results = execute_values(
        cur,
        """
        INSERT INTO articles (title, description, url, published_at)
        VALUES %s
        ON CONFLICT (url) DO UPDATE SET
            title        = EXCLUDED.title,
            description  = EXCLUDED.description,
            published_at = EXCLUDED.published_at
        RETURNING (xmax = 0) AS inserted
        """,
        rows,
        fetch=True,
    )
    inserted = sum(1 for r in results if r[0])
    updated  = sum(1 for r in results if not r[0])
    cur.close()
    return {"inserted": inserted, "updated": updated}


def cleanup_old_articles(conn, months: int = 3) -> int:
    """
    Delete articles older than `months` months.
    Returns the number of rows deleted.
    """
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM articles WHERE created_at < NOW() - INTERVAL '%s months'",
        (months,),
    )
    deleted = cur.rowcount
    cur.close()
    return deleted


def fix_null_decay(conn) -> int:
    """
    One-shot migration: set decay = 'moderate' for any articles where it is NULL.

    Safe to call repeatedly — subsequent runs are no-ops.
    Returns the number of rows updated.
    """
    cur = conn.cursor()
    cur.execute(
        "UPDATE articles SET decay = %s WHERE decay IS NULL",
        (DEFAULT_DECAY,),
    )
    updated = cur.rowcount
    conn.commit()
    cur.close()
    return updated


def select_article_pool(conn, user_id: int, mode: str, scoring,
                         extra_exclude: list | None = None) -> list[dict]:
    """
    Shared article-selection logic used by all three callers
    (get_articles_to_summarise, get_queue, fetch_story).

    Parameters
    ----------
    conn         : open psycopg2 connection
    user_id      : the score-owner user id (borrows_scores_from already resolved
                   by the caller when relevant)
    mode         : "latest" (Pool A) or "top" (Pool B)
    scoring      : the app.scoring module (avoids circular import)
    extra_exclude: additional article IDs to exclude (used by stories.py)

    Returns a list of dicts, sorted by descending effective_score (Pool A) or
    descending ai_score (Pool B).  Each dict has:
        article_id, url, title, voice_summary, full_content, images,
        ai_score, effective_score, decay, category, created_at

    Pool A applies decay to both filtering and ranking.
    Pool B filters and ranks by raw ai_score only — decay is intentionally
    not applied, so old high-quality articles never fall out of the top pool.
    """
    if mode == "latest":
        score_min   = LATEST_SCORE_MIN
        pool_size   = LATEST_POOL_SIZE
        time_clause = "AND a.published_at >= (DATE_TRUNC('day', NOW() AT TIME ZONE 'Australia/Brisbane') - INTERVAL '1 day') AT TIME ZONE 'Australia/Brisbane'"
        time_params = ()
    elif mode == "top":
        score_min   = TOP_SCORE_MIN
        pool_size   = TOP_POOL_SIZE
        time_clause = ""
        time_params = ()
    else:
        raise ValueError(f"Unknown mode: {mode!r}")

    # Build optional extra-exclude clause (stories.py passes previously-heard IDs)
    if extra_exclude:
        exclude_clause = f"AND a.id NOT IN ({','.join(['%s'] * len(extra_exclude))})"
        exclude_params = tuple(extra_exclude)
    else:
        exclude_clause = ""
        exclude_params = ()

    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT
            a.id, a.url, a.title, a.description, a.voice_summary, a.full_content, a.images,
            aus.ai_score, a.decay, a.created_at, a.published_at,
            c.name AS category,
            aus.rescued_at
        FROM articles a
        JOIN article_user_scores aus ON aus.article_id = a.id
        LEFT JOIN categories c       ON c.id = aus.category_id
        WHERE aus.user_id = %s
          AND (aus.ai_score >= %s OR aus.rescued_at IS NOT NULL)
          AND a.id NOT IN (
              SELECT article_id FROM article_interactions
              WHERE user_id = %s
                AND status IN ('read', 'skipped', 'declined')
          )
          {time_clause}
          {exclude_clause}
        ORDER BY
            CASE WHEN aus.rescued_at IS NOT NULL THEN 0 ELSE 1 END,
            aus.ai_score DESC
        LIMIT %s
        """,
        (user_id, score_min, user_id) + time_params + exclude_params + (pool_size * 3,),
    )

    candidates = []
    for row in cur.fetchall():
        aid, url, title, description, voice_summary, full_content, images, ai_score, decay, created_at, published_at, category, rescued_at = row

        # Treat NULL decay as 'moderate' consistently (mirrors DEFAULT_DECAY and
        # the fix_null_decay() migration).
        decay = decay or DEFAULT_DECAY

        is_rescued = rescued_at is not None

        if mode == "latest":
            # Pool A: filter and rank by raw ai_score — recency is already
            # enforced by the ai_scored_at time window in the SQL query, so
            # applying time-decay on top would zero-out old-but-recent scores.
            eff = scoring.effective_score(ai_score or 0, decay, created_at)
            if not is_rescued and (ai_score or 0) < score_min:
                continue
            sort_key = ai_score or 0
        else:
            # Pool B: filter and rank by raw ai_score — do NOT apply decay.
            # Rescued articles bypass the score floor entirely.
            eff      = scoring.effective_score(ai_score or 0, decay, created_at)
            sort_key = ai_score or 0

        candidates.append({
            "article_id":      aid,
            "url":             url,
            "title":           title,
            "description":     description,
            "voice_summary":   voice_summary,
            "full_content":    full_content,
            "content_fetched": full_content is not None,
            "images":          images or [],
            "ai_score":        ai_score,
            "effective_score": round(eff, 1),
            "decay":           decay,
            "category":        category,
            "created_at":      created_at,
            "published_at":    published_at,
            "rescued_at":      rescued_at,
            "_sort_key":       sort_key,
            "_rescued":        is_rescued,
        })

    cur.close()

    # Rescued articles pin to top; within each group sort by score descending.
    candidates.sort(key=lambda x: (0 if x["_rescued"] else 1, -x["_sort_key"]))
    for c in candidates:
        del c["_sort_key"]
        del c["_rescued"]

    return candidates[:pool_size]


def get_articles_to_summarise(conn, scoring) -> list[dict]:
    """
    Return articles that need voice summaries, across all non-borrowing users.

    Runs Pool A (latest, high-scoring, within time window) and Pool B
    (all-time top scorers) per user, deduplicates across users, and returns
    a list sorted by priority then effective score descending.

    Each dict contains:
        article_id, url, title, full_content, images, ai_score,
        effective_score, decay, queue ("latest" | "top")

    `scoring` must be the app.scoring module (passed in to avoid circular import).

    Delegates to select_article_pool() for the shared selection logic so that
    Pool A / Pool B behaviour is identical to what get_queue() and fetch_story()
    serve to clients.
    """
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM users WHERE borrows_scores_from IS NULL")
    users = cur.fetchall()
    cur.close()

    articles_to_summarise = {}
    article_priority      = {}
    article_eff_score     = {}

    for user_id, _user_name in users:

        # ── Priority 0: rescued articles ──────────────────────────────────────
        # Rescued articles jump the queue entirely regardless of score or pool.
        # We query directly rather than via select_article_pool so we can catch
        # articles that haven't been fetched yet (full_content IS NULL) —
        # those wouldn't appear in the pool but still need to be prioritised.
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                a.id, a.url, a.title, a.full_content, a.images,
                aus.ai_score, a.decay, a.created_at
            FROM articles a
            JOIN article_user_scores aus ON aus.article_id = a.id AND aus.user_id = %s
            WHERE aus.rescued_at IS NOT NULL
              AND a.voice_summary IS NULL
              AND a.id NOT IN (
                  SELECT article_id FROM article_interactions
                  WHERE user_id = %s AND status IN ('read', 'skipped', 'declined')
              )
            ORDER BY aus.rescued_at ASC
            """,
            (user_id, user_id),
        )
        for row in cur.fetchall():
            aid, url, title, full_content, images, ai_score, decay, created_at = row
            if aid not in articles_to_summarise:
                eff = scoring.effective_score(ai_score or 0, decay, created_at)
                articles_to_summarise[aid] = {
                    "article_id":      aid,
                    "url":             url,
                    "title":           title,
                    "full_content":    full_content,
                    "images":          images or [],
                    "ai_score":        ai_score,
                    "effective_score": round(eff, 1),
                    "decay":           decay,
                    "queue":           "rescued",
                }
                article_priority[aid]  = 0
                article_eff_score[aid] = 999  # always sort first within priority 0
        cur.close()

        # ── Pool A: latest unread articles within time window ─────────────────
        for art in select_article_pool(conn, user_id, "latest", scoring):
            if art["voice_summary"] is not None:
                continue  # already summarised
            aid = art["article_id"]
            if aid not in articles_to_summarise or 1 < article_priority.get(aid, 99):
                articles_to_summarise[aid] = {
                    **{k: art[k] for k in ("article_id", "url", "title", "full_content",
                                            "images", "ai_score", "effective_score", "decay")},
                    "queue": "latest",
                }
                article_priority[aid]  = 1
                article_eff_score[aid] = art["effective_score"]

        # ── Pool B: all-time top scorers ──────────────────────────────────────
        for art in select_article_pool(conn, user_id, "top", scoring):
            if art["voice_summary"] is not None:
                continue  # already summarised
            aid = art["article_id"]
            if aid not in articles_to_summarise:
                articles_to_summarise[aid] = {
                    **{k: art[k] for k in ("article_id", "url", "title", "full_content",
                                            "images", "ai_score", "effective_score", "decay")},
                    "queue": "top",
                }
                article_priority[aid]  = 2
                article_eff_score[aid] = art["effective_score"]

    return sorted(
        articles_to_summarise.values(),
        key=lambda a: (article_priority[a["article_id"]], -article_eff_score[a["article_id"]]),
    )


def update_article_content(
    conn,
    article_id:   int,
    full_content: str,
    images:       list,
) -> None:
    """
    Write scraped full_content and images back to the articles table.
    """
    import json as _json
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE articles
        SET full_content       = %s,
            content_fetched_at = NOW(),
            images             = %s
        WHERE id = %s
        """,
        (full_content, _json.dumps(images), article_id),
    )
    conn.commit()
    cur.close()


def update_voice_summary(conn, article_id: int, voice_summary: str) -> None:
    """
    Write a completed voice summary back to the articles table.
    """
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE articles
        SET voice_summary  = %s,
            summarised_at  = NOW()
        WHERE id = %s
        """,
        (voice_summary, article_id),
    )
    conn.commit()
    cur.close()
