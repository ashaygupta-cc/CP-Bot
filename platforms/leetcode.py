"""
platforms/leetcode.py — LeetCode adapter via GraphQL API.
Problem ID format: title slug  e.g. 'two-sum'  (from the URL: leetcode.com/problems/two-sum/)

FIX (was breaking every LeetCode check):
  checker.py always calls check_solved(handle, problem_id, since_ts, until_ts)
  with 4 positional args. This adapter's check_solved only accepted 3,
  so every single LeetCode check was crashing with:
    "check_solved() takes 4 positional arguments but 5 were given"
  caught silently by checker.py's try/except and shown as a generic
  "⚠️ API error" to the user. Fixed below by accepting `until_ts` and
  using it as an upper bound so a solve isn't credited to the wrong day.
"""

import aiohttp
from datetime import datetime, timezone
from platforms.base import PlatformAdapter, Submission

LC_GQL = "https://leetcode.com/graphql"

VERIFY_QUERY = """
query userProfile($username: String!) {
  matchedUser(username: $username) {
    username
    submitStats { acSubmissionNum { difficulty count } }
  }
}
"""

RECENT_AC_QUERY = """
query recentAcSubmissions($username: String!, $limit: Int!) {
  recentAcSubmissionList(username: $username, limit: $limit) {
    id title titleSlug timestamp
  }
}
"""

_HEADERS = {
    "Content-Type": "application/json",
    "Referer": "https://leetcode.com",
    "User-Agent": "Mozilla/5.0 (compatible; CPBot/1.0)",
}


async def _gql(query: str, variables: dict) -> dict:
    async with aiohttp.ClientSession(headers=_HEADERS) as s:
        async with s.post(
            LC_GQL,
            json={"query": query, "variables": variables},
            timeout=aiohttp.ClientTimeout(total=12),
        ) as r:
            return await r.json()


class LeetCodeAdapter(PlatformAdapter):
    KEY  = "lc"
    NAME = "LeetCode"

    def format_problem_id(self, raw: str) -> str:
        return raw.strip().lower()

    def problem_url(self, problem_id: str) -> str | None:
        return f"https://leetcode.com/problems/{problem_id}/"

    async def verify_handle(self, handle: str) -> tuple[bool, str]:
        try:
            data = await _gql(VERIFY_QUERY, {"username": handle})
        except Exception as e:
            return False, f"⚠️ Could not reach LeetCode: {e}"

        user = data.get("data", {}).get("matchedUser")
        if not user:
            return False, f"❌ Handle `{handle}` not found on LeetCode."

        solved = sum(
            s["count"]
            for s in user.get("submitStats", {}).get("acSubmissionNum", [])
            if s["difficulty"] != "All"
        )
        return True, f"✅ Found `{user['username']}` — {solved} problems solved."

    async def get_recent_submissions(self, handle: str, limit: int = 20) -> list[Submission]:
        try:
            data = await _gql(RECENT_AC_QUERY, {"username": handle, "limit": limit})
        except Exception as e:
            raise RuntimeError(f"LeetCode API error: {e}")

        items = data.get("data", {}).get("recentAcSubmissionList") or []
        return [
            Submission(
                problem_id = item["titleSlug"],
                title      = item["title"],
                verdict    = "AC",
                timestamp  = float(item["timestamp"]),
                url        = f"https://leetcode.com/problems/{item['titleSlug']}/",
            )
            for item in items
        ]

    async def check_solved(
        self, handle: str, problem_id: str, since_ts: float, until_ts: float = None
    ) -> tuple[bool, str]:
        """
        LeetCode's public API returns up to ~20 recent AC submissions via
        recentAcSubmissionList — no arbitrary time-range query exists.
        If the user has solved 20+ problems since the assignment this may
        miss an older one, but it's fine for the daily/weekly check window
        this bot uses. `until_ts` bounds it above so a same-slug solve from
        a *future* day isn't mistakenly credited to an earlier one.
        """
        try:
            subs = await self.get_recent_submissions(handle, limit=20)
        except RuntimeError as e:
            return False, f"⚠️ {e}"

        slug = problem_id.lower()
        for sub in subs:
            if sub.problem_id.lower() != slug:
                continue
            if sub.timestamp < since_ts:
                continue
            if until_ts is not None and sub.timestamp > until_ts:
                continue
            return True, "✅ Accepted"

        return False, "❌ No accepted submission found within the time window."