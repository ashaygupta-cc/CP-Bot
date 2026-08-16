"""
cogs/contests.py — Upcoming contest reminders. (v2 — no clist.by dependency)

Fetches contests directly from each platform's own public API:
  • Codeforces  →  codeforces.com/api/contest.list          (official JSON API)
  • LeetCode    →  leetcode.com/graphql                     (official GraphQL)
  • CodeChef    →  codechef.com/api/list/contests/future    (public JSON)
  • AtCoder     →  kenkoooo.com/atcoder/resources/contests.json (community JSON)

No third-party aggregator, no API key needed.

Posts reminders at 12h, 6h, 1h before each contest into #contest-reminder.
Pings @everyone (or configured role).

Admin commands:
  !contests        — list upcoming contests (next 7 days)
  !contestcheck    — manually trigger the reminder check right now
"""

import asyncio
import re
import aiohttp
import discord
from discord.ext import commands, tasks
from datetime import datetime, timezone, timedelta

import config
from database import queries as q
from database.connection import get_pool

IST = q.IST

ATCODER_COOKIE_KEY = "atcoder_session"


async def _get_atcoder_cookie() -> str | None:
    """
    Read the same stored REVEL_SESSION cookie value the checker cog uses
    (set via `!setcookie`). Reusing a logged-in session here for free,
    since `platforms/atcoder.py` already proved it makes AtCoder requests
    from this host noticeably more reliable.
    """
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            return await q.get_config(conn, ATCODER_COOKIE_KEY)
    except Exception as e:
        print(f"[contests/atcoder] could not read session cookie from DB: {e}", flush=True)
        return None


# ── Platform config ────────────────────────────────────────────────────────────
PLATFORMS = {
    "cf":      {"name": "Codeforces", "emoji": "🔵", "color": 0x1F8EF1},
    "lc":      {"name": "LeetCode",   "emoji": "🟡", "color": 0xFFA116},
    "cc":      {"name": "CodeChef",   "emoji": "🟤", "color": 0x6B3A2A},
    "atcoder": {"name": "AtCoder",    "emoji": "🔴", "color": 0xED4245},
}

# Reminder windows: (label, seconds_before_start, window_tolerance_seconds)
# Tolerance = how wide a band around the threshold to fire in (task runs every 30m)
REMINDER_WINDOWS = [
    ("12h", 12 * 3600, 30 * 60),
    ("6h",   6 * 3600, 30 * 60),
    ("1h",   1 * 3600, 30 * 60),
]

POLL_MINUTES   = 30
FETCH_DAYS     = 7     # fetch contests starting within next 7 days


# ── Duration formatter ─────────────────────────────────────────────────────────
def _fmt_dur(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    return f"{m}m"


# ── Ping string ────────────────────────────────────────────────────────────────
def _ping(guild: discord.Guild) -> str:
    cfg = getattr(config, "CONTEST_REMINDER_ROLE", "everyone").strip()
    if not cfg:
        return ""
    if cfg.lower() == "everyone":
        return "@everyone"
    role = discord.utils.get(guild.roles, name=cfg)
    return role.mention if role else ""


# ══════════════════════════════════════════════════════════════════════════════
#  Per-platform fetchers
# ══════════════════════════════════════════════════════════════════════════════

async def _fetch_cf(session: aiohttp.ClientSession) -> list[dict]:
    """Codeforces official API — returns all future rated/unrated contests."""
    url = "https://codeforces.com/api/contest.list?gym=false"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=12)) as r:
            if r.status != 200:
                return []
            data = await r.json()
        if data.get("status") != "OK":
            return []
        now_ts = datetime.now(timezone.utc).timestamp()
        results = []
        for c in data.get("result", []):
            if c.get("phase") != "BEFORE":
                continue
            start_ts = c.get("startTimeSeconds")
            if not start_ts:
                continue
            if start_ts - now_ts > FETCH_DAYS * 86400:
                continue
            dur = c.get("durationSeconds", 0)
            results.append({
                "key":      "cf",
                "id":       str(c.get("id", "")),
                "name":     c.get("name", "Codeforces Contest"),
                "start_ts": float(start_ts),
                "duration": dur,
                "url":      f"https://codeforces.com/contest/{c.get('id')}",
            })
        return results
    except Exception as e:
        print(f"[contests/cf] fetch error: {e}", flush=True)
        return []


