"""
cogs/reset.py
Admin-only reset commands for the CP Discord Bot.

Commands:
  !resetweek        — Clear all solves for the current active week
  !resetalltime     — Wipe ALL solves ever (nuclear)
  !resetuser        — Remove a specific member's solves (week or all-time)
  !resetproblem     — Un-mark all solves for a specific problem DB ID
  !resetweekfull    — Delete solves + problems + deactivate current week (full reset)
"""

import asyncio
import discord
from discord.ext import commands
from database.connection import get_pool
from database import queries as q
from config import COLOR_SUCCESS, COLOR_ERROR, COLOR_WARN, COLOR_INFO, ADMIN_ROLE


def is_admin():
    async def predicate(ctx):
        return (
            any(r.name == ADMIN_ROLE for r in ctx.author.roles)
            or ctx.author.guild_permissions.administrator
        )
    return commands.check(predicate)


class Reset(commands.Cog):
    """Admin-only reset and data-management commands."""

    def __init__(self, bot):
        self.bot = bot

    # ── !resetweek ──────────────────────────────────────────────────────────

    @commands.command(name="resetweek")
    @is_admin()
    async def reset_week(self, ctx):
        """
        Clear all solves for the currently active week.
        Does NOT delete the week or its problems — members can re-earn points.

        Usage: !resetweek
        """
        pool = get_pool()
        async with pool.acquire() as conn:
            week = await q.get_active_week(conn, str(ctx.guild.id))

        if not week:
            await ctx.send("❌ No active week found. Nothing to reset.")
            return

        embed = discord.Embed(
            title="⚠️ Reset Current Week Solves?",
            description=(
                f"This will delete **all recorded solves** for **{week['label']}** "
                f"(`{week['start_date']}` → `{week['end_date']}`).\n\n"
                "Problems will remain. Members can re-earn points after a fresh `!check`.\n\n"
                "Type `yes` within 15 seconds to confirm."
            ),
            color=COLOR_WARN,
        )
        await ctx.send(embed=embed)

        def check(m):
            return (
                m.author == ctx.author
                and m.channel == ctx.channel
                and m.content.lower() == "yes"
            )

        try:
            await self.bot.wait_for("message", check=check, timeout=15.0)
        except asyncio.TimeoutError:
            await ctx.send("🚫 Reset cancelled — timed out.")
            return

        async with pool.acquire() as conn:
            deleted = await q.reset_current_week_solves(conn, str(ctx.guild.id))

        embed = discord.Embed(
            title="✅ Week Solves Reset",
            description=(
                f"Cleared **{deleted}** solve record(s) for **{week['label']}**.\n"
                "Run `!checkall` to let members re-earn points."
            ),
            color=COLOR_SUCCESS,
        )
        embed.set_footer(text=f"Action by {ctx.author.display_name}")
        await ctx.send(embed=embed)

    # ── !resetalltime ───────────────────────────────────────────────────────

    @commands.command(name="resetalltime")
    @is_admin()
    async def reset_all_time(self, ctx):
        """
        ☢️ NUCLEAR: Permanently delete ALL solves ever for this server.
        Weeks and problems are kept; only solve records are wiped.

        Usage: !resetalltime
        """
        confirmation_phrase = f"CONFIRM WIPE {ctx.author.name}"

        embed = discord.Embed(
            title="☢️ NUCLEAR OPTION — WIPE ALL-TIME SCORES",
            description=(
                "This will **permanently delete every solve record** for every member "
                "across all weeks in this server.\n\n"
                "Weeks and problems are **kept** but all points go to zero.\n\n"
                f"To proceed, type **exactly** (within 30 seconds):\n"
                f"```\n{confirmation_phrase}\n```"
            ),
            color=discord.Color.red(),
        )
        await ctx.send(embed=embed)

        def check(m):
            return (
                m.author == ctx.author
                and m.channel == ctx.channel
                and m.content == confirmation_phrase
            )

        try:
            await self.bot.wait_for("message", check=check, timeout=30.0)
        except asyncio.TimeoutError:
            await ctx.send("🚫 Nuclear reset cancelled — phrase not received in time.")
            return

        pool = get_pool()
        async with pool.acquire() as conn:
            deleted = await q.reset_all_solves(conn, str(ctx.guild.id))

        embed = discord.Embed(
            title="☢️ All-Time Scores Wiped",
            description=f"Deleted **{deleted}** solve record(s) across all weeks.",
            color=discord.Color.red(),
        )
        embed.set_footer(text=f"Action by {ctx.author.display_name}")
        await ctx.send(embed=embed)

    # ── !resetuser ──────────────────────────────────────────────────────────

    @commands.command(name="resetuser")
    @is_admin()
    async def reset_user(self, ctx, member: discord.Member = None, scope: str = "week"):
        """
        Remove a specific member's solve records.

        Usage:
          !resetuser @user          — clear their current-week solves
          !resetuser @user week     — clear their current-week solves
          !resetuser @user all      — clear ALL their solves (all-time)
        """
        if not member:
            await ctx.send(
                "**Usage:** `!resetuser @user [week|all]`\n"
                "**Examples:**\n"
                "  `!resetuser @Alice`         — clear this week's solves\n"
                "  `!resetuser @Alice all`      — clear all-time solves"
            )
            return

        scope = scope.lower()
        if scope not in ("week", "all"):
            await ctx.send("❌ Invalid scope. Use `week` or `all`.")
            return

        pool = get_pool()

        if scope == "week":
            async with pool.acquire() as conn:
                week = await q.get_active_week(conn, str(ctx.guild.id))
                if not week:
                    await ctx.send("❌ No active week. Nothing to reset.")
                    return
                deleted = await q.reset_user_week_solves(
                    conn, str(member.id), str(ctx.guild.id)
                )
            desc = (
                f"Removed **{deleted}** solve record(s) for {member.mention} "
                f"in **{week['label']}**."
            )
        else:
            # Confirm for all-time
            await ctx.send(
                f"⚠️ This will delete **ALL** of {member.mention}'s solves (every week). "
                f"Type `yes` to confirm."
            )

            def check(m):
                return (
                    m.author == ctx.author
                    and m.channel == ctx.channel
                    and m.content.lower() == "yes"
                )

            try:
                await self.bot.wait_for("message", check=check, timeout=15.0)
            except asyncio.TimeoutError:
                await ctx.send("🚫 Reset cancelled — timed out.")
                return

            async with pool.acquire() as conn:
                deleted = await q.reset_user_all_solves(
                    conn, str(member.id), str(ctx.guild.id)
                )
            desc = f"Wiped **{deleted}** all-time solve record(s) for {member.mention}."

        embed = discord.Embed(
            title="✅ User Solves Reset",
            description=desc,
            color=COLOR_SUCCESS,
        )
        embed.set_footer(text=f"Action by {ctx.author.display_name}")
        await ctx.send(embed=embed)

    # ── !resetproblem ───────────────────────────────────────────────────────

    @commands.command(name="resetproblem")
    @is_admin()
    async def reset_problem(self, ctx, db_id: int = None):
        """
        Un-mark all solves for a specific problem (by its DB ID).
        Members can re-earn points for that problem on next !check.

        Usage: !resetproblem <db_id>
        Find DB IDs with !problems
        """
        if db_id is None:
            await ctx.send(
                "**Usage:** `!resetproblem <db_id>`\n"
                "Find problem DB IDs with `!problems`."
            )
            return

        pool = get_pool()
        async with pool.acquire() as conn:
            prob = await q.get_problem_by_id(conn, db_id)
            if not prob or prob["guild_id"] != str(ctx.guild.id):
                await ctx.send(f"❌ Problem `#{db_id}` not found in this server.")
                return
            deleted = await q.unmark_problem_solves(conn, db_id, str(ctx.guild.id))

        embed = discord.Embed(
            title="✅ Problem Solves Reset",
            description=(
                f"Removed **{deleted}** solve record(s) for "
                f"`{prob['platform'].upper()} {prob['problem_id']}` (DB #{db_id}).\n"
                "Members can re-earn points with `!check`."
            ),
            color=COLOR_SUCCESS,
        )
        embed.set_footer(text=f"Action by {ctx.author.display_name}")
        await ctx.send(embed=embed)

    # ── !resetweekfull ──────────────────────────────────────────────────────

    @commands.command(name="resetweekfull")
    @is_admin()
    async def reset_week_full(self, ctx):
        """
        Full reset: deletes ALL solves + problems for the current week
        and deactivates the week itself. Use !setweek to start fresh.

        Usage: !resetweekfull
        """
        pool = get_pool()
        async with pool.acquire() as conn:
            week = await q.get_active_week(conn, str(ctx.guild.id))

        if not week:
            await ctx.send("❌ No active week. Nothing to reset.")
            return

        embed = discord.Embed(
            title="⚠️ Full Week Reset?",
            description=(
                f"This will:\n"
                f"• Delete **all solves** for **{week['label']}**\n"
                f"• Delete **all problems** in this week\n"
                f"• Deactivate the week (you'll need `!setweek` to start fresh)\n\n"
                f"Type `yes` within 15 seconds to confirm."
            ),
            color=discord.Color.orange(),
        )
        await ctx.send(embed=embed)

        def check(m):
            return (
                m.author == ctx.author
                and m.channel == ctx.channel
                and m.content.lower() == "yes"
            )

        try:
            await self.bot.wait_for("message", check=check, timeout=15.0)
        except asyncio.TimeoutError:
            await ctx.send("🚫 Full reset cancelled — timed out.")
            return

        async with pool.acquire() as conn:
            result = await q.reset_week_and_problems(conn, str(ctx.guild.id))

        embed = discord.Embed(
            title="🗑️ Full Week Reset Complete",
            description=(
                f"Week **#{result['week_id']}** has been wiped and deactivated.\n\n"
                f"• Solves deleted: **{result['solves']}**\n"
                f"• Problems deleted: **{result['problems']}**\n\n"
                "Use `!setweek \"Label\" YYYY-MM-DD YYYY-MM-DD` to start a new week."
            ),
            color=COLOR_WARN,
        )
        embed.set_footer(text=f"Action by {ctx.author.display_name}")
        await ctx.send(embed=embed)

    # ── Error handler ────────────────────────────────────────────────────────

    @reset_week.error
    @reset_all_time.error
    @reset_user.error
    @reset_problem.error
    @reset_week_full.error
    async def reset_error(self, ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.send(
                f"❌ You need the **{ADMIN_ROLE}** role or Administrator permission "
                f"to use reset commands."
            )
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"❌ Missing argument: `{error.param.name}`. "
                           f"Use `!help {ctx.command}` for usage.")
        elif isinstance(error, commands.BadArgument):
            await ctx.send(f"❌ Invalid argument. Use `!help {ctx.command}` for usage.")


async def setup(bot):
    await bot.add_cog(Reset(bot))
