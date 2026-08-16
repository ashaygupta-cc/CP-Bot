"""
platforms/atcoder.py — AtCoder adapter.

Submission checking uses AtCoder's own public submission filter page
(no auth required for public submissions):
  https://atcoder.jp/contests/{contest}/submissions
    ?f.User={handle}&f.Task={problem_id}&f.Status=AC

This replaces the kenkoooo approach which was unreliable due to
propagation lag and network-level blocking in some environments.

v6 NEW — session cookie support:
  AtCoder's submission filter page is public and doesn't *require* a
  login, but a logged-in session reliably avoids edge cases (rate
  limiting / soft blocks) that the original no-auth approach hit.

  An admin can store a long-lived AtCoder session cookie with
  `!setcookie <REVEL_SESSION value>` (see cogs in bot.py). That value
  is saved in the `bot_config` table (key = "atcoder_session") and
  read here on every request. If no cookie has been set yet, requests
  simply go out anonymously exactly like before — nothing breaks.
"""

import re
import aiohttp
import asyncio
import time
from datetime import datetime, timezone, timedelta
from platforms.base import PlatformAdapter, Submission
from database.connection import get_pool
from database import queries as q

KENKOOOO = "https://kenkoooo.com/atcoder"
CONFIG_KEY = "atcoder_session"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}


async def _get_session_cookie() -> str | None:
    """Read the stored REVEL_SESSION cookie value from bot_config, if any."""
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            return await q.get_config(conn, CONFIG_KEY)
    except Exception as e:
        print(f"[atcoder] could not read session cookie from DB: {e}", flush=True)
        return None


def _parse_ac_submission_page(html: str) -> list[dict]:
    """
    Parse AtCoder's submission filter page HTML.
    Returns list of dicts: [{"timestamp": float_epoch, "verdict": str}, ...]
    sorted newest-first.
    """
    results = []
    tbody = re.findall(r"<tbody.*?>(.*?)</tbody>", html, re.DOTALL)
    if not tbody:
        return results

    trs = re.findall(r"<tr[^>]*>(.*?)</tr>", tbody[0], re.DOTALL)
    for tr in trs:
        time_match = re.search(
            r"<time[^>]*>(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})([+-]\d{4})?</time>", tr
        )
        verdict_match = re.search(r"label-success[^>]*>\s*(\w+)\s*<", tr)
        if not verdict_match:
            verdict_match = re.search(r">\s*(AC)\s*<", tr)

        if not time_match:
            continue

        verdict = verdict_match.group(1).strip() if verdict_match else "?"
        ts_str     = time_match.group(1)
        offset_str = time_match.group(2)   # e.g. "+0900" — AtCoder always shows JST

        try:
            dt_naive = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            if offset_str:
                sign = 1 if offset_str[0] == "+" else -1
                oh, om = int(offset_str[1:3]), int(offset_str[3:5])
                tz = timezone(sign * timedelta(hours=oh, minutes=om))
            else:
                # AtCoder's submission page always renders times in JST even
                # when no explicit offset is present in the HTML, so default
                # to +09:00 rather than assuming UTC (which silently shifted
                # every timestamp 9 hours into the future).
                tz = timezone(timedelta(hours=9))
            dt = dt_naive.replace(tzinfo=tz)
            results.append({"timestamp": dt.timestamp(), "verdict": verdict})
        except ValueError:
            continue

    results.sort(key=lambda x: x["timestamp"], reverse=True)
    return results


class AtCoderAdapter(PlatformAdapter):
    KEY  = "atcoder"
    NAME = "AtCoder"

    def format_problem_id(self, raw: str) -> str:
        return raw.strip().lower()

    def problem_url(self, problem_id: str) -> str | None:
        parts = problem_id.split("_")
        if len(parts) >= 2:
            contest = "_".join(parts[:-1])
            return f"https://atcoder.jp/contests/{contest}/tasks/{problem_id}"
        return None

    async def verify_handle(self, handle: str) -> tuple[bool, str]:
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
        since = int(time.time()) - 90 * 24 * 3600
        url = f"{KENKOOOO}/atcoder-api/v3/user/submissions?user={handle}&from_second={since}"
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=12)) as r:
                data = await r.json()

        if not isinstance(data, list):
            raise RuntimeError("Unexpected AtCoder API response.")

        data.sort(key=lambda x: x.get("epoch_second", 0), reverse=True)
        results = []
        for sub in data[:limit]:
            contest = sub.get("contest_id", "")
            pid     = sub.get("problem_id", "")
            results.append(Submission(
                problem_id = pid,
                title      = pid,
                verdict    = sub.get("result", "?"),
                timestamp  = float(sub.get("epoch_second", 0)),
                language   = sub.get("language"),
                url        = f"https://atcoder.jp/contests/{contest}/submissions/{sub.get('id')}",
            ))
        return results

    async def check_solved(
        self, handle: str, problem_id: str, since_ts: float, until_ts: float = None
    ) -> tuple[bool, str]:
        pid = problem_id.strip().lower()

        parts = pid.split("_")
        if len(parts) < 2:
            return False, f"⚠️ Cannot derive contest from problem ID `{problem_id}`."
        contest = "_".join(parts[:-1])

        url = (
            f"https://atcoder.jp/contests/{contest}/submissions"
            f"?f.User={handle}&f.Task={pid}&f.Status=AC"
        )

        print(f"[atcoder] fetching: {url}", flush=True)
        subs = await self._fetch_ac_page(url)
        print(f"[atcoder] subs returned: {subs}", flush=True)
        print(f"[atcoder] window since={since_ts} until={until_ts}", flush=True)

        if subs is None:
            print("[atcoder] fetch returned None — AtCoder unreachable", flush=True)
            return False, "⚠️ Could not reach AtCoder. Try `!check` again shortly."

        if len(subs) == 0:
            print("[atcoder] empty list — retrying after 3s", flush=True)
            await asyncio.sleep(3)
            subs = await self._fetch_ac_page(url)
            print(f"[atcoder] retry subs: {subs}", flush=True)
            if subs is None:
                return False, "⚠️ Could not reach AtCoder. Try `!check` again shortly."

        for sub in subs:
            ts = sub["timestamp"]
            print(f"[atcoder] checking sub ts={ts} verdict={sub.get('verdict')} in_window={since_ts <= ts <= (until_ts or float('inf'))}", flush=True)
            if ts < since_ts:
                continue
            if until_ts is not None and ts > until_ts:
                continue
            if sub.get("verdict") == "AC":
                return True, "✅ Accepted"

        return False, "❌ No accepted submission found within the time window."

    async def _fetch_ac_page(self, url: str) -> list[dict] | None:
        cookie_val = await _get_session_cookie()
        headers = dict(_HEADERS)
        if cookie_val:
            headers["Cookie"] = f"REVEL_SESSION={cookie_val}"

        try:
            async with aiohttp.ClientSession(headers=headers) as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                    print(f"[atcoder] HTTP {r.status} for {url} (cookie={'yes' if cookie_val else 'no'})", flush=True)
                    if r.status != 200:
                        return None
                    html = await r.text()
                    print(f"[atcoder] HTML length: {len(html)}", flush=True)
            parsed = _parse_ac_submission_page(html)
            print(f"[atcoder] parsed {len(parsed)} submissions from HTML", flush=True)
            return parsed
        except Exception as e:
            print(f"[atcoder] exception in _fetch_ac_page: {e}", flush=True)
            return None