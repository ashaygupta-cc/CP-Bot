"""
cogs/checker.py  — v3
!check / !checkall
Key fix: solve timestamp is checked against BOTH the day start AND day end (midnight IST).
If someone runs !check after midnight for yesterday's problem → no points, day is locked.

Background tasks:
  auto_check      — runs every 6 h, awards points for today's open window
  midnight_reset  — fires at 00:00 IST every night:
                    1. Resets the daily leaderboard (solo, weekly/monthly untouched)
                    2. Logs the reset silently (no message sent)
"""

import discord
from discord.ext import commands, tasks
from datetime import datetime, timezone, timedelta, time
import asyncio
from database.connection import get_pool
from database import queries as q
import platforms as P
from config import COLOR_SUCCESS, COLOR_INFO, COLOR_WARN, ADMIN_ROLE

IST = q.IST

# ── Per-user !check rate limiting (in-memory, resets on bot restart) ──────────
# Why: hitting CF/AtCoder/LC/CC endpoints too often gets the bot IP rate-limited.
# Rule: max 3 !check runs per user per day, and a 1h cooldown after EVERY run
# (so even within the 3/day budget, runs are spaced out).
MAX_CHECKS_PER_DAY = 3
COOLDOWN_SECONDS   = 60 * 60          # 1 hour
API_HIT_DELAY      = 0.5              # seconds between platform API hits


def _day_window(target_date) -> tuple[datetime, datetime]:
    return q._day_window_utc(target_date)


