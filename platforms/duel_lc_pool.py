"""
platforms/duel_lc_pool.py — LeetCode problem POOL for the duel system.

Separate from platforms/leetcode.py (handle verify + submission check via
recentAcSubmissionList). This module fetches the public problem list and
caches it in memory, then picks a random non-premium problem of a given
difficulty.

FIX (was throwing HTTP 400 on every fetch):
  The old `questionList` GraphQL query has been deprecated by LeetCode and
  now returns HTTP 400 for most callers. Fetching went through this single
  query with no fallback, so `_load_pool` raised on every call and duels
  couldn't start.

  Fixed with a three-tier fallback chain, each tier only tried if the
  previous one fails:
    1. REST  `/api/problems/all/`            — simple, stable, unauthenticated,
                                                 returns ALL difficulties in one
                                                 call (so we fetch it once and
                                                 filter in memory, instead of
                                                 once per difficulty).
    2. GraphQL `problemsetQuestionListV2`     — the current schema LeetCode's
                                                 own site uses.
    3. GraphQL `questionList` (legacy)        — kept as a last resort in case
                                                 REST and V2 both get blocked.

Rate-limit safety (per your earlier confirmation):
  • Small random delay before each network call.
  • Realistic browser-like headers (aligned with platforms/leetcode.py style).
  • Full list fetched once (all difficulties together) and cached — a
    duel/blitz match never needs to query LeetCode again once problems are
    already pre-fetched at match start.
"""

import asyncio
import logging
import random
import time
import aiohttp

logger = logging.getLogger(__name__)

LC_GQL = "https://leetcode.com/graphql"
LC_REST_ALL = "https://leetcode.com/api/problems/all/"

