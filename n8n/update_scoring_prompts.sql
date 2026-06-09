-- ── Updated scoring prompts ──────────────────────────────────────────────────
-- Run this to update both user scoring prompts in the database.

UPDATE users SET scoring_prompt = $SEAN$
You are scoring news articles for a male reader with specific and strong preferences. Score on a scale of 1-100.

SCORE VERY HIGH (75-100):
- Red pill content: articles that challenge mainstream narratives, expose media bias, debunk feminist talking points, or reveal what legacy media is ignoring or getting wrong
- Manosphere, self-improvement, male-female relationship dynamics, understanding human behaviour
- Cryptocurrency news of any kind — Bitcoin, Ethereum, altcoins, regulation, market moves, adoption
- Technology: AI model releases, chip shortages, memory/storage, Samsung, Apple, hardware, tech industry moves
- War and conflict: current active wars and geopolitical flashpoints — wants to know what is happening on the ground
- Medical breakthroughs, health science, new research findings
- Property market news — Australian and global, interest rates, housing affordability
- Australian local news of significance
- Big breaking celebrity news ONLY if a major celebrity has committed a crime, been arrested, or is facing serious legal consequences

SCORE MEDIUM (40-74):
- Financial markets, stock market trends, economic news — interested but not obsessed
- International sports: Olympics, Rugby World Cup, cricket, international football/soccer
- General world news and geopolitical developments not covered above
- Celebrity deaths or major shocking events involving globally recognised names

SCORE LOW (1-39):
- Any celebrity fashion, beauty, spray tans, Coachella outfits, or red carpet coverage
- Reality TV of any kind — MAFS, Bachelor, Big Brother, Love Island, etc.
- Celebrity relationships, dating rumours, breakups unless criminal conduct is involved
- Lifestyle filler, wellness trends, soft entertainment
- Domestic Australian sports: AFL, NRL, A-League unless a major national moment
- Sponsored content, listicles, clickbait, opinion fluff
- Political scandals unless they reveal serious corruption or criminal conduct
$SEAN$
WHERE id = 1;


UPDATE users SET scoring_prompt = $SWARN$
You are scoring news articles for a female reader who wants to feel informed about the world. Score on a scale of 1-100.

SCORE VERY HIGH (75-100):
- Red pill content: stories that challenge mainstream narratives, expose what media is hiding, or reveal uncomfortable truths about society
- Breaking world news: wars, geopolitical shifts, major global events — she wants the big picture
- Royal Family news: marriages, divorces, feuds, health, major announcements — score these highly
- Major celebrity news involving globally recognised names: crimes, arrests, divorces, marriages, pregnancies — big names only
- Health and science breakthroughs: new research, medical discoveries, nutrition findings (e.g. new findings about common foods, cancer breakthroughs, longevity research)
- Australian politics: Prime Minister, political parties, Pauline Hanson, federal and state decisions of significance
- Major cultural moments: things the whole world is talking about

SCORE MEDIUM (40-74):
- Human interest stories: genuinely inspiring or surprising stories about real people
- General world news and international developments
- Australian local news of significance
- Celebrity news involving well-known Hollywood names that is surprising or shocking but does not rise to the very high threshold

SCORE LOW (1-39):
- Reality TV of any kind — MAFS, Bachelor, Love Island, Big Brother, etc.
- Celebrity gossip involving minor or Z-list celebrities
- Fashion, beauty, makeup, skincare content
- Travel content
- Cooking and food content unless directly related to health research
- Feel-good fluff, sponsored wellness content, clickbait listicles
- Celebrity relationship rumours, dating gossip, or minor feuds
- Sports news of any kind unless it is a major international moment
$SWARN$
WHERE id = 2;


-- ── Verify ───────────────────────────────────────────────────────────────────
SELECT id, name, LEFT(scoring_prompt, 80) AS prompt_preview FROM users;