def _seconds_until_midnight_ist() -> float:
    """Seconds from now until the next 00:00:00 IST."""
    now_ist      = datetime.now(IST)
    tomorrow_ist = (now_ist + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return (tomorrow_ist - now_ist).total_seconds()


class Checker(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.auto_check.start()
        # midnight reset is started after bot is ready (see below)
        self._midnight_task = None
        # { user_id: {"date": date, "count": int, "cooldown_until": datetime|None} }
        self._check_state: dict[int, dict] = {}

    def _check_rate_limit(self, user_id: int) -> tuple[bool, str]:
        """
        Returns (allowed, reason_if_blocked).
        Max MAX_CHECKS_PER_DAY runs/day per user + COOLDOWN_SECONDS after each run.
        Resets automatically at IST day-rollover (compares stored date vs today_ist()).
        """
        now   = datetime.now(timezone.utc)
        today = q.today_ist()
        state = self._check_state.get(user_id)

        if state is None or state["date"] != today:
            state = {"date": today, "count": 0, "cooldown_until": None}
            self._check_state[user_id] = state

        if state["cooldown_until"] and now < state["cooldown_until"]:
            remaining = state["cooldown_until"] - now
            mins = int(remaining.total_seconds() // 60) + 1
            return False, (
                f"⏳  Please wait **{mins} min** before running `!check` again "
                f"(1h cooldown between checks — keeps us from getting rate-limited)."
            )

        if state["count"] >= MAX_CHECKS_PER_DAY:
            return False, (
                f"🚫  You've used all **{MAX_CHECKS_PER_DAY} `!check` runs** for today. "
                f"Resets at midnight IST."
            )

        return True, ""

    def _record_check_use(self, user_id: int):
        now   = datetime.now(timezone.utc)
        today = q.today_ist()
        state = self._check_state.setdefault(
            user_id, {"date": today, "count": 0, "cooldown_until": None}
        )
        if state["date"] != today:
            state.update({"date": today, "count": 0})
        state["count"] += 1
        state["cooldown_until"] = now + timedelta(seconds=COOLDOWN_SECONDS)

    def cog_unload(self):
        self.auto_check.cancel()
        if self._midnight_task:
            self._midnight_task.cancel()

    # ── !check ───────────────────────────────────────────────────────────────

    @commands.command(name="check")
    async def check(self, ctx, member: discord.Member = None):
        """
        Check solve status for TODAY's problems.
        Points are only awarded within today's IST window (00:00 – 23:59 IST).
        !check
        !check @friend
        """
        target = member or ctx.author
        pool   = get_pool()
        today  = q.today_ist()

        # ── Rate limit: protects platform endpoints (CF/AtCoder/LC/CC) from bans ──
        allowed, reason = self._check_rate_limit(ctx.author.id)
        if not allowed:
            await ctx.send(reason)
            return

        async with pool.acquire() as conn:
            week = await q.get_active_week(conn, str(ctx.guild.id))
            if not week:
                await ctx.send("❌  No active week. Admin: `!setweek`")
                return
            probs = await q.get_problems_for_day(conn, str(ctx.guild.id), today)

        if not probs:
            async with pool.acquire() as conn:
                all_probs = await q.get_problems_for_week(conn, str(ctx.guild.id), week["id"])
            if all_probs:
                await ctx.send(
                    f"📭  No problems assigned for today (`{today}`).\n"
                    f"Use `!problems` to see the full week schedule."
                )
            else:
                await ctx.send("📭  No problems assigned this week yet.")
            return

        msg = await ctx.send(f"🔍  Checking **{target.display_name}**'s submissions for today…")
        self._record_check_use(ctx.author.id)
        try:
            results, total_earned = await self._check_member(target, probs, str(ctx.guild.id))
        except Exception as e:
            print(f"[check] _check_member crashed for {target.id}: {e}")
            await msg.edit(content=f"⚠️  An error occurred while checking: `{e}`")
            return

        # Ensure no None slots leak into the embed (safety net)
        results = [
            r if r is not None else "⚠️  Unknown error for this problem."
            for r in results
        ]

        color = COLOR_SUCCESS if total_earned > 0 else COLOR_INFO
        embed = discord.Embed(
            title=f"🔍  Solve Check  —  {target.display_name}",
            description="\n".join(results),
            color=color,
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="📅  Date",          value=f"`{today}`",                                   inline=True)
        embed.add_field(name="🏅  Points Earned", value=f"**+{total_earned} pts**" if total_earned > 0
                                                         else "No new points",                           inline=True)
        remaining = MAX_CHECKS_PER_DAY - self._check_state[ctx.author.id]["count"]
        embed.set_footer(
            text=f"Week: {week['label']}  ·  Points valid 00:00–23:59 IST today only  ·  "
                 f"{remaining}/{MAX_CHECKS_PER_DAY} checks left today"
        )
        await msg.edit(content=None, embed=embed)

    # ── !checkall ────────────────────────────────────────────────────────────

    @commands.command(name="checkall")
    @commands.has_permissions(administrator=True)
    async def check_all(self, ctx):
        """(Admin) Bulk-check ALL registered members for today's problems."""
        pool  = get_pool()
        today = q.today_ist()

        async with pool.acquire() as conn:
            week  = await q.get_active_week(conn, str(ctx.guild.id))
            if not week:
                await ctx.send("❌  No active week.")
                return
            probs = await q.get_problems_for_day(conn, str(ctx.guild.id), today)
            users = await conn.fetch("SELECT DISTINCT discord_id FROM handles")

        if not probs:
            await ctx.send(f"📭  No problems for today (`{today}`).")
            return

        msg = await ctx.send(
            f"⏳  Checking **{len(users)}** member(s) across "
            f"**{len(probs)}** problem(s) for `{today}`…"
        )
        summary, total_new = [], 0

        for row in users:
            member = ctx.guild.get_member(int(row["discord_id"]))
            if not member:
                continue
            try:
                results, earned = await self._check_member(member, probs, str(ctx.guild.id))
            except Exception as e:
                print(f"[checkall] _check_member crashed for {member.id}: {e}")
                summary.append(f"**{member.display_name}**  ⚠️ error")
                await asyncio.sleep(API_HIT_DELAY)
                continue
            total_new += earned
            results = [r if r is not None else "⚠️ error" for r in results]
            solved_count = sum(1 for r in results if "Solved" in r or "Already" in r)
            badge = f"**+{earned} pts**" if earned else "—"
            summary.append(f"**{member.display_name}**  {badge}  `{solved_count}/{len(probs)}`")
            await asyncio.sleep(API_HIT_DELAY)

        embed = discord.Embed(
            title=f"📊  Bulk Check  —  {today}",
            description="\n".join(summary) or "No registered members found.",
            color=COLOR_INFO,
        )
        embed.set_footer(text=f"Total new points: {total_new}  ·  Week: {week['label']}")
        await msg.edit(content=None, embed=embed)

    # ── Core logic ────────────────────────────────────────────────────────────

    async def _check_member(
        self, member: discord.Member, probs: list, guild_id: str
    ) -> tuple[list[str], int]:
        """
        Award points ONLY if:
          1. The problem's assigned_date == today (IST)
          2. The solve timestamp falls within [day_start_utc, day_end_utc]
             i.e. midnight-to-midnight IST of that day.
        This means after 00:00 IST the previous day's problems are CLOSED.

        Speed/rate-limit strategy:
          - Non-network checks (day-locked, already-recorded, no adapter, no handle)
            are resolved instantly, in order, no delay.
          - Problems that actually need a platform API hit are grouped BY PLATFORM.
            Different platforms (CF/LC/CC/AtCoder) are hit CONCURRENTLY since they're
            independent services with separate rate limits.
          - Within the SAME platform, calls stay sequential with a 0.5s gap
            (API_HIT_DELAY) between them — this is the only place delay matters,
            since hitting one platform repeatedly back-to-back is what trips its
            rate limiter.
        """
        results      = [None] * len(probs)   # filled in original order
        total_earned = 0
        pool         = get_pool()
        now_utc      = datetime.now(timezone.utc)

        # platform -> list of (index, prob) that need a real API hit
        api_groups: dict[str, list[tuple[int, dict]]] = {}

        for idx, prob in enumerate(probs):
            prob_id       = prob["id"]
            pid           = prob["problem_id"]
            pts           = prob["points"]
            platform      = prob["platform"]
            assigned_date = prob["assigned_date"]
            adapter       = P.get(platform)

            # Hard day window — both sides enforced
            day_start_utc, day_end_utc = _day_window(assigned_date)

            # ── Guard: if current time is past the day end, lock it out ─────
            if now_utc > day_end_utc:
                results[idx] = (
                    f"🔒  `{platform.upper()} {pid}` — "
                    f"Day ended (`{assigned_date}`). No points awarded."
                )
                continue

            async with pool.acquire() as conn:
                already = await q.has_solved(conn, str(member.id), prob_id)

            if already:
                results[idx] = f"✅  `{platform.upper()} {pid}` — Already recorded (+{pts} pts)"
                continue

            if not adapter:
                results[idx] = f"⚠️  `{platform.upper()} {pid}` — Platform adapter unavailable."
                continue

            async with pool.acquire() as conn:
                handle = await q.get_handle(conn, str(member.id), platform)

            if not handle:
                results[idx] = (
                    f"⚠️  `{platform.upper()} {pid}` — "
                    f"No {adapter.NAME} handle. `!register {platform} <handle>`"
                )
                continue

            api_groups.setdefault(platform, []).append((idx, prob))

        if not api_groups:
            return results, total_earned

        async def _run_platform_group(platform: str, items: list[tuple[int, dict]]):
            adapter = P.get(platform)
            earned  = 0
            for i, (idx, prob) in enumerate(items):
                pid           = prob["problem_id"]
                pts           = prob["points"]
                prob_id       = prob["id"]
                assigned_date = prob["assigned_date"]
                day_start_utc, day_end_utc = _day_window(assigned_date)

                if i > 0:
                    # Only delay between repeated hits to the SAME platform.
                    await asyncio.sleep(API_HIT_DELAY)

                try:
                    async with pool.acquire() as conn:
                        handle = await q.get_handle(conn, str(member.id), platform)

                    if not handle:
                        results[idx] = (
                            f"⚠️  `{platform.upper()} {pid}` — "
                            f"No {adapter.NAME} handle. `!register {platform} <handle>`"
                        )
                        continue

                    solved, status = await adapter.check_solved(
                        handle, pid, day_start_utc.timestamp(), day_end_utc.timestamp()
                    )

                    if solved:
                        async with pool.acquire() as conn:
                            newly = await q.record_solve(
                                conn,
                                discord_id    = str(member.id),
                                problem_db_id = prob_id,
                                guild_id      = guild_id,
                                solved_at     = datetime.now(timezone.utc),
                                points        = pts,
                            )
                        if newly:
                            earned += pts
                            results[idx] = f"✅  `{platform.upper()} {pid}` — **Solved! +{pts} pts** 🎉"
                        else:
                            results[idx] = f"✅  `{platform.upper()} {pid}` — Already recorded."
                    else:
                        results[idx] = f"❌  `{platform.upper()} {pid}` — {status.replace('❌ ', '')}"

                except Exception as e:
                    print(f"[checker] Platform {platform} prob {pid} error: {e}")
                    results[idx] = f"⚠️  `{platform.upper()} {pid}` — API error: `{e}`"

            return earned

        earned_per_group = await asyncio.gather(
            *[_run_platform_group(platform, items) for platform, items in api_groups.items()]
        )
        total_earned += sum(earned_per_group)

        return results, total_earned

    # ── Background: auto-check every 6 h ─────────────────────────────────────

    @tasks.loop(hours=6)
    async def auto_check(self):
        """Silently check all members every 6 h within the active day window."""
        today = q.today_ist()
        for guild in self.bot.guilds:
            pool = get_pool()
            try:
                async with pool.acquire() as conn:
                    week  = await q.get_active_week(conn, str(guild.id))
                    if not week:
                        continue
                    probs = await q.get_problems_for_day(conn, str(guild.id), today)
                    users = await conn.fetch("SELECT DISTINCT discord_id FROM handles")
                for row in users:
                    member = guild.get_member(int(row["discord_id"]))
                    if member:
                        await self._check_member(member, probs, str(guild.id))
            except Exception:
                pass

    @auto_check.before_loop
    async def before_auto_check(self):
        await self.bot.wait_until_ready()

    # ── Background: midnight IST auto daily-reset ─────────────────────────────

    async def _midnight_reset_loop(self):
        """
        Waits until the next 00:00 IST, then:
          1. Resets the daily leaderboard (only today's solves — weekly/monthly untouched).
          2. Sleeps 24 h and repeats forever.

        This is the automatic counterpart to !resetdaily.
        After reset, !problems will automatically show the new day's problems
        because it always reads today_ist().
        """
        await self.bot.wait_until_ready()

        while not self.bot.is_closed():
            # Sleep until next midnight IST
            wait = _seconds_until_midnight_ist()
            print(f"[midnight_reset] Next daily reset in {wait/3600:.1f} h ({wait:.0f} s)")
            await asyncio.sleep(wait)

            # It is now 00:00 IST — yesterday's date
            from datetime import date as _date
            yesterday = (datetime.now(IST) - timedelta(seconds=1)).date()

            print(f"[midnight_reset] Auto-resetting daily leaderboard for {yesterday}")
            for guild in self.bot.guilds:
                pool = get_pool()
                try:
                    async with pool.acquire() as conn:
                        deleted = await q.reset_daily_solves(conn, str(guild.id), yesterday)
                    print(f"[midnight_reset] Guild {guild.id}: cleared {deleted} daily solves for {yesterday}")
                except Exception as e:
                    print(f"[midnight_reset] Guild {guild.id} error: {e}")

            # Sleep 23 h 59 min before checking again (avoid double-fire)
            await asyncio.sleep(23 * 3600 + 59 * 60)

    @commands.Cog.listener()
    async def on_ready(self):
        """Start the midnight reset loop once on bot ready."""
        if self._midnight_task is None or self._midnight_task.done():
            self._midnight_task = asyncio.create_task(self._midnight_reset_loop())
            print("✅  Midnight IST daily-reset task started.")


async def setup(bot):
    await bot.add_cog(Checker(bot))