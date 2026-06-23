"""
cogs/checker.py
Commands: !check, !checkall
Background task: auto-checks all members every 6 hours.
"""

import discord
from discord.ext import commands, tasks
from datetime import datetime, timezone
from database.connection import get_pool
from database import queries as q
import platforms as P
from config import COLOR_SUCCESS, COLOR_ERROR, COLOR_INFO, ADMIN_ROLE


def _week_since_ts(week) -> float:
    """Unix timestamp of the week's start date (midnight UTC)."""
    from datetime import datetime, timezone
    start = datetime.combine(week["start_date"], datetime.min.time()).replace(tzinfo=timezone.utc)
    return start.timestamp()


class Checker(commands.Cog):
    """Check whether members have solved assigned problems."""

    def __init__(self, bot):
        self.bot = bot
        self.auto_check.start()

    def cog_unload(self):
        self.auto_check.cancel()

    # ── !check ─────────────────────────────────────────────────────────────

    @commands.command(name="check")
    async def check(self, ctx, member: discord.Member = None):
        """
        Check if you (or another member) solved this week's problems.
        !check
        !check @friend
        """
        target = member or ctx.author
        pool   = get_pool()

        async with pool.acquire() as conn:
            week = await q.get_active_week(conn, str(ctx.guild.id))
            if not week:
                await ctx.send("❌ No active week. Admin: `!setweek`")
                return
            probs = await q.get_problems_for_week(conn, str(ctx.guild.id), week["id"])

        if not probs:
            await ctx.send("📭 No problems assigned this week.")
            return

        msg = await ctx.send(f"🔍 Checking `{target.display_name}`'s submissions...")
        results, total_earned = await self._check_member(target, probs, week, str(ctx.guild.id))

        embed = discord.Embed(
            title=f"🔍 Solve Check — {target.display_name}",
            description="\n".join(results),
            color=COLOR_SUCCESS if total_earned > 0 else COLOR_INFO,
        )
        embed.set_footer(
            text=f"+{total_earned} pts earned" if total_earned > 0 else "No new points this check"
        )
        await msg.edit(content=None, embed=embed)

    # ── !checkall ──────────────────────────────────────────────────────────

    @commands.command(name="checkall")
    @commands.has_permissions(administrator=True)
    async def check_all(self, ctx):
        """(Admin) Check ALL registered members for this week's problems."""
        pool = get_pool()
        async with pool.acquire() as conn:
            week  = await q.get_active_week(conn, str(ctx.guild.id))
            if not week:
                await ctx.send("❌ No active week.")
                return
            probs = await q.get_problems_for_week(conn, str(ctx.guild.id), week["id"])
            users = await conn.fetch("SELECT DISTINCT discord_id FROM handles")

        if not probs:
            await ctx.send("📭 No problems assigned this week.")
            return

        msg = await ctx.send(
            f"⏳ Checking {len(users)} member(s) across {len(probs)} problem(s)…"
        )
        summary_lines = []
        total_new     = 0

        for row in users:
            uid    = row["discord_id"]
            member = ctx.guild.get_member(int(uid))
            if not member:
                continue

            results, earned = await self._check_member(member, probs, week, str(ctx.guild.id))
            total_new += earned
            solved_count  = sum(1 for r in results if r.startswith("✅"))
            pts_str       = f" (+{earned} pts)" if earned else ""
            summary_lines.append(
                f"**{member.display_name}**{pts_str}: "
                f"{solved_count}/{len(probs)} solved"
            )

        embed = discord.Embed(
            title=f"📊 Bulk Check — {week['label']}",
            description="\n".join(summary_lines) or "No registered members.",
            color=COLOR_INFO,
        )
        embed.set_footer(text=f"Total points awarded this run: {total_new}")
        await msg.edit(content=None, embed=embed)

    # ── Core logic ─────────────────────────────────────────────────────────

    async def _check_member(
        self, member: discord.Member, probs: list, week, guild_id: str
    ) -> tuple[list[str], int]:
        """
        Check every unresolved problem for a member.
        Returns (result_lines, total_points_newly_awarded).
        """
        since_ts    = _week_since_ts(week)
        results     = []
        total_earned = 0
        pool        = get_pool()

        for prob in probs:
            prob_id  = prob["id"]
            pid      = prob["problem_id"]
            pts      = prob["points"]
            platform = prob["platform"]
            adapter  = P.get(platform)

            async with pool.acquire() as conn:
                already = await q.has_solved(conn, str(member.id), prob_id)

            if already:
                results.append(f"✅ `{platform.upper()} {pid}` — Already solved (+{pts} pts)")
                continue

            if not adapter:
                results.append(f"⚠️ `{platform.upper()} {pid}` — Platform adapter unavailable.")
                continue

            async with pool.acquire() as conn:
                handle = await q.get_handle(conn, str(member.id), platform)

            if not handle:
                results.append(
                    f"⚠️ `{platform.upper()} {pid}` — "
                    f"No {adapter.NAME} handle. Use `!register {platform} <handle>`."
                )
                continue

            solved, status = await adapter.check_solved(handle, pid, since_ts)

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
                    total_earned += pts
                results.append(f"✅ `{platform.upper()} {pid}` — Solved! **+{pts} pts**")
            else:
                results.append(f"❌ `{platform.upper()} {pid}` — {status.replace('❌ ', '')}")

        return results, total_earned

    # ── Auto check (background task) ───────────────────────────────────────

    @tasks.loop(hours=6)
    async def auto_check(self):
        """Silently check all members every 6 hours. No messages sent."""
        for guild in self.bot.guilds:
            pool = get_pool()
            try:
                async with pool.acquire() as conn:
                    week  = await q.get_active_week(conn, str(guild.id))
                    if not week:
                        continue
                    probs = await q.get_problems_for_week(conn, str(guild.id), week["id"])
                    users = await conn.fetch("SELECT DISTINCT discord_id FROM handles")

                for row in users:
                    member = guild.get_member(int(row["discord_id"]))
                    if member:
                        await self._check_member(member, probs, week, str(guild.id))
            except Exception:
                pass   # Don't crash the task on one guild's error

    @auto_check.before_loop
    async def before_auto_check(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Checker(bot))
