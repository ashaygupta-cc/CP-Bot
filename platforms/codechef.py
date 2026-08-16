"""
platforms/codechef.py — CodeChef adapter.

⚠️  CodeChef's official submission API requires OAuth 2.0.
    This adapter verifies handles via their public profile page
    and checks for accepted submissions using the unofficial JSON
    endpoint. If CodeChef changes their HTML/API this may break.

Problem ID format: problem CODE  e.g. 'CHEFEZ'

NOTE: added a browser-like User-Agent on every request below. CodeChef's
unofficial endpoints sometimes return empty/garbage bodies to requests
that don't look like they came from a browser — this mirrors the headers
already used successfully in the extension's codechef.js scraper.
"""

import aiohttp
from platforms.base import PlatformAdapter, Submission

CC_BASE = "https://www.codechef.com"

_HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent": "Mozilla/5.0 (compatible; CPBot/1.0)",
}


class CodeChefAdapter(PlatformAdapter):
    KEY  = "cc"
    NAME = "CodeChef"

    def format_problem_id(self, raw: str) -> str:
        return raw.strip().upper()

    def problem_url(self, problem_id: str) -> str | None:
        return f"https://www.codechef.com/problems/{problem_id}"

    async def verify_handle(self, handle: str) -> tuple[bool, str]:
        """Checks the public profile page for a 200 status."""
        url = f"{CC_BASE}/users/{handle}"
        try:
            async with aiohttp.ClientSession(headers=_HEADERS) as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status == 200:
                        return True, f"✅ Found CodeChef profile for `{handle}`."
                    return False, f"❌ Handle `{handle}` not found on CodeChef (HTTP {r.status})."
        except Exception as e:
            return False, f"⚠️ Could not reach CodeChef: {e}"

    async def get_recent_submissions(self, handle: str, limit: int = 20) -> list[Submission]:
        """
        Fetches recent submissions from CodeChef's unofficial JSON endpoint.
        NOTE: This endpoint is undocumented and may change without notice.
        For production use, implement OAuth 2.0 via https://api.codechef.com/
        """
        url = f"{CC_BASE}/recent/user?user_handle={handle}&page=0"
        try:
            async with aiohttp.ClientSession(headers=_HEADERS) as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    data = await r.json(content_type=None)
        except Exception as e:
            raise RuntimeError(f"CodeChef API error: {e}")

        results = []
        for sub in (data.get("content", {}).get("submissions", []) or [])[:limit]:
            results.append(Submission(
                problem_id = sub.get("problemCode", ""),
                title      = sub.get("problemName", sub.get("problemCode", "")),
                verdict    = "AC" if sub.get("result", "") == "AC" else sub.get("result", "?"),
                timestamp  = 0.0,    # CodeChef doesn't return a timestamp here
                language   = sub.get("language"),
                url        = f"{CC_BASE}/problems/{sub.get('problemCode','')}",
            ))
        return results

    async def check_solved(
        self, handle: str, problem_id: str, since_ts: float, until_ts: float = None
    ) -> tuple[bool, str]:
        """
        Checks recent submissions for an AC on the given problem.
        Limitation: no timestamps available from this endpoint,
        so 'within timeframe' is approximated by recency of the page.
        Upgrade to OAuth API for exact timestamps.
        """
        try:
            subs = await self.get_recent_submissions(handle, limit=20)
        except RuntimeError as e:
            return False, f"⚠️ {e}"

        pid = problem_id.upper()
        for sub in subs:
            if sub.problem_id.upper() == pid and sub.verdict == "AC":
                # No timestamp — we trust it's recent enough given the window
                return True, "✅ Accepted (note: exact submission time unavailable on CodeChef)"

        return False, "❌ No accepted submission found in recent activity."