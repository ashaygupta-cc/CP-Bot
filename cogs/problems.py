"""
cogs/problems.py
Commands: !addproblem, !removeproblem, !problems, !setdifficulty
"""

import discord
from discord.ext import commands
from datetime import date
from database.connection import get_pool
from database import queries as q
import platforms as P
from config import COLOR_SUCCESS, COLOR_ERROR, COLOR_INFO, COLOR_WARN, ADMIN_ROLE


def is_admin():
    async def predicate(ctx):
        return (
            any(r.name == ADMIN_ROLE for r in ctx.author.roles)
            or ctx.author.guild_permissions.administrator
        )
    return commands.check(predicate)


PLATFORM_EMOJIS = {"cf": "🔵", "lc": "🟡", "cc": "🟤", "atcoder": "🔴"}


class Problems(commands.Cog):
    """Manage the problems assigned each week."""

    def __init__(self, bot):
        self.bot = bot

    # ── !addproblem ────────────────────────────────────────────────────────

    @commands.command(name="addproblem")
    @is_admin()
    async def add_problem(self, ctx, platform: str = None, problem_id: str = None,
                          difficulty: str = None, custom_points: int = None):
        """
        Add a problem to the current week.
        !addproblem cf 1234A hard
        !addproblem lc two-sum easy 7          ← custom points override
        !addproblem atcoder abc123_a medium

        If custom_points is omitted, points come from !setpoints config.
        """
        if not platform or not problem_id or not difficulty:
            await ctx.send(
                "**Usage:** `!addproblem <platform> <problem_id> <difficulty> [custom_points]`\n"
                "**Example (CF):** `!addproblem cf 1234A hard`\n"
                "**Example (LC):** `!addproblem lc two-sum medium`\n"
                "**Example (AC):** `!addproblem atcoder abc123_a easy`\n"
                f"**Platforms:** {P.choices_str()}"
            )
            return

        adapter = P.get(platform)
        if not adapter:
            await ctx.send(f"❌ Unknown platform `{platform}`. Supported: {P.choices_str()}")
            return

        pool = get_pool()
        async with pool.acquire() as conn:
            week = await q.get_active_week(conn, str(ctx.guild.id))
            if not week:
                await ctx.send(
                    "❌ No active week. Create one first with `!setweek \"Week 1\" YYYY-MM-DD YYYY-MM-DD`."
                )
                return

            # Resolve points
            if custom_points is not None:
                points = custom_points
            else:
                cfg    = await q.get_difficulty_points(conn, str(ctx.guild.id))
                points = cfg.get(difficulty.lower())
                if points is None:
                    difficulties = ", ".join(f"`{d}`" for d in cfg)
                    await ctx.send(
                        f"❌ Unknown difficulty `{difficulty}`. "
                        f"Available: {difficulties}\n"
                        f"Or set a custom one with `!setpoints {difficulty} <pts>`."
                    )
                    return

            pid = adapter.format_problem_id(problem_id)
            prob_db_id = await q.add_problem(
                conn,
                guild_id      = str(ctx.guild.id),
                week_id       = week["id"],
                platform      = adapter.KEY,
                problem_id    = pid,
                title         = None,
                difficulty    = difficulty.lower(),
                points        = points,
                set_by        = str(ctx.author.id),
            )

        url = adapter.problem_url(pid)
        embed = discord.Embed(title="📌 Problem Added", color=COLOR_SUCCESS)
        embed.add_field(name="Platform",    value=adapter.NAME,            inline=True)
        embed.add_field(name="Problem ID",  value=f"`{pid}`",              inline=True)
        embed.add_field(name="Difficulty",  value=difficulty.capitalize(), inline=True)
        embed.add_field(name="Points",      value=f"{points} pts",         inline=True)
        embed.add_field(name="Week",        value=week["label"],           inline=True)
        embed.add_field(name="DB ID",       value=f"#{prob_db_id}",        inline=True)
        if url:
            embed.add_field(name="Link", value=f"[Open Problem]({url})", inline=False)
        await ctx.send(embed=embed)

    # ── !removeproblem ─────────────────────────────────────────────────────

    @commands.command(name="removeproblem")
    @is_admin()
    async def remove_problem(self, ctx, problem_db_id: int = None):
        """Remove a problem by its DB ID.  !removeproblem 42"""
        if problem_db_id is None:
            await ctx.send("Usage: `!removeproblem <db_id>`  (find ID with `!problems`)")
            return

        pool = get_pool()
        async with pool.acquire() as conn:
            prob = await q.get_problem_by_id(conn, problem_db_id)
            if not prob or prob["guild_id"] != str(ctx.guild.id):
                await ctx.send(f"❌ Problem `#{problem_db_id}` not found in this server.")
                return
            await q.remove_problem(conn, problem_db_id, str(ctx.guild.id))

        await ctx.send(
            f"✅ Removed **{prob['platform'].upper()} `{prob['problem_id']}`** (#{problem_db_id})."
        )

    # ── !problems ──────────────────────────────────────────────────────────

    @commands.command(name="problems", aliases=["week"])
    async def list_problems(self, ctx):
        """Show all problems for the current week."""
        pool = get_pool()
        async with pool.acquire() as conn:
            week = await q.get_active_week(conn, str(ctx.guild.id))
            if not week:
                await ctx.send("❌ No active week. Admin: `!setweek`")
                return
            probs = await q.get_problems_for_week(conn, str(ctx.guild.id), week["id"])

        embed = discord.Embed(
            title=f"📋 Problems — {week['label']}",
            description=(
                f"📅 **{week['start_date']}** → **{week['end_date']}**\n"
                f"{len(probs)} problem(s) assigned"
            ),
            color=COLOR_INFO,
        )

        if not probs:
            embed.add_field(
                name="No problems yet",
                value="Admins can add with `!addproblem <platform> <problem_id> <difficulty>`",
                inline=False,
            )
        else:
            for prob in probs:
                adapter = P.get(prob["platform"])
                emoji   = PLATFORM_EMOJIS.get(prob["platform"], "⚪")
                name    = adapter.NAME if adapter else prob["platform"]
                url     = adapter.problem_url(prob["problem_id"]) if adapter else None
                link    = f"[Link]({url})" if url else "N/A"

                embed.add_field(
                    name=f"{emoji} {name} `{prob['problem_id']}`  (#{prob['id']})",
                    value=(
                        f"**Difficulty:** {prob['difficulty'].capitalize()}  "
                        f"**Points:** {prob['points']} pts  "
                        f"{link}"
                    ),
                    inline=False,
                )

        await ctx.send(embed=embed)

    # ── !setdifficulty ─────────────────────────────────────────────────────

    @commands.command(name="setdifficulty")
    @is_admin()
    async def set_difficulty(self, ctx, problem_db_id: int = None, difficulty: str = None):
        """
        Change difficulty (and recalculate points) for a problem.
        !setdifficulty 42 hard
        """
        if problem_db_id is None or difficulty is None:
            await ctx.send(
                "Usage: `!setdifficulty <db_id> <difficulty>`\n"
                "Example: `!setdifficulty 42 hard`"
            )
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

        await ctx.send(
            f"✅ Problem `#{problem_db_id}` updated: "
            f"**{difficulty.capitalize()}** → **{points} pts**."
        )

    # ── Error handler ──────────────────────────────────────────────────────

    @add_problem.error
    @remove_problem.error
    @set_difficulty.error
    async def admin_error(self, ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.send(f"❌ You need the **{ADMIN_ROLE}** role or Administrator permission.")


async def setup(bot):
    await bot.add_cog(Problems(bot))
