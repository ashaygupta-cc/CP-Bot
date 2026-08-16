"""
platforms/duel_lc_pool.py — LeetCode problem POOL for the duel system.

Separate from platforms/leetcode.py (handle verify + submission check via
recentAcSubmissionList). This module fetches the public problem list via
LeetCode's `problemsetQuestionList` GraphQL query, cached in memory, and
picks a random non-premium problem of a given difficulty.

Rate-limit safety (per your earlier confirmation):
  • Small random delay before each network call.
  • Realistic browser-like headers.
  • Full list fetched once per difficulty and cached — a duel/blitz match
    never needs to query LeetCode again once problems are already
    pre-fetched at match start.
"""

import asyncio
import random
import time
import aiohttp

LC_GQL = "https://leetcode.com/graphql"

_HEADERS = {
    "Content-Type": "application/json",
    "Referer": "https://leetcode.com",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

LIST_QUERY = """
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
_cache: dict[str, dict] = {}   # difficulty -> {"problems": [...], "ts": float}
_lock = asyncio.Lock()

DIFFICULTIES = {"easy": "Easy", "medium": "Medium", "hard": "Hard"}


async def _gql_with_backoff(query: str, variables: dict, retries: int = 2) -> dict:
    last_err = RuntimeError("LeetCode request failed.")
    for attempt in range(retries + 1):
        await asyncio.sleep(random.uniform(2, 5))
        try:
            async with aiohttp.ClientSession(headers=_HEADERS) as s:
                async with s.post(
                    LC_GQL, json={"query": query, "variables": variables},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as r:
                    if r.status == 200:
                        return await r.json()
                    last_err = RuntimeError(f"LeetCode HTTP {r.status}")
        except asyncio.TimeoutError:
            last_err = RuntimeError("LeetCode request timed out.")
        except aiohttp.ClientError as e:
            last_err = RuntimeError(f"LeetCode network error: {e}")
        if attempt < retries:
            await asyncio.sleep(3 * (attempt + 1))
    raise last_err


async def _load_pool(difficulty_key: str) -> list[dict]:
    difficulty = DIFFICULTIES[difficulty_key]
    now = time.time()
    cached = _cache.get(difficulty_key)
    if cached and (now - cached["ts"]) < CACHE_TTL:
        return cached["problems"]

    async with _lock:
        cached = _cache.get(difficulty_key)
        now = time.time()
        if cached and (now - cached["ts"]) < CACHE_TTL:
            return cached["problems"]

        variables = {
            "categorySlug": "",
            "limit": 400,
            "skip": 0,
            "filters": {"difficulty": difficulty},
        }
        data = await _gql_with_backoff(LIST_QUERY, variables)
        questions = (
            data.get("data", {}).get("problemsetQuestionList", {}).get("questions") or []
        )
        pool = [q for q in questions if not q.get("isPaidOnly")]
        _cache[difficulty_key] = {"problems": pool, "ts": time.time()}
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