_HEADERS = {
    "Content-Type": "application/json",
    "Referer": "https://leetcode.com",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

# Current schema LeetCode's own site uses as of 2025/2026.
QUESTION_LIST_V2_QUERY = """
query problemsetQuestionListV2($categorySlug: String, $limit: Int, $skip: Int, $filters: QuestionFilterInput) {
  problemsetQuestionListV2(categorySlug: $categorySlug, limit: $limit, skip: $skip, filters: $filters) {
    questions {
      titleSlug
      title
      difficulty
      paidOnly
    }
    totalLength
    hasMore
  }
}
"""

# Deprecated (HTTP 400 for most callers as of this fix) — kept only as a
# last-resort fallback in case both REST and V2 are unavailable.
LEGACY_QUESTION_LIST_QUERY = """
query problemsetQuestionList($categorySlug: String, $limit: Int, $skip: Int, $filters: QuestionListFilterInput) {
  problemsetQuestionList: questionList(categorySlug: $categorySlug, limit: $limit, skip: $skip, filters: $filters) {
    total: totalNum
    questions: data {
      difficulty
      title
      titleSlug
      isPaidOnly
    }
  }
}
"""

CACHE_TTL = 6 * 3600
# Single cache for the full (all-difficulty) problem list — fetched once via
# whichever tier succeeds, then filtered per-difficulty in memory.
_all_cache: dict | None = None  # {"problems": [...], "ts": float}
_lock = asyncio.Lock()

DIFFICULTIES = {"easy": "Easy", "medium": "Medium", "hard": "Hard"}
_LEVEL_TO_DIFFICULTY = {1: "Easy", 2: "Medium", 3: "Hard"}


async def _sleep_jitter() -> None:
    await asyncio.sleep(random.uniform(2, 5))


async def _fetch_rest_all() -> list[dict]:
    """Tier 1: REST /api/problems/all/ — returns every problem, every
    difficulty, in a single unauthenticated call."""
    await _sleep_jitter()
    async with aiohttp.ClientSession(headers=_HEADERS) as s:
        async with s.get(
            LC_REST_ALL, timeout=aiohttp.ClientTimeout(total=15)
        ) as r:
            if r.status != 200:
                raise RuntimeError(f"LeetCode REST HTTP {r.status}")
            data = await r.json()

    pairs = data.get("stat_status_pairs") or []
    problems = []
    for p in pairs:
        stat = p.get("stat") or {}
        level = (p.get("difficulty") or {}).get("level")
        difficulty = _LEVEL_TO_DIFFICULTY.get(level)
        slug = stat.get("question__title_slug")
        title = stat.get("question__title")
        if not (difficulty and slug and title):
            continue
        problems.append({
            "titleSlug": slug,
            "title": title,
            "difficulty": difficulty,
            "isPaidOnly": bool(p.get("paid_only")),
        })
    if not problems:
        raise RuntimeError("LeetCode REST returned no usable problems.")
    return problems


async def _gql_post(query: str, variables: dict) -> dict:
    async with aiohttp.ClientSession(headers=_HEADERS) as s:
        async with s.post(
            LC_GQL, json={"query": query, "variables": variables},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as r:
            if r.status != 200:
                # Log the response body — a GraphQL 400 includes the exact
                # schema error ("Unknown argument…", "exceeds maximum…"),
                # which is the only way to debug this from Render logs.
                body = ""
                try:
                    body = (await r.text())[:300]
                except Exception:
                    pass
                print(f"[LC_POOL] GQL HTTP {r.status} — body: {body}", flush=True)
                raise RuntimeError(f"LeetCode HTTP {r.status}")
            data = await r.json(content_type=None)
            # GraphQL can return 200 with an "errors" array — surface it.
            if isinstance(data, dict) and data.get("errors"):
                msg = str(data["errors"])[:300]
                print(f"[LC_POOL] GQL 200-with-errors: {msg}", flush=True)
                raise RuntimeError(f"LeetCode GraphQL error: {msg}")
            return data


def _parse_v2_questions(data: dict) -> list[dict]:
    payload = (data.get("data") or {}).get("problemsetQuestionListV2") or {}
    return payload.get("questions") or []


async def _fetch_gql_v2_all() -> list[dict]:
    """Tier 2: current problemsetQuestionListV2 schema, unfiltered so we get
    every difficulty back.

    LeetCode's server caps `limit` (their own site pages at 100); a huge
    limit like 3500 is one plausible source of HTTP 400. Strategy:
      a) try one big page (limit 1000 — often accepted),
      b) if that errors, paginate with limit=100 across the list, sampling
         pages from the front, middle, and deep end so all three
         difficulties are represented (hards live deeper in the list).
    """
    questions: list[dict] = []
    # LeetCode's V2 schema REQUIRES filterCombineType inside filters —
    # an empty {} is rejected with HTTP 400 (verified from live logs).
    v2_filters = {"filterCombineType": "ALL"}
    await _sleep_jitter()
    try:
        data = await _gql_post(QUESTION_LIST_V2_QUERY,
                               {"categorySlug": "", "limit": 1000, "skip": 0, "filters": v2_filters})
        questions = _parse_v2_questions(data)
    except Exception as e:
        print(f"[LC_POOL] V2 single-page fetch failed ({e}); trying paginated fallback…", flush=True)
        # Sample pages across the problemset: fronts are easy/medium-heavy,
        # deeper skips pick up mediums/hards. ~8 quick requests, cached 6h.
        for skip in (0, 100, 400, 800, 1200, 1800, 2400, 3000):
            try:
                await asyncio.sleep(random.uniform(0.4, 1.0))
                data = await _gql_post(QUESTION_LIST_V2_QUERY,
                                       {"categorySlug": "", "limit": 100, "skip": skip, "filters": v2_filters})
                page = _parse_v2_questions(data)
                if not page:
                    break  # ran past the end of the list
                questions.extend(page)
            except Exception as page_err:
                print(f"[LC_POOL] V2 page skip={skip} failed: {page_err}", flush=True)
                break
    if not questions:
        raise RuntimeError("LeetCode GraphQL V2 returned no questions.")

    problems = []
    for q in questions:
        slug = q.get("titleSlug")
        title = q.get("title")
        difficulty_raw = (q.get("difficulty") or "").capitalize()
        if not (slug and title and difficulty_raw in DIFFICULTIES.values()):
            continue
        problems.append({
            "titleSlug": slug,
            "title": title,
            "difficulty": difficulty_raw,
            "isPaidOnly": bool(q.get("paidOnly")),
        })
    if not problems:
        raise RuntimeError("LeetCode GraphQL V2 returned no usable problems.")
    return problems


async def _fetch_gql_legacy_all() -> list[dict]:
    """Tier 3 (last resort): the deprecated questionList query, called once
    per difficulty since its filter is mandatory-shaped that way."""
    problems = []
    for difficulty in DIFFICULTIES.values():
        await _sleep_jitter()
        variables = {
            "categorySlug": "",
            "limit": 400,
            "skip": 0,
            "filters": {"difficulty": difficulty.upper()},  # DifficultyEnum: EASY/MEDIUM/HARD (verified from live 400 body)
        }
        data = await _gql_post(LEGACY_QUESTION_LIST_QUERY, variables)
        questions = (
            data.get("data", {}).get("problemsetQuestionList", {}).get("questions") or []
        )
        for q in questions:
            problems.append({
                "titleSlug": q["titleSlug"],
                "title": q["title"],
                "difficulty": q["difficulty"],
                "isPaidOnly": bool(q.get("isPaidOnly")),
            })
    if not problems:
        raise RuntimeError("LeetCode legacy GraphQL query returned no questions.")
    return problems


async def _fetch_with_retries(fetch_fn, retries: int = 2) -> list[dict]:
    last_err: Exception = RuntimeError("LeetCode request failed.")
    for attempt in range(retries + 1):
        try:
            return await fetch_fn()
        except asyncio.TimeoutError:
            last_err = RuntimeError("LeetCode request timed out.")
        except aiohttp.ClientError as e:
            last_err = RuntimeError(f"LeetCode network error: {e}")
        except RuntimeError as e:
            last_err = e
        if attempt < retries:
            await asyncio.sleep(3 * (attempt + 1))
    raise last_err


async def _load_all_problems() -> list[dict]:
    global _all_cache
    now = time.time()
    if _all_cache and (now - _all_cache["ts"]) < CACHE_TTL:
        return _all_cache["problems"]

    async with _lock:
        now = time.time()
        if _all_cache and (now - _all_cache["ts"]) < CACHE_TTL:
            return _all_cache["problems"]

        problems: list[dict] | None = None
        # Order (verified from live Render logs): the GraphQL endpoint is
        # reachable from Render (submission checks use it all day), while
        # REST /api/problems/all/ gets a Cloudflare HTML page there. So
        # GraphQL goes first for fast success; REST is kept as a last
        # resort for environments where it does work.
        for tier_name, fetch_fn in (
            ("GraphQL problemsetQuestionListV2", _fetch_gql_v2_all),
            ("GraphQL questionList (legacy)", _fetch_gql_legacy_all),
            ("REST /api/problems/all/", _fetch_rest_all),
        ):
            try:
                problems = await _fetch_with_retries(fetch_fn)
                logger.info("LeetCode pool fetched via %s (%d problems).", tier_name, len(problems))
                break
            except Exception as e:
                logger.warning("LeetCode pool fetch tier '%s' failed: %s", tier_name, e)
                continue

        if problems is None:
            # All tiers failed — keep any stale cache rather than nothing,
            # otherwise surface an empty pool.
            if _all_cache:
                logger.error("All LeetCode fetch tiers failed; serving stale cache.")
                return _all_cache["problems"]
            logger.error("All LeetCode fetch tiers failed; no cache available.")
            return []

        _all_cache = {"problems": problems, "ts": time.time()}
        return problems


async def _load_pool(difficulty_key: str) -> list[dict]:
    difficulty = DIFFICULTIES[difficulty_key]
    all_problems = await _load_all_problems()
    pool = [
        q for q in all_problems
        if q["difficulty"] == difficulty and not q.get("isPaidOnly")
    ]
    return pool


async def pick_lc_problem(difficulty_key: str, pair_history: set[str]) -> dict | None:
    """
    difficulty_key: 'easy' | 'medium' | 'hard'
    Returns {"problem_id": slug, "title": ..., "difficulty": "Easy", "url": ...}
    Weighted-random, same 0.1 weight for already-seen problems as CF.
    """
    difficulty_key = difficulty_key.lower()
    if difficulty_key not in DIFFICULTIES:
        return None
    pool = await _load_pool(difficulty_key)
    if not pool:
        return None

    weights = [0.1 if q["titleSlug"] in pair_history else 1.0 for q in pool]
    q = random.choices(pool, weights=weights, k=1)[0]
    return {
        "problem_id": q["titleSlug"],
        "title": q["title"],
        "difficulty": q["difficulty"],
        "url": f"https://leetcode.com/problems/{q['titleSlug']}/",
    }


async def pick_sequence(num_problems: int, pair_history: set[str]) -> list[dict]:
    """
    Pick problem sequence for LeetCode matches.

    num_problems == 2: Medium + Medium (for 2-problem format)
    num_problems == 3: Easy + Medium + Hard (for 3-problem format, Bo3)
    """
    if num_problems == 2:
        difficulties = ("medium", "medium")
    else:  # num_problems == 3 or default
        difficulties = ("easy", "medium", "hard")

    used = set(pair_history)
    out = []
    for diff in difficulties:
        pick = await pick_lc_problem(diff, used)
        if pick:
            out.append(pick)
            used.add(pick["problem_id"])
    return out