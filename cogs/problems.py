"""
cogs/problems.py
Commands: /addproblem, /removeproblem, /problems, /setdifficulty
v2: assigned_date required, /removeproblem keeps solve history by default
"""

import discord
from discord.ext import commands
from datetime import date
from database.connection import get_pool
from database import queries as q
import platforms as P
from config import COLOR_SUCCESS, COLOR_ERROR, COLOR_INFO, COLOR_WARN, ADMIN_ROLE

PLATFORM_EMOJIS = {"cf": "🔵", "lc": "🟡", "cc": "🟤", "atcoder": "🔴"}
DIFF_EMOJIS     = {"easy": "🟢", "medium": "🟡", "hard": "🔴", "expert": "🟣", "master": "⚫"}


def is_admin():
    async def predicate(ctx):
        return (
            any(r.name == ADMIN_ROLE for r in ctx.author.roles)
            or ctx.author.guild_permissions.administrator
        )
    return commands.check(predicate)


class Problems(commands.Cog):
    """Manage the problems assigned each week."""

    def __init__(self, bot):
        self.bot = bot

    # ── /addproblem ─────────────────────────────────────────────────────────

    @commands.command(name="addproblem")
    @is_admin()
    async def add_problem(self, ctx, platform: str = None, problem_id: str = None,
                          difficulty: str = None, assigned_date: str = None,
                          custom_points: int = None):
        """
        Add a problem to the current week for a specific date.
        /addproblem cf 1234A hard 2026-06-26
        /addproblem lc two-sum easy 2026-06-27 7
        """
        if not platform or not problem_id or not difficulty or not assigned_date:
            await ctx.send(
                "**Usage:** `!addproblem <platform> <problem_id> <difficulty> <YYYY-MM-DD> [custom_points]`\n"
                "**Example:** `!addproblem cf 1234A hard 2026-06-26`\n"
                "**Example:** `!addproblem lc two-sum easy 2026-06-27 7`\n"
                f"**Platforms:** {P.choices_str()}"
            )
            return

        adapter = P.get(platform)
        if not adapter:
            await ctx.send(f"❌ Unknown platform `{platform}`. Supported: {P.choices_str()}")
            return

        try:
            a_date = date.fromisoformat(assigned_date)
        except ValueError:
            await ctx.send("❌ Date must be `YYYY-MM-DD`, e.g. `2026-06-26`.")
            return

        pool = get_pool()
        async with pool.acquire() as conn:
            week = await q.get_active_week(conn, str(ctx.guild.id))
            if not week:
                await ctx.send("❌ No active week. Create one with `!setweek \"Week 1\" YYYY-MM-DD YYYY-MM-DD`.")
                return

            if not (week["start_date"] <= a_date <= week["end_date"]):
                await ctx.send(
                    f"❌ Date `{a_date}` is outside the active week "
                    f"(`{week['start_date']}` → `{week['end_date']}`)."
                )
                return

            # Resolve points
            if custom_points is not None:
                points = custom_points
            else:
                cfg    = await q.get_difficulty_points(conn, str(ctx.guild.id))
                points = cfg.get(difficulty.lower())
                if points is None:
                    diffs = ", ".join(f"`{d}`" for d in cfg)
                    await ctx.send(f"❌ Unknown difficulty `{difficulty}`. Available: {diffs}")
                    return

            # Get active month too
            month = await q.get_active_month(conn, str(ctx.guild.id))
            month_id = month["id"] if month else None

            pid = adapter.format_problem_id(problem_id)
            prob_db_id = await q.add_problem(
                conn,
                guild_id      = str(ctx.guild.id),
                week_id       = week["id"],
                month_id      = month_id,
                platform      = adapter.KEY,
                problem_id    = pid,
                title         = None,
                difficulty    = difficulty.lower(),
                points        = points,
                set_by        = str(ctx.author.id),
                assigned_date = a_date,
            )

        url   = adapter.problem_url(pid)
        demoji = DIFF_EMOJIS.get(difficulty.lower(), "⚪")
        pemoji = PLATFORM_EMOJIS.get(adapter.KEY, "⚪")

        embed = discord.Embed(
            title=f"{pemoji} Problem Added to {week['label']}",
            color=COLOR_SUCCESS
        )
        embed.add_field(name="Platform",   value=adapter.NAME,                        inline=True)
        embed.add_field(name="Problem ID", value=f"`{pid}`",                          inline=True)
        embed.add_field(name="Difficulty", value=f"{demoji} {difficulty.capitalize()}", inline=True)
        embed.add_field(name="Points",     value=f"**{points} pts**",                 inline=True)
        embed.add_field(name="Day",        value=f"`{a_date}`",                       inline=True)
        embed.add_field(name="DB ID",      value=f"#{prob_db_id}",                    inline=True)
        if url:
            embed.add_field(name="🔗 Link", value=f"[Open Problem]({url})", inline=False)
        embed.set_footer(text=f"Added by {ctx.author.display_name}")
        await ctx.send(embed=embed)

    # ── /removeproblem ──────────────────────────────────────────────────────

    @commands.command(name="removeproblem")
    @is_admin()
    async def remove_problem(self, ctx, problem_db_id: int = None, keep_history: str = "yes"):
        """
        Remove a problem. Solve history kept by default.
        /removeproblem 42           — keeps past solves
        /removeproblem 42 no        — deletes solves too (points gone)
        """
        if problem_db_id is None:
            await ctx.send(
                "**Usage:** `!removeproblem <db_id> [keep_history: yes/no]`\n"
                "Default keeps solve history. Use `no` to also delete recorded solves."
            )
            return

        pool = get_pool()
        async with pool.acquire() as conn:
            prob = await q.get_problem_by_id(conn, problem_db_id)
            if not prob or prob["guild_id"] != str(ctx.guild.id):
                await ctx.send(f"❌ Problem `#{problem_db_id}` not found in this server.")
                return

            if keep_history.lower() in ("no", "false", "0"):
                await q.hard_remove_problem(conn, problem_db_id, str(ctx.guild.id))
                note = "Solve records also deleted."
            else:
                await q.remove_problem_keep_solves(conn, problem_db_id, str(ctx.guild.id))
                note = "Past solve records kept (points preserved)."

        pemoji = PLATFORM_EMOJIS.get(prob["platform"], "⚪")
        embed = discord.Embed(
            title=f"{pemoji} Problem Removed",
            description=(
                f"`{prob['platform'].upper()} {prob['problem_id']}` (#{problem_db_id}) removed.\n"
                f"*{note}*"
            ),
            color=COLOR_WARN,
        )
        embed.set_footer(text=f"By {ctx.author.display_name}")
        await ctx.send(embed=embed)

    # ── /problems ───────────────────────────────────────────────────────────

    @commands.command(name="problems")
    async def list_problems(self, ctx):
        """Show all problems for the current week, grouped by day."""
        pool = get_pool()
        async with pool.acquire() as conn:
            week = await q.get_active_week(conn, str(ctx.guild.id))
            if not week:
                await ctx.send("❌ No active week. Admin: `!setweek`")
                return
            probs = await q.get_problems_for_week(conn, str(ctx.guild.id), week["id"])

        embed = discord.Embed(
            title=f"📋  {week['label']}  —  Problem Set",
            description=f"📅  `{week['start_date']}` → `{week['end_date']}`",
            color=COLOR_INFO,
        )
        embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon.url if ctx.guild.icon else None)

        if not probs:
            embed.add_field(
                name="No problems yet",
                value="Admins: `!addproblem <platform> <id> <difficulty> <YYYY-MM-DD>`",
                inline=False,
            )
        else:
            # Group by day
            by_day: dict[date, list] = {}
            for p in probs:
                by_day.setdefault(p["assigned_date"], []).append(p)

            today = q.today_ist()
            for day, day_probs in sorted(by_day.items()):
                day_label = day.strftime("%A, %d %b")
                if day == today:
                    day_label = f"📍 TODAY — {day_label}"
                elif day < today:
                    day_label = f"✅ {day_label} (past)"
                else:
                    day_label = f"🔜 {day_label}"

                lines = []
                for prob in day_probs:
                    adapter = P.get(prob["platform"])
                    pemoji  = PLATFORM_EMOJIS.get(prob["platform"], "⚪")
                    demoji  = DIFF_EMOJIS.get(prob["difficulty"], "⚪")
                    url     = adapter.problem_url(prob["problem_id"]) if adapter else None
                    link    = f"[{prob['problem_id']}]({url})" if url else f"`{prob['problem_id']}`"
                    lines.append(
                        f"{pemoji} **{link}**  {demoji} {prob['difficulty'].capitalize()}  "
                        f"·  **{prob['points']} pts**  ·  `#{prob['id']}`"
                    )

                embed.add_field(name=day_label, value="\n".join(lines), inline=False)

        embed.set_footer(text=f"{len(probs)} problem(s) total  ·  /check to verify solves")
        await ctx.send(embed=embed)

    # ── /setdifficulty ──────────────────────────────────────────────────────

    @commands.command(name="setdifficulty")
    @is_admin()
    async def set_difficulty(self, ctx, problem_db_id: int = None, difficulty: str = None):
        """Change difficulty (and recalculate points) for a problem. /setdifficulty 42 hard"""
        if problem_db_id is None or difficulty is None:
            await ctx.send("Usage: `!setdifficulty <db_id> <difficulty>`")
            return

        pool = get_pool()
        async with pool.acquire() as conn:
            prob = await q.get_problem_by_id(conn, problem_db_id)
            if not prob or prob["guild_id"] != str(ctx.guild.id):
                await ctx.send(f"❌ Problem `#{problem_db_id}` not found.")
                return

            cfg    = await q.get_difficulty_points(conn, str(ctx.guild.id))
            points = cfg.get(difficulty.lower())
            if points is None:
                await ctx.send(f"❌ Unknown difficulty `{difficulty}`. Use `!setpoints` to add it.")
                return

            await q.set_problem_difficulty(conn, problem_db_id, difficulty, points)

        demoji = DIFF_EMOJIS.get(difficulty.lower(), "⚪")
        await ctx.send(
            f"✅ Problem `#{problem_db_id}` → {demoji} **{difficulty.capitalize()}** · **{points} pts**"
        )

    @add_problem.error
    @remove_problem.error
    @set_difficulty.error
    async def admin_error(self, ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.send(f"❌ You need the **{ADMIN_ROLE}** role or Administrator permission.")


async def setup(bot):
    await bot.add_cog(Problems(bot))
