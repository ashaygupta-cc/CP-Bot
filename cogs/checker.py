"""
cogs/checker.py  — v5
!check / !checkall  +  auto-checkall at 23:58 IST

Rate-limit strategy
───────────────────
  !check (per user):
    • Max 3 runs / day per user, 1 h cooldown between runs.
    • CF uses a single bulk fetch then local filters — never more than
      1 CF API call per !check, regardless of how many CF problems exist.
    • Other platforms: 1 s gap between consecutive hits.

  !checkall / auto-check (bulk):
    • Members are processed SEQUENTIALLY, never concurrently.
    • Gap between members: BULK_MEMBER_DELAY (2 s) — gives CF / LC / CC
      time to cool down between consecutive user lookups.
    • CF: still 1 bulk fetch per member → local filter.  Zero extra calls.
    • Other platforms: BULK_PROBLEM_DELAY (1.5 s) between per-problem hits.

  Auto-checkall at 23:58 IST:
    • Fires 2 min before midnight so points land before the day closes.
    • Uses the same bulk logic with generous delays — no rush.
    • Posts a summary embed to CHECKALL_CHANNEL_ID if set in config,
      otherwise logs to console only.

  Codeforces block/rate-limit handling (v5 fix):
    • Previously, if the single CF bulk fetch failed (503 / Cloudflare
      block), the code "gracefully degraded" by falling back to N
      separate per-problem CF API calls — which is exactly what makes a
      block worse, since CF was already rejecting us.
    • Now: if the CF bulk fetch fails for ANY reason, we mark all of that
      member's CF problems as "temporarily unavailable" and move on —
      zero extra CF requests are made. codeforces.py itself already does
      a couple of short backoff retries before giving up, mirroring the
      cooldown approach used in the browser-extension sync.

Background tasks
────────────────
  auto_check_6h      — every 6 h, awards points silently (unchanged)
  _night_check_loop  — fires at 23:58 IST, bulk-checks all members
  _midnight_reset_loop — fires at 00:00 IST, resets daily leaderboard
"""

import discord
from discord.ext import commands, tasks
from datetime import datetime, timezone, timedelta
import asyncio
from database.connection import get_pool
from database import queries as q
import platforms as P
from config import COLOR_SUCCESS, COLOR_INFO, COLOR_WARN, ADMIN_ROLE

# Optional: set to a channel ID in config.py to receive nightly summary
# e.g.  CHECKALL_CHANNEL_ID = 123456789
try:
    from config import CHECKALL_CHANNEL_ID
except ImportError:
    CHECKALL_CHANNEL_ID = None

IST = q.IST

# Same fixed display order used by !problems, so !check / !checkall always
# list problems in the identical sequence the user already sees there.
DIFF_ORDER = {"easy": 0, "medium": 1, "hard": 2, "expert": 3, "master": 4}


def _difficulty_sort_key(prob):
    return (DIFF_ORDER.get(prob["difficulty"], 99), prob["id"])


def _sorted_probs(probs: list) -> list:
    return sorted(probs, key=_difficulty_sort_key)


def _line(i: int, platform: str, pid: str, status: str) -> str:
    """One clean, numbered result line — no emoji, matches !problems formatting."""
    return f"`{i}.`  **{platform.upper()} {pid}**  —  {status}"


# ── Rate-limit constants ───────────────────────────────────────────────────────
MAX_CHECKS_PER_DAY  = 3
COOLDOWN_SECONDS    = 60 * 60   # 1 h between !check runs
API_HIT_DELAY       = 1.0       # seconds between per-problem API hits (!check)
BULK_MEMBER_DELAY   = 2.0       # seconds between members in bulk ops
BULK_PROBLEM_DELAY  = 1.5       # seconds between per-problem hits inside bulk


def _day_window(target_date) -> tuple[datetime, datetime]:
    return q._day_window_utc(target_date)


def _seconds_until_ist(hour: int, minute: int) -> float:
    """Seconds from now until the next HH:MM IST (today or tomorrow)."""
    now_ist    = datetime.now(IST)
    target_ist = now_ist.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target_ist <= now_ist:
        target_ist += timedelta(days=1)
    return (target_ist - now_ist).total_seconds()