async def _fetch_lc(session: aiohttp.ClientSession) -> list[dict]:
    """LeetCode GraphQL — upcoming contests."""
    query = """
    {
      allContests {
        title
        titleSlug
        startTime
        duration
      }
    }
    """
    url = "https://leetcode.com/graphql"
    headers = {
        "Content-Type": "application/json",
        "Referer": "https://leetcode.com",
        "User-Agent": "Mozilla/5.0 (compatible; CPBot/1.0)",
    }
    try:
        async with session.post(
            url,
            json={"query": query},
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=12),
        ) as r:
            if r.status != 200:
                return []
            data = await r.json()

        now_ts  = datetime.now(timezone.utc).timestamp()
        results = []
        for c in data.get("data", {}).get("allContests", []):
            start_ts = c.get("startTime", 0)
            if not start_ts or start_ts <= now_ts:
                continue
            if start_ts - now_ts > FETCH_DAYS * 86400:
                continue
            slug = c.get("titleSlug", "")
            results.append({
                "key":      "lc",
                "id":       slug,
                "name":     c.get("title", "LeetCode Contest"),
                "start_ts": float(start_ts),
                "duration": c.get("duration", 5400),
                "url":      f"https://leetcode.com/contest/{slug}/",
            })
        return results
    except Exception as e:
        print(f"[contests/lc] fetch error: {e}", flush=True)
        return []


