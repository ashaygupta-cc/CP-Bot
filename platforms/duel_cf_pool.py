"""
platforms/duel_cf_pool.py — Codeforces problem POOL for the duel system.

Separate from platforms/codeforces.py (which handles handle verification
and submission checking). This module's only job is: "give me a random
problem at this rating, with these tag constraints, weighted away
from problems this pair has already played."

NEW: pick_cf_problems_at_ratings() — picks one problem per EXACT target
rating (duels.py now computes targets like [base-100, base+100, base+200]
from the players' average rating). Falls back to ±100 / ±200 bands if the
exact rating has no candidates.

Design choices (deliberately conservative, to never trip CF's rate limiter):
  • The full problemset (~10k problems) is fetched ONCE and cached in memory
    for CACHE_TTL seconds. Every duel problem-pick after that is a pure
    in-memory filter — zero extra network calls per match.
  • Uses the same block-detection idea as platforms/codeforces.py
    (CFBlockedError) so duels.py can show a clean error instead of hanging.
"""

import asyncio
import random
import time
import aiohttp

from platforms.codeforces import CFBlockedError

CF_API = "https://codeforces.com/api"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; CPBot-Duels/1.0; +https://codeforces.com)"
}

CACHE_TTL = 6 * 3600  # 6 hours
_cache = {"problems": None, "ts": 0.0}
_lock = asyncio.Lock()

# Pure-math tags — an ICPC problem must have at least one of these …
MATH_TAGS = {
    "number theory", "combinatorics", "probabilities", "geometry",
    "matrices", "fft", "chinese remainder theorem", "math", "algebra",
}
# … AND at least one of these algorithmic tags.
ALGO_TAGS = {
    "dp", "graphs", "trees", "greedy", "binary search", "strings",
    "two pointers", "bitmasks", "divide and conquer", "flows", "dsu",
    "shortest paths", "sortings", "implementation", "data structures",
    "constructive algorithms", "game theory", "hashing", "segment tree",
}


async def _cf_get(url: str, retries: int = 2, base_delay: float = 3.0) -> dict:
    last_err: Exception = RuntimeError("Codeforces request failed for an unknown reason.")
    for attempt in range(retries + 1):
        try:
            async with aiohttp.ClientSession(headers=_HEADERS) as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                    if r.status in (403, 503):
                        last_err = CFBlockedError(f"Codeforces is blocking requests (HTTP {r.status}).")
                    else:
                        ctype = r.headers.get("content-type", "")
                        if "application/json" not in ctype:
                            text = (await r.text())[:300].lower()
                            if "cloudflare" in text or "just a moment" in text:
                                last_err = CFBlockedError("Codeforces returned a Cloudflare challenge page.")
                            else:
                                last_err = RuntimeError("Codeforces returned a non-JSON response.")
                        else:
                            return await r.json()
        except asyncio.TimeoutError:
            last_err = RuntimeError("Codeforces request timed out.")
        except aiohttp.ClientError as e:
            last_err = RuntimeError(f"Codeforces network error: {e}")
        if attempt < retries:
            await asyncio.sleep(base_delay * (attempt + 1))
    raise last_err


async def _load_all_problems() -> list[dict]:
    now = time.time()
    if _cache["problems"] is not None and (now - _cache["ts"]) < CACHE_TTL:
        return _cache["problems"]
    async with _lock:
        now = time.time()
        if _cache["problems"] is not None and (now - _cache["ts"]) < CACHE_TTL:
            return _cache["problems"]
        data = await _cf_get(f"{CF_API}/problemset.problems")
        if data.get("status") != "OK":
            raise RuntimeError(data.get("comment", "CF problemset fetch failed"))
        problems = data["result"]["problems"]
        _cache["problems"] = problems
        _cache["ts"] = time.time()
        return problems


def _matches_icpc(tags: set[str]) -> bool:
    return bool(tags & MATH_TAGS) and bool(tags & ALGO_TAGS)


async def pick_cf_problem(
    min_rating: int,
    max_rating: int,
    pair_history: set[str],
    icpc: bool = False,
) -> dict | None:
    """
    Returns {"problem_id": "1234A", "title": ..., "rating": int, "url": ..., "topic": ...}
    or None if nothing matches. Weighted-random: problems already seen by
    this pair get weight 0.1 instead of being hard-excluded.
    """
    problems = await _load_all_problems()
    candidates, weights = [], []

    for p in problems:
        rating = p.get("rating")
        if rating is None or not (min_rating <= rating <= max_rating):
            continue
        pid = f"{p['contestId']}{p['index']}"
        tags = {t.lower() for t in p.get("tags", [])}
        if icpc and not _matches_icpc(tags):
            continue
        candidates.append((pid, p, rating))
        weights.append(0.1 if pid in pair_history else 1.0)

    if not candidates:
        return None

    pid, p, rating = random.choices(candidates, weights=weights, k=1)[0]
    tags = [t for t in p.get("tags", [])][:4]
    return {
        "problem_id": pid,
        "title": p.get("name"),
        "rating": rating,
        "url": f"https://codeforces.com/problemset/problem/{p['contestId']}/{p['index']}",
        "topic": ", ".join(t.title() for t in tags) if tags else None,
    }