class Checker(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.auto_check_6h.start()
        self._midnight_task   = None
        self._night_check_task = None
        # { user_id: {"date": date, "count": int, "cooldown_until": datetime|None} }
        self._check_state: dict[int, dict] = {}

    # ── Rate-limit helpers ────────────────────────────────────────────────────

    def _check_rate_limit(self, user_id: int) -> tuple[bool, str]:
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
                f"Please wait **{mins} min** before running `!check` again "
                f"(1 h cooldown — keeps us from getting rate-limited)."
            )

        if state["count"] >= MAX_CHECKS_PER_DAY:
            return False, (
                f"You've used all **{MAX_CHECKS_PER_DAY}** `!check` runs for today. "
                "Resets at midnight IST."
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
        self.auto_check_6h.cancel()
        if self._midnight_task:
            self._midnight_task.cancel()
        if self._night_check_task:
            self._night_check_task.cancel()

    # ── !check ────────────────────────────────────────────────────────────────

    @commands.command(name="check")
    async def check(self, ctx, member: discord.Member = None):
        """
        Check your (or another member's) solve status for today's problems.
        Points are only awarded within today's IST window (00:00 – 23:59 IST).
        !check
        !check @friend
        """
        target = member or ctx.author
        pool   = get_pool()
        today  = q.today_ist()

        allowed, reason = self._check_rate_limit(ctx.author.id)
        if not allowed:
            await ctx.send(reason)
            return

        async with pool.acquire() as conn:
            week = await q.get_active_week(conn, str(ctx.guild.id))
            if not week:
                await ctx.send("No active week. Admin: `!setweek`")
                return
            probs = _sorted_probs(await q.get_problems_for_day(conn, str(ctx.guild.id), today))

        if not probs:
            async with pool.acquire() as conn:
                all_probs = await q.get_problems_for_week(conn, str(ctx.guild.id), week["id"])
            if all_probs:
                await ctx.send(
                    f"No problems assigned for today (`{today}`).\n"
                    "Use `!problems` to see the full week schedule."
                )
            else:
                await ctx.send("No problems assigned this week yet.")
            return

        msg = await ctx.send(f"Checking **{target.display_name}**'s submissions for today…")
        self._record_check_use(ctx.author.id)

        try:
            results, total_earned = await self._check_member(
                target, probs, str(ctx.guild.id), delay=API_HIT_DELAY
            )
        except Exception as e:
            print(f"[check] _check_member crashed for {target.id}: {e}")
            await msg.edit(content=f"An error occurred while checking: `{e}`")
            return

        results = [r if r is not None else "Unknown error." for r in results]

        color = COLOR_SUCCESS if total_earned > 0 else COLOR_INFO
        embed = discord.Embed(
            title=f"Solve Check  ·  {target.display_name}",
            description="\n".join(results),
            color=color,
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="Date",          value=f"`{today}`",                                    inline=True)
        embed.add_field(name="Points Earned", value=f"**+{total_earned} pts**" if total_earned > 0
                                                       else "No new points",                            inline=True)
        remaining = MAX_CHECKS_PER_DAY - self._check_state[ctx.author.id]["count"]
        embed.set_footer(
            text=f"Week: {week['label']}  ·  Points valid 00:00–23:59 IST only  ·  "
                 f"{remaining}/{MAX_CHECKS_PER_DAY} checks left today"
        )
        await msg.edit(content=None, embed=embed)

    # ── !checkall ─────────────────────────────────────────────────────────────

    @commands.command(name="checkall")
    @commands.has_permissions(administrator=True)
    async def check_all(self, ctx):
        """(Admin) Bulk-check ALL registered members for today's problems."""
        pool  = get_pool()
        today = q.today_ist()

        async with pool.acquire() as conn:
            week  = await q.get_active_week(conn, str(ctx.guild.id))
            if not week:
                await ctx.send("No active week.")
                return
            probs = _sorted_probs(await q.get_problems_for_day(conn, str(ctx.guild.id), today))
            # NOTE: `handles` has no guild_id column (it's not guild-scoped) —
            # we pull every registered handle and filter to this guild's
            # members afterward via guild.get_member().
            users = await conn.fetch("SELECT DISTINCT discord_id FROM handles")

        if not probs:
            await ctx.send(f"No problems for today (`{today}`).")
            return

        msg = await ctx.send(
            f"Checking **{len(users)}** member(s) across "
            f"**{len(probs)}** problem(s) for `{today}`…  *(~{len(users) * 2}s)*"
        )

        summary, total_new = await self._bulk_check_guild(
            ctx.guild, probs, str(ctx.guild.id)
        )

        embed = discord.Embed(
            title=f"Bulk Check  ·  {today}",
            description="\n".join(summary) or "No registered members found.",
            color=COLOR_INFO,
        )
        embed.set_footer(text=f"Total new points: {total_new}  ·  Week: {week['label']}")
        await msg.edit(content=None, embed=embed)

    @check_all.error
    async def check_all_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send(f"You need the **{ADMIN_ROLE}** role or Administrator permission.")

    # ── Core: check one member ────────────────────────────────────────────────

    async def _check_member(
        self,
        member: discord.Member,
        probs: list,
        guild_id: str,
        delay: float = API_HIT_DELAY,
    ) -> tuple[list[str], int]:
        """
        Check and award points for a single member.

        CF optimisation: one bulk fetch → local filter (0 extra API calls).
        Other platforms:  sequential with `delay` seconds between hits.
        """
        results      = [None] * len(probs)
        total_earned = 0
        pool         = get_pool()
        now_utc      = datetime.now(timezone.utc)

        # v2.2: fetch active month once so we can pass month_id to record_solve
        async with pool.acquire() as conn:
            month    = await q.get_active_month(conn, guild_id)
            month_id = month["id"] if month else None

        api_groups: dict[str, list[tuple[int, dict]]] = {}

        # ── Fast local pre-checks (no network) ───────────────────────────────
        for idx, prob in enumerate(probs):
            prob_id       = prob["id"]
            pid           = prob["problem_id"]
            pts           = prob["points"]
            platform      = prob["platform"]
            assigned_date = prob["assigned_date"]
            adapter       = P.get(platform)

            day_start_utc, day_end_utc = _day_window(assigned_date)

            if now_utc > day_end_utc:
                results[idx] = _line(
                    idx + 1, platform, pid,
                    f"Day ended (`{assigned_date}`) — no points awarded",
                )
                continue

            async with pool.acquire() as conn:
                already = await q.has_solved(conn, str(member.id), prob_id)

            if already:
                results[idx] = _line(idx + 1, platform, pid, f"Already recorded  ·  +{pts} pts")
                continue

            if not adapter:
                results[idx] = _line(idx + 1, platform, pid, "Platform adapter unavailable")
                continue

            async with pool.acquire() as conn:
                handle = await q.get_handle(conn, str(member.id), platform)

            if not handle:
                results[idx] = _line(
                    idx + 1, platform, pid,
                    f"No {adapter.NAME} handle linked  ·  `!register {platform} <handle>`",
                )
                continue

            api_groups.setdefault(platform, []).append((idx, prob))

        if not api_groups:
            return results, total_earned

        # ── Platform groups: different platforms run concurrently, ────────────
        # within each platform calls are sequential with `delay` gaps.
        async def _run_platform_group(platform: str, items: list[tuple[int, dict]]):
            adapter = P.get(platform)
            earned  = 0

            async with pool.acquire() as conn:
                handle = await q.get_handle(conn, str(member.id), platform)

            if not handle:
                for idx, prob in items:
                    results[idx] = _line(
                        idx + 1, platform, prob["problem_id"],
                        f"No {adapter.NAME} handle linked",
                    )
                return 0

            # CF: single bulk fetch, then local filter — zero extra API calls.
            # v5 FIX: if this fails (rate-limited / Cloudflare block), do NOT
            # fall through to per-problem CF calls — that just makes the
            # block worse. Mark these problems unavailable and stop here.
            if platform == "cf" and hasattr(adapter, "fetch_all_submissions"):
                try:
                    bulk_submissions = await adapter.fetch_all_submissions(handle)
                except Exception as e:
                    print(f"[checker] CF bulk fetch failed for {handle}: {e}")
                    for idx, prob in items:
                        results[idx] = _line(
                            idx + 1, platform, prob["problem_id"],
                            "Codeforces temporarily unavailable — try `!check` again shortly",
                        )
                    return 0

                for idx, prob in items:
                    pid           = prob["problem_id"]
                    pts           = prob["points"]
                    prob_id       = prob["id"]
                    assigned_date = prob["assigned_date"]
                    day_start_utc, day_end_utc = _day_window(assigned_date)

                    try:
                        solved, status = adapter.check_solved_from_submissions(
                            bulk_submissions, pid,
                            day_start_utc.timestamp(), day_end_utc.timestamp(),
                        )
                    except Exception as e:
                        print(f"[checker] cf / {pid} local-filter error for {handle}: {e}")
                        results[idx] = _line(idx + 1, platform, pid, f"Error: `{e}`")
                        continue

                    earned += await self._apply_result(
                        results, idx, member, prob_id, pid, pts, platform, solved, status, pool, guild_id,
                        month_id=month_id,   # v2.2
                    )

                return earned

            # Non-CF platforms: sequential per-problem calls with `delay` gaps.
            for i, (idx, prob) in enumerate(items):
                pid           = prob["problem_id"]
                pts           = prob["points"]
                prob_id       = prob["id"]
                assigned_date = prob["assigned_date"]
                day_start_utc, day_end_utc = _day_window(assigned_date)

                try:
                    if i > 0:
                        await asyncio.sleep(delay)

                    solved, status = await adapter.check_solved(
                        handle, pid,
                        day_start_utc.timestamp(), day_end_utc.timestamp(),
                    )

                    earned += await self._apply_result(
                        results, idx, member, prob_id, pid, pts, platform, solved, status, pool, guild_id,
                        month_id=month_id,   # v2.2
                    )

                except Exception as e:
                    print(f"[checker] {platform} / {pid} error for {handle}: {e}")
                    results[idx] = _line(idx + 1, platform, pid, f"API error: `{e}`")

            return earned

        earned_per_group = await asyncio.gather(
            *[_run_platform_group(plt, items) for plt, items in api_groups.items()]
        )
        total_earned += sum(earned_per_group)
        return results, total_earned

    async def _apply_result(
        self, results, idx, member, prob_id, pid, pts, platform, solved, status, pool, guild_id,
        month_id=None,   # v2.2: passed through so monthly_solves gets populated
    ) -> int:
        """Shared "award + format result line" logic for both CF (local-filter) and
        per-problem (network) check paths, so the two stay in sync.
        `idx` is the position in the (already-sorted) probs list, so the
        rendered line lands in the same row !problems would show it in."""
        if solved:
            async with pool.acquire() as conn:
                newly = await q.record_solve(
                    conn,
                    discord_id    = str(member.id),
                    problem_db_id = prob_id,
                    guild_id      = guild_id,
                    solved_at     = datetime.now(timezone.utc),
                    points        = pts,
                    month_id      = month_id,   # v2.2: mirrors solve into monthly_solves
                )
            if newly:
                results[idx] = _line(idx + 1, platform, pid, f"Solved  ·  +{pts} pts")
                return pts
            else:
                results[idx] = _line(idx + 1, platform, pid, "Already recorded")
                return 0
        else:
            clean_status = status.replace("❌ ", "").replace("⚠️ ", "").strip()
            results[idx] = _line(idx + 1, platform, pid, clean_status)
            return 0

    # ── Core: bulk-check all members in a guild ───────────────────────────────

    async def _bulk_check_guild(
        self,
        guild: discord.Guild,
        probs: list,
        guild_id: str,
    ) -> tuple[list[str], int]:
        """
        Sequentially check every registered member with BULK_MEMBER_DELAY between
        each one.  Returns (summary_lines, total_new_points).

        Why sequential + delay (not concurrent)?
        Hitting CF with 20 simultaneous user.status calls triggers 503s.
        Sequential + 2 s gap keeps us well under every platform's rate limit.
        """
        pool    = get_pool()
        summary = []
        total   = 0

        async with pool.acquire() as conn:
            # NOTE: `handles` has no guild_id column (it's not guild-scoped) —
            # we pull every registered handle and filter to this guild's
            # members afterward via guild.get_member() in the loop below.
            users = await conn.fetch("SELECT DISTINCT discord_id FROM handles")

        for i, row in enumerate(users):
            member = guild.get_member(int(row["discord_id"]))
            if not member:
                continue

            if i > 0:
                # Breathing room between members — the main rate-limit defence
                await asyncio.sleep(BULK_MEMBER_DELAY)

            try:
                results, earned = await self._check_member(
                    member, probs, guild_id, delay=BULK_PROBLEM_DELAY
                )
            except Exception as e:
                print(f"[bulk_check] _check_member error for {member.id}: {e}")
                summary.append(f"**{member.display_name}**  —  error: `{e}`")
                continue

            total += earned
            results      = [r if r is not None else "error" for r in results]
            solved_count = sum(1 for r in results if "Solved" in r or "Already" in r)
            badge        = f"**+{earned} pts**" if earned else "—"
            summary.append(f"**{member.display_name}**  {badge}  ·  `{solved_count}/{len(probs)}`")

        return summary, total

    # ── Background: auto-check every 6 h (silent) ────────────────────────────

    @tasks.loop(hours=6)
    async def auto_check_6h(self):
        """Silently award points for today's problems every 6 h."""
        today = q.today_ist()
        for guild in self.bot.guilds:
            pool = get_pool()
            try:
                async with pool.acquire() as conn:
                    week  = await q.get_active_week(conn, str(guild.id))
                    if not week:
                        continue
                    probs = _sorted_probs(await q.get_problems_for_day(conn, str(guild.id), today))
                if probs:
                    await self._bulk_check_guild(guild, probs, str(guild.id))
            except Exception as e:
                print(f"[auto_check_6h] Guild {guild.id}: {e}")

    @auto_check_6h.before_loop
    async def before_auto_check(self):
        await self.bot.wait_until_ready()

    # ── Background: nightly check at 23:58 IST ───────────────────────────────

    async def _night_check_loop(self):
        """
        Fires at 23:58 IST every night — 2 min before the daily window closes.
        Bulk-checks all members so last-minute solves get credited before
        the day locks at midnight.

        Posts a summary embed to CHECKALL_CHANNEL_ID (if configured),
        otherwise just logs to console.
        """
        await self.bot.wait_until_ready()

        while not self.bot.is_closed():
            wait = _seconds_until_ist(23, 58)
            print(f"[night_check] Next nightly check in {wait/3600:.1f} h")
            await asyncio.sleep(wait)

            today = q.today_ist()
            print(f"[night_check] Running nightly bulk-check for {today}")

            for guild in self.bot.guilds:
                pool = get_pool()
                try:
                    async with pool.acquire() as conn:
                        week  = await q.get_active_week(conn, str(guild.id))
                        if not week:
                            continue
                        probs = _sorted_probs(await q.get_problems_for_day(conn, str(guild.id), today))

                    if not probs:
                        print(f"[night_check] Guild {guild.id}: no problems today, skipping.")
                        continue

                    summary, total_new = await self._bulk_check_guild(
                        guild, probs, str(guild.id)
                    )
                    print(f"[night_check] Guild {guild.id}: {total_new} new pts awarded.")

                    # Post summary to configured channel (optional)
                    if CHECKALL_CHANNEL_ID:
                        channel = guild.get_channel(CHECKALL_CHANNEL_ID)
                        if channel:
                            embed = discord.Embed(
                                title=f"Nightly Auto-Check  ·  {today}",
                                description="\n".join(summary) or "No members found.",
                                color=COLOR_INFO,
                            )
                            embed.set_footer(
                                text=f"Auto-run at 23:58 IST  ·  New points this run: {total_new}  ·  Week: {week['label']}"
                            )
                            await channel.send(embed=embed)

                except Exception as e:
                    print(f"[night_check] Guild {guild.id} error: {e}")

            # Sleep ~23 h 55 min before recalculating (avoids double-fire)
            await asyncio.sleep(23 * 3600 + 55 * 60)

    # ── Background: midnight IST daily-reset ─────────────────────────────────
    # INTENTIONALLY DISABLED — no solves are deleted at midnight.
    #
    # Daily leaderboard already filters by assigned_date = today, so it
    # naturally shows 0 entries the next day without touching the DB.
    # Weekly solves must stay intact until the week ends (!resetweek).
    # Monthly solves must stay intact until the month ends (!resetmonth).
    # Deleting solves at midnight was the root cause of weekly/monthly
    # leaderboards going blank. The _midnight_task field is kept so
    # cog_unload does not crash.

    # ── Start background loops on bot ready ──────────────────────────────────

    @commands.Cog.listener()
    async def on_ready(self):
        # _midnight_task intentionally not started — see note above
        self._midnight_task = None

        if self._night_check_task is None or self._night_check_task.done():
            self._night_check_task = asyncio.create_task(self._night_check_loop())
            print("✅  23:58 IST nightly auto-check task started.")


async def setup(bot):
    await bot.add_cog(Checker(bot))