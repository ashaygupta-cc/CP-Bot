"""
platforms/codechef.py — CodeChef adapter.

⚠️  CodeChef's official submission API requires OAuth 2.0.
    This adapter verifies handles via their public profile page
    and checks for accepted submissions using the unofficial
    `/recent/user` endpoint.

IMPORTANT (v2 fix):
  The `/recent/user` endpoint does NOT return a structured
  `{content: {submissions: [...]}}` JSON — `content` is actually a
  raw HTML *string* (a rendered table of recent submissions). The
  previous version of this file wrongly assumed `content` was an
  object with a `submissions` list, which crashed with:
      'str' object has no attribute 'get'
  as soon as a user actually had recent submissions to show.

  This matches exactly what the browser-extension scraper (codechef.js)
  already does correctly — it regex-parses <tr>/<td> rows out of that
  HTML string. This adapter now does the same thing.

Problem ID format: problem CODE  e.g. 'CHEFEZ'
"""

import re
import aiohttp
from platforms.base import PlatformAdapter, Submission

CC_BASE = "https://www.codechef.com"

_HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent": "Mozilla/5.0 (compatible; CPBot/1.0)",
}

_TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.IGNORECASE | re.DOTALL)
_HREF_RE = re.compile(r"href=['\"]([^'\"]+)['\"]", re.IGNORECASE)
_ACCEPTED_RE = re.compile(r"title=['\"]accepted['\"]|tick-icon", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(html: str) -> str:
    return _TAG_RE.sub("", html or "").strip()


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
        Fetches recent submissions from CodeChef's unofficial `/recent/user`
        endpoint and parses the HTML table inside `content` with regex
        (same approach as the working browser-extension scraper).
        NOTE: This endpoint is undocumented and may change without notice.
        For production use, implement OAuth 2.0 via https://api.codechef.com/
        """
        url = f"{CC_BASE}/recent/user?page=0&user_handle={handle}"
        try:
            async with aiohttp.ClientSession(headers=_HEADERS) as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    data = await r.json(content_type=None)
        except Exception as e:
            raise RuntimeError(f"CodeChef API error: {e}")

        if not isinstance(data, dict):
            raise RuntimeError("CodeChef returned an unexpected response shape.")

        content = data.get("content", "")
        if not isinstance(content, str):
            # Defensive: if CC ever DOES switch to structured JSON, don't crash —
            # just treat it as "nothing parseable" rather than raising.
            content = ""

        results: list[Submission] = []
        for tr_match in _TR_RE.finditer(content):
            row_html = tr_match.group(1)
            tds = [m.group(1) for m in _TD_RE.finditer(row_html)]
            if len(tds) < 5:
                continue

            result_html = tds[2]
            is_accepted = bool(_ACCEPTED_RE.search(result_html))
            if not is_accepted:
                continue

            # tds[1] = problem link
            href_match = _HREF_RE.search(tds[1])
            if not href_match:
                continue
            href = href_match.group(1)
            parts = [p for p in href.split("/") if p]
            problem_name = parts[-1].upper() if parts else ""
            if not problem_name:
                continue

            contest_id = "Practice"
            if len(parts) >= 3 and parts[-2] == "problems":
                contest_id = parts[0]

            # tds[3] = language
            lang_text = _strip_tags(tds[3]) or "C++17"

            # tds[4] = solution link (viewsolution/<id>)
            sol_match = re.search(r"href=['\"]\/viewsolution\/([^'\"]+)['\"]", tds[4], re.IGNORECASE)
            sub_id = sol_match.group(1) if sol_match else None

            results.append(Submission(
                problem_id = problem_name,
                title      = problem_name,
                verdict    = "AC",
                timestamp  = 0.0,    # CodeChef doesn't expose a timestamp here
                language   = lang_text,
                url        = (
                    f"{CC_BASE}/viewsolution/{sub_id}" if sub_id
                    else f"{CC_BASE}/problems/{problem_name}"
                ),
            ))

            if len(results) >= limit:
                break

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