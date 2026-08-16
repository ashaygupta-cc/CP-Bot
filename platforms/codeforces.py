"""
platforms/codeforces.py — Codeforces adapter using the public REST API.
Problem ID format: '1234A'  (contest digits + problem letter, e.g. '1234A')

Rate-limit / Cloudflare resilience
───────────────────────────────────
Codeforces aggressively rate-limits and occasionally shows a Cloudflare
challenge page (HTTP 403) or throws transient 503s under load — exactly
the issue your browser-extension sync (codeforces.js) had to fight with
its 3x-5-minute-cooldown logic.

This adapter mirrors that defensive approach for the bot:
  • `_cf_get()` detects BOTH explicit block status codes (403/503) AND
    non-JSON / "cloudflare" bodies returned with a 200 (the sneaky case
    the JS extension also checks for).
  • It retries a couple of times with short backoff before giving up.
  • On a real block it raises `CFBlockedError` (not a generic RuntimeError)
    so checker.py can tell "CF is rate-limiting us" apart from other
    errors and respond correctly: SKIP Codeforces for this run instead of
    retrying with even more requests (which is what made things worse
    before).
  • `fetch_all_submissions()` is the ONLY network call checker.py should
    ever make per !check / per bulk-member — one call covers every CF
    problem via local filtering (`check_solved_from_submissions`), so CF
    hits never multiply with the number of assigned CF problems.
"""

import asyncio
import aiohttp
from platforms.base import PlatformAdapter, Submission

CF_API = "https://codeforces.com/api"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; CPBot/1.0; +https://codeforces.com)"
}


class CFBlockedError(RuntimeError):
    """Codeforces is actively rate-limiting us or showing a Cloudflare challenge."""
    pass


async def _cf_get(url: str, retries: int = 2, base_delay: float = 3.0) -> dict:
    """
    GET helper with Cloudflare/503 detection + short backoff retries.

    Raises CFBlockedError  -> CF is clearly blocking us, even after retries.
    Raises RuntimeError    -> any other failure (network, bad JSON, timeout).
    """
    last_err: Exception = RuntimeError("Codeforces request failed for an unknown reason.")

    for attempt in range(retries + 1):
        try:
            async with aiohttp.ClientSession(headers=_HEADERS) as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status in (403, 503):
                        last_err = CFBlockedError(
                            f"Codeforces is blocking requests (HTTP {r.status}) — "
                            "likely rate-limited or showing a Cloudflare challenge."
                        )
                    else:
                        ctype = r.headers.get("content-type", "")
                        if "application/json" not in ctype:
                            text = (await r.text())[:300].lower()
                            if "cloudflare" in text or "attention required" in text or "just a moment" in text:
                                last_err = CFBlockedError(
                                    "Codeforces returned a Cloudflare challenge page."
                                )
                            else:
                                last_err = RuntimeError("Codeforces returned a non-JSON response.")
                        else:
                            return await r.json()
        except asyncio.TimeoutError:
            last_err = RuntimeError("Codeforces request timed out.")
        except aiohttp.ClientError as e:
            last_err = RuntimeError(f"Codeforces network error: {e}")

        if attempt < retries:
            await asyncio.sleep(base_delay * (attempt + 1))  # e.g. 3s, then 6s

    raise last_err


