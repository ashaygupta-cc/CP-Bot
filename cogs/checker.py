"""
cogs/checker.py
Commands: /check, /checkall
v2: Points only awarded if problem's assigned_date == today (IST).
    Checks solve timestamp against the day window of assigned_date.
"""

import discord
from discord.ext import commands, tasks
from datetime import datetime, timezone, timedelta
from database.connection import get_pool
from database import queries as q
import platforms as P
from config import COLOR_SUCCESS, COLOR_ERROR, COLOR_INFO, COLOR_WARN, ADMIN_ROLE

IST = q.IST


def _day_window(target_date) -> tuple[datetime, datetime]:
    return q._day_window_utc(target_date)


class Checker(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.auto_check.start()

    def cog_unload(self):
        self.auto_check.cancel()

    # ── /check ──────────────────────────────────────────────────────────────

    @commands.command(name="check")
    async def check(self, ctx, member: discord.Member = None):
        """
        Check your (or another member's) solve status for today's problems.
        /check
        /check @friend
        """
        target = member or ctx.author
        pool   = get_pool()
        today  = q.today_ist()

        async with pool.acquire() as conn:
            week = await q.get_active_week(conn, str(ctx.guild.id))
            if not week:
                await ctx.send("❌ No active week. Admin: `!setweek`")
                return
            # Only today's problems
            probs = await q.get_problems_for_day(conn, str(ctx.guild.id), today)

        if not probs:
            # Also show if week has problems but none today
            async with pool.acquire() as conn:
                all_probs = await q.get_problems_for_week(conn, str(ctx.guild.id), week["id"])
            if all_probs:
                await ctx.send(f"📭 No problems assigned for today (`{today}`). Check `!problems` for the full schedule.")
            else:
                await ctx.send("📭 No problems assigned this week yet.")
            return

        msg = await ctx.send(f"🔍 Checking **{target.display_name}**'s submissions for today...")
        results, total_earned = await self._check_member(target, probs, str(ctx.guild.id))

        color = COLOR_SUCCESS if total_earned > 0 else COLOR_INFO
        embed = discord.Embed(
            title=f"🔍  Solve Check  —  {target.display_name}",
            description="\n".join(results),
            color=color,
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(
            name="📅 Date",
            value=f"`{today}`",
            inline=True,
        )
        embed.add_field(
            name="🏅 Points Earned",
            value=f"**+{total_earned} pts**" if total_earned > 0 else "No new points",
            inline=True,
        )
        embed.set_footer(text=f"Week: {week['label']}  ·  Only today's problems count")
        await msg.edit(content=None, embed=embed)

    # ── /checkall ────────────────────────────────────────────────────────────

    @commands.command(name="checkall")
    @commands.has_permissions(administrator=True)
    async def check_all(self, ctx):
        """(Admin) Bulk-check ALL registered members for today's problems."""
        pool  = get_pool()
        today = q.today_ist()

        async with pool.acquire() as conn:
            week  = await q.get_active_week(conn, str(ctx.guild.id))
            if not week:
                await ctx.send("❌ No active week.")
                return
            probs = await q.get_problems_for_day(conn, str(ctx.guild.id), today)
            users = await conn.fetch("SELECT DISTINCT discord_id FROM handles")

        if not probs:
            await ctx.send(f"📭 No problems for today (`{today}`).")
            return

        msg = await ctx.send(
            f"⏳ Checking **{len(users)}** member(s) across **{len(probs)}** problem(s) for `{today}`…"
        )
        summary, total_new = [], 0

        for row in users:
            member = ctx.guild.get_member(int(row["discord_id"]))
            if not member:
                continue
            results, earned = await self._check_member(member, probs, str(ctx.guild.id))
            total_new += earned
            solved_count = sum(1 for r in results if "Solved" in r or "Already" in r)
            badge = f"**+{earned} pts**" if earned else "—"
            summary.append(f"**{member.display_name}**  {badge}  `{solved_count}/{len(probs)}`")

        embed = discord.Embed(
            title=f"📊  Bulk Check  —  {today}",
            description="\n".join(summary) or "No registered members found.",
            color=COLOR_INFO,
        )
        embed.set_footer(text=f"Total new points awarded: {total_new}  ·  Week: {week['label']}")
        await msg.edit(content=None, embed=embed)

    # ── Core logic ───────────────────────────────────────────────────────────

    async def _check_member(
        self, member: discord.Member, probs: list, guild_id: str
    ) -> tuple[list[str], int]:
        """
        For each problem, check if the member solved it WITHIN the day window
        of that problem's assigned_date.
        """
        results      = []
        total_earned = 0
        pool         = get_pool()

        for prob in probs:
            prob_id       = prob["id"]
            pid           = prob["problem_id"]
            pts           = prob["points"]
            platform      = prob["platform"]
            assigned_date = prob["assigned_date"]
            adapter       = P.get(platform)

            # Day window = midnight-to-midnight IST of the assigned day
            day_start_utc, day_end_utc = _day_window(assigned_date)

            async with pool.acquire() as conn:
                already = await q.has_solved(conn, str(member.id), prob_id)

            if already:
                results.append(f"✅ `{platform.upper()} {pid}` — Already recorded (+{pts} pts)")
                continue

            if not adapter:
                results.append(f"⚠️ `{platform.upper()} {pid}` — Platform adapter unavailable.")
                continue

            async with pool.acquire() as conn:
                handle = await q.get_handle(conn, str(member.id), platform)

            if not handle:
                results.append(
                    f"⚠️ `{platform.upper()} {pid}` — No handle. `!register {platform} <handle>`"
                )
                continue

            # Check platform: solved within the day window
            solved, status = await adapter.check_solved(
                handle, pid, day_start_utc.timestamp()
            )

            if solved:
                # Verify it's not AFTER the day window ends
                # (adapter returns most-recent AC; we trust it's within window since
                #  we pass since_ts; if platform gives exact timestamp we could double-check)
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
                    results.append(f"✅ `{platform.upper()} {pid}` — **Solved! +{pts} pts** 🎉")
                else:
                    results.append(f"✅ `{platform.upper()} {pid}` — Already recorded.")
            else:
                results.append(f"❌ `{platform.upper()} {pid}` — {status.replace('❌ ', '')}")

        return results, total_earned

    # ── Auto check ───────────────────────────────────────────────────────────

    @tasks.loop(hours=6)
    async def auto_check(self):
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


async def setup(bot):
    await bot.add_cog(Checker(bot))