async def _pick_closest_by_rating(
    target: int,
    pair_history: set[str],
    icpc: bool = False,
) -> dict | None:
    """
    Last-resort fallback: instead of giving up (or picking from an
    unbounded pool — which is how a newbie at 800 could end up with a
    2100-rated ICPC problem), scan every rated problem that matches the
    tag filter and return one from the small cluster CLOSEST to the
    target rating. This guarantees the fallback is always "nearby", never
    wildly off.
    """
    problems = await _load_all_problems()
    scored = []
    for p in problems:
        rating = p.get("rating")
        if rating is None:
            continue
        tags = {t.lower() for t in p.get("tags", [])}
        if icpc and not _matches_icpc(tags):
            continue
        pid = f"{p['contestId']}{p['index']}"
        scored.append((abs(rating - target), pid, p, rating))

    if not scored:
        return None

    scored.sort(key=lambda x: x[0])
    best_dist = scored[0][0]
    cluster = [s for s in scored if s[0] <= best_dist + 50][:10] or scored[:10]
    weights = [0.1 if pid in pair_history else 1.0 for (_, pid, _, _) in cluster]
    _, pid, p, rating = random.choices(cluster, weights=weights, k=1)[0]
    tags = [t for t in p.get("tags", [])][:4]
    return {
        "problem_id": pid,
        "title": p.get("name"),
        "rating": rating,
        "url": f"https://codeforces.com/problemset/problem/{p['contestId']}/{p['index']}",
        "topic": ", ".join(t.title() for t in tags) if tags else None,
    }


async def pick_cf_problems_at_ratings(
    target_ratings: list[int],
    pair_history: set[str],
    icpc: bool = False,
) -> list[dict]:
    """
    Pick one problem per EXACT target rating (with graceful widening).

    For each target rating R (clamped to [800, 3500]):
      1) try exactly R
      2) try [R-100, R+100]
      3) try [R-200, R+200]
      4) try [R-300, R+300]
      5) try [R-500, R+500]
      6) last resort: closest-rated match anywhere in the (tag-filtered)
         pool — never a random pick from a wide/unbounded band. This fixes
         newbies getting a 2100-rated ICPC problem when their target was
         ~800: ICPC mode requires BOTH a math-family tag and an algo-family
         tag, and that combination is genuinely rare below ~1000 rating, so
         the old ±200 cap often came up empty. Widening further, and
         falling back to "nearest available" rather than "give up", keeps
         the picked problem close to the intended difficulty even in that
         sparse zone.

    Problems already chosen in this match (or in pair history) are avoided.
    """
    chosen: list[dict] = []
    used_ids = set(pair_history)

    for raw in target_ratings:
        r = min(3500, max(800, int(raw)))
        pick = None
        for band in (0, 100, 200, 300, 500):
            pick = await pick_cf_problem(max(800, r - band), min(3500, r + band), used_ids, icpc=icpc)
            if pick:
                break
        if pick is None:
            pick = await _pick_closest_by_rating(r, used_ids, icpc=icpc)
            if pick:
                print(f"[CF_POOL] ⚠️ target {r} had no band match — used closest-rated fallback "
                      f"→ {pick['problem_id']} (rating {pick['rating']})", flush=True)
        if pick is None:
            print(f"[CF_POOL] ❌ no problem found near rating {r} (icpc={icpc}) — pool may be exhausted.", flush=True)
            continue
        pick["target_rating"] = r
        chosen.append(pick)
        used_ids.add(pick["problem_id"])
        print(f"[CF_POOL] target {r} → {pick['problem_id']} (rating {pick['rating']})", flush=True)

    return chosen


async def pick_many_cf_problems(
    min_rating: int, max_rating: int, pair_history: set[str], count: int, icpc: bool = False,
) -> list[dict]:
    """Legacy band-based picker (kept for compatibility)."""
    chosen: list[dict] = []
    used_ids = set(pair_history)
    for _ in range(count):
        pick = await pick_cf_problem(min_rating, max_rating, used_ids, icpc=icpc)
        if pick is None:
            break
        chosen.append(pick)
        used_ids.add(pick["problem_id"])
    return chosen