class CodeforcesAdapter(PlatformAdapter):
    KEY  = "cf"
    NAME = "Codeforces"

    def format_problem_id(self, raw: str) -> str:
        return raw.strip().upper()

    def problem_url(self, problem_id: str) -> str | None:
        contest = "".join(filter(str.isdigit, problem_id))
        index   = "".join(filter(str.isalpha, problem_id)).upper()
        if contest and index:
            return f"https://codeforces.com/problemset/problem/{contest}/{index}"
        return None

    async def verify_handle(self, handle: str) -> tuple[bool, str]:
        url = f"{CF_API}/user.info?handles={handle}"
        try:
            data = await _cf_get(url, retries=1)
        except CFBlockedError as e:
            return False, f"⚠️ {e}"
        except Exception as e:
            return False, f"⚠️ Could not reach Codeforces: {e}"

        if data.get("status") == "OK":
            info   = data["result"][0]
            rating = info.get("rating", "unrated")
            rank   = info.get("rank", "unranked")
            return True, f"✅ Found `{info['handle']}` — {rank} (rating {rating})"
        return False, f"❌ Handle `{handle}` not found on Codeforces."

    async def get_recent_submissions(self, handle: str, limit: int = 20) -> list[Submission]:
        url = f"{CF_API}/user.status?handle={handle}&from=1&count={limit}"
        try:
            data = await _cf_get(url, retries=1)
        except CFBlockedError as e:
            raise RuntimeError(str(e))
        except Exception as e:
            raise RuntimeError(f"Codeforces API error: {e}")

        if data.get("status") != "OK":
            raise RuntimeError(data.get("comment", "Unknown CF API error"))

        results = []
        for sub in data["result"]:
            prob = sub.get("problem", {})
            cid  = prob.get("contestId", "")
            idx  = prob.get("index", "")
            results.append(Submission(
                problem_id = f"{cid}{idx}",
                title      = prob.get("name"),
                verdict    = sub.get("verdict", "?"),
                timestamp  = float(sub.get("creationTimeSeconds", 0)),
                language   = sub.get("programmingLanguage"),
                url        = f"https://codeforces.com/contest/{cid}/submission/{sub.get('id')}",
            ))
        return results

    async def check_solved(
        self, handle: str, problem_id: str, since_ts: float, until_ts: float = None
    ) -> tuple[bool, str]:
        """
        Single-problem CF check. NOTE: checker.py should almost never call
        this directly anymore — it's kept for other callers (e.g. a future
        !checkone command) but the bulk checker always prefers
        fetch_all_submissions() + check_solved_from_submissions() instead,
        which is zero extra network calls per additional problem.
        """
        contest = "".join(filter(str.isdigit, problem_id))
        index   = "".join(filter(str.isalpha, problem_id)).upper()

        if not contest or not index:
            return False, f"❌ Invalid CF problem ID `{problem_id}`. Use format `1234A`."

        url = f"{CF_API}/user.status?handle={handle}&from=1&count=100"
        try:
            data = await _cf_get(url, retries=1)
        except CFBlockedError as e:
            return False, f"⚠️ {e}"
        except Exception as e:
            return False, f"⚠️ CF API error: {e}"

        if data.get("status") != "OK":
            comment = data.get("comment", "")
            if "not found" in comment.lower():
                return False, f"❌ CF handle `{handle}` not found."
            return False, f"⚠️ CF API error: {comment}"

        for sub in data["result"]:
            if sub.get("verdict") != "OK":
                continue
            prob     = sub.get("problem", {})
            sub_cid  = str(prob.get("contestId", ""))
            sub_idx  = prob.get("index", "").upper()
            sub_time = float(sub.get("creationTimeSeconds", 0))

            if sub_cid == contest and sub_idx == index and sub_time >= since_ts and (until_ts is None or sub_time <= until_ts):
                return True, "✅ Accepted"

        return False, "❌ No accepted submission found within the time window."

    async def fetch_all_submissions(self, handle: str, count: int = 500) -> list[dict]:
        """
        Fetch up to `count` recent submissions in ONE API call.
        Called once per !check for ALL CF problems — avoids N separate
        user.status hits that trigger Codeforces 503s / Cloudflare blocks.

        Raises CFBlockedError specifically when CF is rate-limiting / Cloudflare-
        challenging us (after retries), so callers (checker.py) can back off
        CF entirely for this run instead of retrying with even more requests.
        """
        url = f"{CF_API}/user.status?handle={handle}&from=1&count={count}"
        try:
            data = await _cf_get(url, retries=2, base_delay=4.0)
        except CFBlockedError:
            raise
        except Exception as e:
            raise RuntimeError(f"CF API unreachable: {e}")

        if data.get("status") != "OK":
            comment = data.get("comment", "Unknown CF API error")
            raise RuntimeError(f"CF API error: {comment}")

        return data["result"]

    def check_solved_from_submissions(
        self,
        submissions: list[dict],
        problem_id: str,
        since_ts: float,
        until_ts: float | None = None,
    ) -> tuple[bool, str]:
        """
        Pure local filter — zero network calls.
        Used by checker.py after a single fetch_all_submissions() call.
        """
        contest = "".join(filter(str.isdigit, problem_id))
        index   = "".join(filter(str.isalpha, problem_id)).upper()

        if not contest or not index:
            return False, f"❌ Invalid CF problem ID `{problem_id}`. Use format `1234A`."

        for sub in submissions:
            if sub.get("verdict") != "OK":
                continue
            prob     = sub.get("problem", {})
            sub_cid  = str(prob.get("contestId", ""))
            sub_idx  = prob.get("index", "").upper()
            sub_time = float(sub.get("creationTimeSeconds", 0))

            if (
                sub_cid == contest
                and sub_idx == index
                and sub_time >= since_ts
                and (until_ts is None or sub_time <= until_ts)
            ):
                return True, "✅ Accepted"

        return False, "❌ No accepted submission found within the time window."