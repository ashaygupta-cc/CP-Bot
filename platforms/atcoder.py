"""
platforms/atcoder.py — AtCoder adapter using the kenkoooo community API.
Problem ID format: 'abc123_a'  (contest_id + '_' + problem letter)
e.g. AtCoder Beginner Contest 123, problem A  →  'abc123_a'
"""

import aiohttp
from platforms.base import PlatformAdapter, Submission

KENKOOOO = "https://kenkoooo.com/atcoder"


class AtCoderAdapter(PlatformAdapter):
    KEY  = "atcoder"
    NAME = "AtCoder"

    def format_problem_id(self, raw: str) -> str:
        return raw.strip().lower()

    def problem_url(self, problem_id: str) -> str | None:
        # 'abc123_a' → contest='abc123', task='abc123_a'
        parts = problem_id.split("_")
        if len(parts) >= 2:
            contest = "_".join(parts[:-1])
            return f"https://atcoder.jp/contests/{contest}/tasks/{problem_id}"
        return None

    async def verify_handle(self, handle: str) -> tuple[bool, str]:
        # kenkoooo returns submissions; an empty list means handle unknown
        url = f"{KENKOOOO}/atcoder-api/v3/user/submissions?user={handle}&from_second=0"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status == 400:
                        return False, f"❌ Handle `{handle}` not found on AtCoder."
                    data = await r.json()
        except Exception as e:
            return False, f"⚠️ Could not reach AtCoder API: {e}"

        if isinstance(data, list):
            solved = sum(1 for s in data if s.get("result") == "AC")
            return True, f"✅ Found `{handle}` — {solved} AC submissions on record."
        return False, f"❌ Handle `{handle}` not found on AtCoder."

    async def get_recent_submissions(self, handle: str, limit: int = 20) -> list[Submission]:
        # kenkoooo paginates by time, not count; fetch recent 100 and slice
        import time
        since = int(time.time()) - 90 * 24 * 3600    # last 90 days
        url = f"{KENKOOOO}/atcoder-api/v3/user/submissions?user={handle}&from_second={since}"
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=12)) as r:
                data = await r.json()

        if not isinstance(data, list):
            raise RuntimeError("Unexpected AtCoder API response.")

        # Sort newest first
        data.sort(key=lambda x: x.get("epoch_second", 0), reverse=True)
        results = []
        for sub in data[:limit]:
            contest  = sub.get("contest_id", "")
            pid      = sub.get("problem_id", "")
            results.append(Submission(
                problem_id = pid,
                title      = pid,
                verdict    = sub.get("result", "?"),
                timestamp  = float(sub.get("epoch_second", 0)),
                language   = sub.get("language"),
                url        = f"https://atcoder.jp/contests/{contest}/submissions/{sub.get('id')}",
            ))
        return results

    async def check_solved(self, handle: str, problem_id: str, since_ts: float) -> tuple[bool, str]:
        url = (
            f"{KENKOOOO}/atcoder-api/v3/user/submissions"
            f"?user={handle}&from_second={int(since_ts)}"
        )
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=12)) as r:
                    data = await r.json()
        except Exception as e:
            return False, f"⚠️ AtCoder API error: {e}"

        if not isinstance(data, list):
            return False, "⚠️ Unexpected AtCoder API response."

        pid = problem_id.lower()
        for sub in data:
            if sub.get("problem_id", "").lower() == pid and sub.get("result") == "AC":
                return True, "✅ Accepted"

        return False, "❌ No accepted submission found within the time window."
