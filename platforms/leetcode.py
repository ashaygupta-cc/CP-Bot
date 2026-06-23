"""
platforms/leetcode.py — LeetCode adapter via GraphQL API.
Problem ID format: title slug  e.g. 'two-sum'  (from the URL: leetcode.com/problems/two-sum/)
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


async def _gql(query: str, variables: dict) -> dict:
    headers = {
        "Content-Type": "application/json",
        "Referer": "https://leetcode.com",
    }
    async with aiohttp.ClientSession(headers=headers) as s:
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

    async def check_solved(self, handle: str, problem_id: str, since_ts: float) -> tuple[bool, str]:
        """
        LeetCode's public API returns up to 15 recent AC submissions.
        If a user has solved many problems since the assignment this may miss it,
        but works well for typical weekly contest windows.
        """
        try:
            subs = await self.get_recent_submissions(handle, limit=15)
        except RuntimeError as e:
            return False, f"⚠️ {e}"

        slug = problem_id.lower()
        for sub in subs:
            if sub.problem_id.lower() == slug and sub.timestamp >= since_ts:
                return True, "✅ Accepted"

        return False, "❌ No accepted submission found within the time window."