def _parse_iso(s: str) -> float | None:
    """Parse an ISO-8601 timestamp (with or without trailing Z) to a UTC epoch float."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.strip().replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    except Exception:
        return None


async def _fetch_cc(session: aiohttp.ClientSession) -> list[dict]:
    """
    CodeChef contests.

    The original attempt here used only a User-Agent header on CodeChef's
    `api/list/contests/future` endpoint and got nothing back. The submission
    checker (`platforms/codechef.py`) hits CodeChef successfully for the same
    kind of request by also sending `X-Requested-With: XMLHttpRequest` — that
    header is what CodeChef's WAF actually checks for "is this a legit AJAX
    call from the site itself", not the User-Agent. So we now try the official
    endpoint first with that same header set (matching the working checker),
    and only fall back to third-party aggregators (competeapi.vercel.app,
    kontests.net) if CodeChef ever blocks it again.
    """
    now_ts  = datetime.now(timezone.utc).timestamp()
    results: list[dict] = []

    # ── Primary: CodeChef's own API, with the checker's working headers ─────
    try:
        url = "https://www.codechef.com/api/list/contests/future?sort_by=START&sorting_order=asc&offset=0&mode=all"
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; CPBot/1.0)",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json",
        }
        async with session.get(
            url,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=12),
        ) as r:
            if r.status == 200:
                data = await r.json(content_type=None)
                if isinstance(data, dict):
                    # CodeChef's API returns the list under "contests" (new shape)
                    # or "future_contests" (old shape) — try both.
                    raw_list = data.get("contests") or data.get("future_contests") or []
                    print(f"[contests/cc] official API OK — keys={list(data.keys())} contests={len(raw_list)}", flush=True)
                else:
                    raw_list = []
                    print(f"[contests/cc] official API OK but unexpected shape: {type(data).__name__}", flush=True)
                for c in raw_list:
                    if not isinstance(c, dict):
                        continue
                    start_str = c.get("contest_start_date_iso") or c.get("contest_start_date", "")
                    try:
                        if "T" in start_str:
                            start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                            start_ts = start_dt.astimezone(timezone.utc).timestamp()
                        else:
                            naive = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S")
                            start_ts = naive.replace(tzinfo=IST).astimezone(timezone.utc).timestamp()
                    except Exception:
                        continue

                    if start_ts <= now_ts or start_ts - now_ts > FETCH_DAYS * 86400:
                        continue

                    end_str = c.get("contest_end_date_iso") or c.get("contest_end_date", "")
                    duration = 0
                    try:
                        if "T" in end_str:
                            end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                            end_ts = end_dt.astimezone(timezone.utc).timestamp()
                        else:
                            end_naive = datetime.strptime(end_str, "%Y-%m-%d %H:%M:%S")
                            end_ts = end_naive.replace(tzinfo=IST).astimezone(timezone.utc).timestamp()
                        duration = int(end_ts - start_ts)
                    except Exception:
                        pass

                    code = c.get("contest_code", "")
                    results.append({
                        "key":      "cc",
                        "id":       code,
                        "name":     c.get("contest_name", "CodeChef Contest"),
                        "start_ts": float(start_ts),
                        "duration": duration,
                        "url":      f"https://www.codechef.com/{code}",
                    })
            else:
                print(f"[contests/cc] official API returned HTTP {r.status}", flush=True)
    except Exception as e:
        print(f"[contests/cc] official-api fetch error: {type(e).__name__}: {e}", flush=True)

    print(f"[contests/cc] official API parsed → {len(results)} upcoming contest(s)", flush=True)
    if results:
        return results

    # ── Fallback 1: competeapi.vercel.app ───────────────────────────────────
    try:
        url = "https://competeapi.vercel.app/contests/codechef/"
        headers = {"User-Agent": "Mozilla/5.0 (compatible; CPBot/1.0)", "Accept": "application/json"}
        async with session.get(
            url, headers=headers, timeout=aiohttp.ClientTimeout(total=12)
        ) as r:
            if r.status == 200:
                data = await r.json(content_type=None)
                if not isinstance(data, dict):
                    print(f"[contests/cc] competeapi unexpected shape: {type(data).__name__} → {str(data)[:200]}", flush=True)
                else:
                    # competeapi returns the same shape as CodeChef's own API
                    comp_list = data.get("future_contests") or data.get("contests") or []
                    print(f"[contests/cc] competeapi OK — future_contests={len(comp_list)}", flush=True)
                    for c in comp_list:
                        if not isinstance(c, dict):
                            continue
                        start_str = c.get("contest_start_date_iso") or c.get("contest_start_date", "")
                        try:
                            if "T" in start_str:
                                start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                                start_ts = start_dt.astimezone(timezone.utc).timestamp()
                            else:
                                naive = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S")
                                start_ts = naive.replace(tzinfo=IST).astimezone(timezone.utc).timestamp()
                        except Exception:
                            continue
                        if start_ts <= now_ts or start_ts - now_ts > FETCH_DAYS * 86400:
                            continue
                        end_str = c.get("contest_end_date_iso") or c.get("contest_end_date", "")
                        duration = 0
                        try:
                            if "T" in end_str:
                                end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                                end_ts = end_dt.astimezone(timezone.utc).timestamp()
                            else:
                                end_naive = datetime.strptime(end_str, "%Y-%m-%d %H:%M:%S")
                                end_ts = end_naive.replace(tzinfo=IST).astimezone(timezone.utc).timestamp()
                            duration = int(end_ts - start_ts)
                        except Exception:
                            pass
                        code = c.get("contest_code", "")
                        results.append({
                            "key":      "cc",
                            "id":       code,
                            "name":     c.get("contest_name", "CodeChef Contest"),
                            "start_ts": float(start_ts),
                            "duration": duration,
                            "url":      f"https://www.codechef.com/{code}",
                        })
            else:
                print(f"[contests/cc] competeapi returned HTTP {r.status}", flush=True)
    except Exception as e:
        print(f"[contests/cc] competeapi fetch error: {type(e).__name__}: {e}", flush=True)

    if results:
        return results

    # ── Fallback 2: kontests.net aggregator ─────────────────────────────────
    try:
        url = "https://kontests.net/api/v1/code_chef"
        headers = {"User-Agent": "Mozilla/5.0 (compatible; CPBot/1.0)", "Accept": "application/json"}
        async with session.get(
            url, headers=headers, timeout=aiohttp.ClientTimeout(total=12)
        ) as r:
            if r.status == 200:
                data = await r.json(content_type=None)
                for c in data:
                    status = (c.get("status") or "").upper()
                    if status and status != "BEFORE":
                        continue

                    start_ts = _parse_iso(c.get("start_time", ""))
                    if not start_ts or start_ts <= now_ts:
                        continue
                    if start_ts - now_ts > FETCH_DAYS * 86400:
                        continue

                    end_ts = _parse_iso(c.get("end_time", ""))
                    duration = 0
                    raw_dur = c.get("duration")
                    if raw_dur:
                        try:
                            duration = int(raw_dur)
                        except (TypeError, ValueError):
                            duration = 0
                    if not duration and end_ts:
                        duration = int(end_ts - start_ts)

                    name = c.get("name", "CodeChef Contest")
                    link = c.get("url", "")
                    code = link.rstrip("/").rsplit("/", 1)[-1] if link else name

                    results.append({
                        "key":      "cc",
                        "id":       code,
                        "name":     name,
                        "start_ts": float(start_ts),
                        "duration": duration,
                        "url":      link or "https://www.codechef.com/contests",
                    })
            else:
                print(f"[contests/cc] kontests.net returned HTTP {r.status}", flush=True)
    except Exception as e:
        print(f"[contests/cc] kontests.net fetch error: {type(e).__name__}: {e}", flush=True)

    return results


async def _fetch_atcoder(session: aiohttp.ClientSession) -> list[dict]:
    """
    AtCoder upcoming contests.

    kenkoooo.com's `contests.json` has stopped reflecting newly-announced
    AtCoder contests (confirmed dead/stale since mid-2026), and the kontests.net
    aggregator times out from this host. Instead we scrape AtCoder's own
    contest list page directly — the same atcoder.jp domain the submission
    checker (`platforms/atcoder.py`) already reaches successfully from this
    host — and, like that checker, attach the stored REVEL_SESSION cookie
    (set via `!setcookie`) if one is available, since a logged-in session is
    what makes those requests reliable. kontests.net and kenkoooo are kept
    as fallbacks.
    """
    now_ts  = datetime.now(timezone.utc).timestamp()
    results: list[dict] = []

    # ── Primary: scrape atcoder.jp's own contest list page ──────────────────
    try:
        url = "https://atcoder.jp/contests/?lang=en"
        cookie_val = await _get_atcoder_cookie()
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if cookie_val:
            headers["Cookie"] = f"REVEL_SESSION={cookie_val}"

        async with session.get(
            url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
        ) as r:
            print(f"[contests/atcoder] HTTP {r.status} for {url} (cookie={'yes' if cookie_val else 'no'})", flush=True)
            if r.status != 200:
                print(f"[contests/atcoder] atcoder.jp returned HTTP {r.status}", flush=True)
            else:
                html = await r.text()
                marker = 'id="contest-table-upcoming"'
                idx = html.find(marker)
                if idx == -1:
                    print("[contests/atcoder] upcoming-table marker not found in page", flush=True)
                else:
                    chunk = html[idx:]
                    next_idx = chunk.find('id="contest-table-', len(marker))
                    if next_idx != -1:
                        chunk = chunk[:next_idx]

                    all_rows = re.findall(r"<tr[^>]*>(.*?)</tr>", chunk, flags=re.S)
                    print(f"[contests/atcoder] found {len(all_rows)} <tr> rows in upcoming chunk", flush=True)

                    for row in all_rows:
                        date_m = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[+-]\d{4})", row)
                        link_m = re.search(r'href="/contests/([a-zA-Z0-9_\-]+)"[^>]*>([^<]+)</a>', row)
                        if not (date_m and link_m):
                            continue
                        try:
                            start_dt = datetime.strptime(date_m.group(1), "%Y-%m-%d %H:%M:%S%z")
                            start_ts = start_dt.astimezone(timezone.utc).timestamp()
                        except Exception:
                            continue
                        if start_ts <= now_ts or start_ts - now_ts > FETCH_DAYS * 86400:
                            continue

                        cid  = link_m.group(1)
                        name = link_m.group(2).strip()
                        duration = 0
                        dur_m = re.search(r">(\d{1,3}):(\d{2})<", row[link_m.end():])
                        if dur_m:
                            duration = int(dur_m.group(1)) * 3600 + int(dur_m.group(2)) * 60

                        results.append({
                            "key":      "atcoder",
                            "id":       cid,
                            "name":     name,
                            "start_ts": float(start_ts),
                            "duration": duration,
                            "url":      f"https://atcoder.jp/contests/{cid}",
                        })
    except Exception as e:
        print(f"[contests/atcoder] atcoder.jp scrape error: {type(e).__name__}: {e}", flush=True)

    print(f"[contests/atcoder] atcoder.jp scrape parsed → {len(results)} upcoming contest(s)", flush=True)
    if results:
        return results

    # ── Fallback 1: kontests.net aggregator ─────────────────────────────────
    try:
        url = "https://kontests.net/api/v1/at_coder"
        headers = {"User-Agent": "Mozilla/5.0 (compatible; CPBot/1.0)", "Accept": "application/json"}
        async with session.get(
            url, headers=headers, timeout=aiohttp.ClientTimeout(total=12)
        ) as r:
            if r.status == 200:
                data = await r.json(content_type=None)
                for c in data:
                    status = (c.get("status") or "").upper()
                    if status and status != "BEFORE":
                        continue

                    start_ts = _parse_iso(c.get("start_time", ""))
                    if not start_ts or start_ts <= now_ts:
                        continue
                    if start_ts - now_ts > FETCH_DAYS * 86400:
                        continue

                    end_ts = _parse_iso(c.get("end_time", ""))
                    duration = 0
                    raw_dur = c.get("duration")
                    if raw_dur:
                        try:
                            duration = int(raw_dur)
                        except (TypeError, ValueError):
                            duration = 0
                    if not duration and end_ts:
                        duration = int(end_ts - start_ts)

                    name = c.get("name", "AtCoder Contest")
                    link = c.get("url", "")
                    cid  = link.rstrip("/").rsplit("/", 1)[-1] if link else name

                    results.append({
                        "key":      "atcoder",
                        "id":       cid,
                        "name":     name,
                        "start_ts": float(start_ts),
                        "duration": duration,
                        "url":      link or "https://atcoder.jp/contests/",
                    })
            else:
                print(f"[contests/atcoder] kontests.net returned HTTP {r.status}", flush=True)
    except Exception as e:
        print(f"[contests/atcoder] kontests.net fetch error: {type(e).__name__}: {e}", flush=True)

    if results:
        return results

    # ── Fallback 2: kenkoooo community JSON (may be stale) ──────────────────
    try:
        url = "https://kenkoooo.com/atcoder/resources/contests.json"
        headers = {"User-Agent": "Mozilla/5.0 (compatible; CPBot/1.0)"}
        async with session.get(
            url,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=12),
        ) as r:
            if r.status != 200:
                return []
            data = await r.json(content_type=None)

        for c in data:
            start_ts = c.get("start_epoch_second", 0)
            if not start_ts or start_ts <= now_ts:
                continue
            if start_ts - now_ts > FETCH_DAYS * 86400:
                continue
            duration = c.get("duration_second", 0)
            cid      = c.get("id", "")
            results.append({
                "key":      "atcoder",
                "id":       cid,
                "name":     c.get("title", "AtCoder Contest"),
                "start_ts": float(start_ts),
                "duration": duration,
                "url":      f"https://atcoder.jp/contests/{cid}",
            })
        return results
    except Exception as e:
        print(f"[contests/atcoder] kenkoooo fetch error: {type(e).__name__}: {e}", flush=True)
        return []


async def fetch_all_contests() -> list[dict]:
    """Fetch upcoming contests from all 4 platforms concurrently."""
    async with aiohttp.ClientSession() as session:
        cf, lc, cc, ac = await asyncio.gather(
            _fetch_cf(session),
            _fetch_lc(session),
            _fetch_cc(session),
            _fetch_atcoder(session),
        )
    contests = cf + lc + cc + ac
    contests.sort(key=lambda x: x["start_ts"])
    return contests


# ══════════════════════════════════════════════════════════════════════════════
#  Embed builder
# ══════════════════════════════════════════════════════════════════════════════

def _build_embed(contest: dict, label: str, guild: discord.Guild) -> discord.Embed:
    plat   = PLATFORMS[contest["key"]]
    name   = contest["name"]
    url    = contest["url"]
    ts     = int(contest["start_ts"])
    dur    = contest["duration"]

    label_info = {
        "12h": ("🕛", "Starting in **12 hours**",  plat["color"]),
        "6h":  ("🕕", "Starting in **6 hours**",   plat["color"]),
        "1h":  ("⏰", "Starting in **1 hour** — Get ready!", 0xED4245),
    }
    l_emoji, l_text, color = label_info.get(label, ("🔔", "Starting soon", plat["color"]))

    embed = discord.Embed(
        title=f"{l_emoji}  {plat['emoji']}  {name}",
        url=url,
        color=color,
        description=l_text,
    )
    embed.add_field(
        name="🕐  Starts",
        value=f"<t:{ts}:F>\n<t:{ts}:R>",
        inline=True,
    )
    if dur:
        embed.add_field(
            name="⏱️  Duration",
            value=_fmt_dur(dur),
            inline=True,
        )
    embed.add_field(
        name="🌐  Platform",
        value=f"{plat['emoji']}  **{plat['name']}**",
        inline=True,
    )
    embed.add_field(
        name="🔗  Link",
        value=f"[Open Contest]({url})",
        inline=False,
    )
    embed.set_footer(text=f"CP Bot  ·  Contest Reminder  ·  {plat['name']}")
    return embed


# ══════════════════════════════════════════════════════════════════════════════
#  Cog
# ══════════════════════════════════════════════════════════════════════════════

class Contests(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot   = bot
        self._sent: dict[str, bool] = {}   # tracks posted reminders this session
        self._reminder_loop.start()

    def cog_unload(self):
        self._reminder_loop.cancel()

    # ── background task ───────────────────────────────────────────────────────

    @tasks.loop(minutes=POLL_MINUTES)
    async def _reminder_loop(self):
        try:
            await self._process()
        except Exception as e:
            print(f"[contests] reminder loop error: {e}", flush=True)

    @_reminder_loop.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()
        print(f"[contests] reminder task started (every {POLL_MINUTES}m).", flush=True)

    async def _process(self):
        contests = await fetch_all_contests()
        if not contests:
            return

        now_ts = datetime.now(timezone.utc).timestamp()

        for guild in self.bot.guilds:
            ch = discord.utils.get(
                guild.text_channels,
                name=config.CONTEST_REMINDER_CHANNEL,
            )
            if not ch:
                continue

            for c in contests:
                secs_until = int(c["start_ts"] - now_ts)
                if secs_until < 0:
                    continue

                for label, threshold, tolerance in REMINDER_WINDOWS:
                    if not (threshold - tolerance <= secs_until <= threshold + tolerance):
                        continue

                    key = f"{c['key']}_{c['id']}_{label}"
                    if self._sent.get(key):
                        continue

                    embed   = _build_embed(c, label, guild)
                    ping    = _ping(guild)
                    try:
                        if ping:
                            await ch.send(content=ping, embed=embed)
                        else:
                            await ch.send(embed=embed)
                        self._sent[key] = True
                        print(
                            f"[contests] ✅ {label} reminder → '{c['name']}' in '{guild.name}'",
                            flush=True,
                        )
                    except discord.Forbidden:
                        print(f"[contests] ❌ no permission in #{ch.name}", flush=True)
                    except Exception as e:
                        print(f"[contests] error posting: {e}", flush=True)

                    await asyncio.sleep(0.5)

    # ── !contests ─────────────────────────────────────────────────────────────

    @commands.command(name="contests")
    async def contests_cmd(self, ctx):
        """Show upcoming contests in the next 7 days."""
        msg = await ctx.send("🔍  Fetching upcoming contests from all platforms…")

        try:
            contests = await fetch_all_contests()
        except Exception as e:
            await msg.edit(content=f"⚠️  Error fetching contests: {e}")
            return

        if not contests:
            await msg.edit(content="📭  No upcoming contests found in the next 7 days.")
            return

        embed = discord.Embed(
            title="📅  Upcoming Contests  —  Next 7 Days",
            color=0x5865F2,
        )

        # Single chronological list (contests already sorted by start_ts)
        lines = []
        for c in contests[:10]:
            plat = PLATFORMS[c["key"]]
            ts   = int(c["start_ts"])
            dur  = f"  ·  {_fmt_dur(c['duration'])}" if c["duration"] else ""
            lines.append(
                f"{plat['emoji']}  **[{c['name']}]({c['url']})**\n"
                f"> <t:{ts}:F>{dur}"
            )

        embed.description = "\n\n".join(lines)
        embed.set_footer(text="Reminders auto-posted at 12h · 6h · 1h before each contest.")
        await msg.edit(content=None, embed=embed)

    # ── !contestcheck ─────────────────────────────────────────────────────────

    @commands.command(name="contestcheck")
    async def contest_check_cmd(self, ctx):
        """(Admin) Manually trigger contest reminder check right now."""
        if not (
            ctx.author.guild_permissions.administrator
            or any(r.name == config.ADMIN_ROLE for r in ctx.author.roles)
        ):
            await ctx.send(f"❌  You need the **{config.ADMIN_ROLE}** role.")
            return

        msg = await ctx.send("🔄  Running contest reminder check…")
        await self._process()
        await msg.edit(content="✅  Contest reminder check complete. Check logs for details.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Contests(bot))