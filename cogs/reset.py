"""
cogs/reset.py
Admin-only reset commands.
v3: Monthly reset is now scope-isolated — it no longer clears weekly/daily.

Rules:
  - !resetdaily    — only clears today's solve records (weekly/monthly untouched)
  - !resetweek     — clears weekly solves (also resets daily since daily is a subset)
  - !resetmonth    — clears ONLY monthly solves outside the active week; weekly/daily preserved
  - !resetalltime  — nuclear wipe of all solves
  - !resetuser     — reset specific user
  - !resetproblem  — un-mark solves for one problem (keep solve history option)
  - !resetweekfull — delete solves + problems + deactivate week
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


async def _confirm(ctx, bot, prompt_embed: discord.Embed, phrase: str = "yes", timeout: float = 20.0) -> bool:
    """Send a confirmation embed and wait for the phrase. Returns True if confirmed."""
    await ctx.send(embed=prompt_embed)

    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel and m.content.lower() == phrase.lower()

    try:
        await bot.wait_for("message", check=check, timeout=timeout)
        return True
    except asyncio.TimeoutError:
        await ctx.send("🚫  Cancelled — confirmation not received in time.")
        return False


class Reset(commands.Cog):
    """Admin-only reset and data-management commands."""

    def __init__(self, bot):
        self.bot = bot

    # ── !resetdaily ─────────────────────────────────────────────────────────

    @commands.command(name="resetdaily")
    @is_admin()
    async def reset_daily(self, ctx):
        """
        Explains that the daily leaderboard resets automatically — no data deleted.
        !resetdaily
        """
        today = q.today_ist()
        embed = discord.Embed(
            title="ℹ️  Daily Leaderboard — Auto Reset",
            description=(
                f"The daily leaderboard **resets automatically every day** — no solves are deleted.\n\n"
                f"It only shows problems assigned on **`{today}`**, so it appears empty "
                f"at the start of each new day and fills up as members solve today's problems.\n\n"
                "**Weekly and Monthly solves are never touched by a daily reset.**\n"
                "Use `!resetweek` or `!resetmonth` to manually clear those scopes when the period ends."
            ),
            color=COLOR_INFO,
        )
        embed.set_footer(text="No data deleted — daily reset is automatic and non-destructive")
        await ctx.send(embed=embed)

    # ── !resetweek ──────────────────────────────────────────────────────────

    @commands.command(name="resetweek")
    @is_admin()
    async def reset_week(self, ctx):
        """
        Reset the current week's leaderboard ONLY.
        Deletes solves that carry the active week_id.
        Daily is a subset of weekly so it resets too.
        Monthly leaderboard is NOT affected — monthly solves have no week_id.
        !resetweek
        """
        pool = get_pool()
        async with pool.acquire() as conn:
            week = await q.get_active_week(conn, str(ctx.guild.id))

        if not week:
            await ctx.send("❌  No active week. Nothing to reset.")
            return

        embed = discord.Embed(
            title="⚠️  Reset Weekly Leaderboard?",
            description=(
                f"Clears **all solve records** for **{week['label']}** "
                f"(`{week['start_date']}` → `{week['end_date']}`).\n\n"
                "**Monthly leaderboard is NOT affected** — monthly solves are stored separately.\n"
                "Problems remain. Members can re-earn points via `!check`.\n\n"
                "Type `yes` to confirm."
            ),
            color=COLOR_WARN,
        )
        if not await _confirm(ctx, self.bot, embed):
            return

        # reset_current_week_solves must delete WHERE week_id = active_week_id
        # It must NOT touch rows that have week_id IS NULL (those are monthly-only solves).
        async with pool.acquire() as conn:
            deleted = await q.reset_current_week_solves(conn, str(ctx.guild.id))

        embed = discord.Embed(
            title="✅  Weekly Leaderboard Reset",
            description=(
                f"Cleared **{deleted}** solve record(s) for **{week['label']}**.\n"
                "Run `!checkall` for members to re-earn points."
            ),
            color=COLOR_SUCCESS,
        )
        embed.set_footer(text=f"By {ctx.author.display_name}  ·  Monthly leaderboard untouched")
        await ctx.send(embed=embed)

    # ── !resetmonth ─────────────────────────────────────────────────────────

    @commands.command(name="resetmonth")
    @is_admin()
    async def reset_month(self, ctx):
        """
        Reset the current month's leaderboard ONLY.
        Solves within the active week's date range are preserved so that
        the weekly and daily leaderboards remain completely untouched.
        !resetmonth
        """
        pool = get_pool()
        async with pool.acquire() as conn:
            month = await q.get_active_month(conn, str(ctx.guild.id))
            week  = await q.get_active_week(conn, str(ctx.guild.id))

        if not month:
            await ctx.send("❌  No active month. Nothing to reset.")
            return

        # Solves that fall inside the active week are left untouched so that
        # the weekly and daily leaderboards remain intact.
        protected_start = week["start_date"] if week else None
        protected_end   = week["end_date"]   if week else None

        week_note = (
            f"Solves within the active week (**{week['label']}**: "
            f"`{week['start_date']}` → `{week['end_date']}`) are **preserved**."
            if week
            else "⚠️  No active week — all monthly solves will be cleared."
        )

        embed = discord.Embed(
            title="⚠️  Reset Monthly Leaderboard?",
            description=(
                f"Clears monthly solve records for **{month['label']}** "
                f"(`{month['start_date']}` → `{month['end_date']}`).\n\n"
                f"{week_note}\n\n"
                "**Weekly and Daily leaderboards are NOT affected.**\n\n"
                "Type `yes` to confirm."
            ),
            color=COLOR_WARN,
        )
        if not await _confirm(ctx, self.bot, embed):
            return

        async with pool.acquire() as conn:
            deleted = await q.reset_month_solves_only(
                conn,
                str(ctx.guild.id),
                month["start_date"],
                month["end_date"],
                protected_start,
                protected_end,
            )

        embed = discord.Embed(
            title="✅  Monthly Leaderboard Reset",
            description=(
                f"Cleared **{deleted}** solve record(s) for **{month['label']}**.\n"
                "Weekly and daily leaderboards were not affected."
            ),
            color=COLOR_SUCCESS,
        )
        embed.set_footer(text=f"By {ctx.author.display_name}  ·  Weekly/Daily untouched")
        await ctx.send(embed=embed)

    # ── !resetalltime ────────────────────────────────────────────────────────

    @commands.command(name="resetalltime")
    @is_admin()
    async def reset_all_time(self, ctx):
        """
        NUCLEAR: Permanently delete ALL solves ever for this server.
        !resetalltime
        """
        phrase = f"CONFIRM WIPE {ctx.author.name}"
        embed = discord.Embed(
            title="☢️  NUCLEAR — Wipe All-Time Scores",
            description=(
                "This permanently deletes **every solve record** for every member.\n\n"
                "Weeks, months and problems are **kept** but all scores go to zero.\n\n"
                f"Type exactly within 30s:\n```\n{phrase}\n```"
            ),
            color=discord.Color.red(),
        )
        await ctx.send(embed=embed)

        def check(m):
            return (m.author == ctx.author and m.channel == ctx.channel
                    and m.content == phrase)
        try:
            await self.bot.wait_for("message", check=check, timeout=30.0)
        except asyncio.TimeoutError:
            await ctx.send("🚫  Nuclear reset cancelled.")
            return

        pool = get_pool()
        async with pool.acquire() as conn:
            deleted = await q.reset_all_solves(conn, str(ctx.guild.id))

        embed = discord.Embed(
            title="☢️  All-Time Scores Wiped",
            description=f"Deleted **{deleted}** solve record(s) across all time.",
            color=discord.Color.red(),
        )
        embed.set_footer(text=f"By {ctx.author.display_name}")
        await ctx.send(embed=embed)

    # ── !resetuser ───────────────────────────────────────────────────────────

    @commands.command(name="resetuser")
    @is_admin()
    async def reset_user(self, ctx, member: discord.Member = None, scope: str = "week"):
        """
        Reset a specific member's solves.
        !resetuser @user week    — this week's solves
        !resetuser @user all     — all-time solves
        """
        if not member:
            embed = discord.Embed(title="🔄  Reset User  —  Usage", color=COLOR_INFO)
            embed.add_field(name="Commands", value=(
                "`!resetuser @user week`  — clear this week's solves\n"
                "`!resetuser @user all`   — clear all-time solves"
            ), inline=False)
            await ctx.send(embed=embed)
            return

        scope = scope.lower()
        if scope not in ("week", "all"):
            await ctx.send("❌  Invalid scope. Use `week` or `all`.")
            return

        pool = get_pool()

        if scope == "week":
            async with pool.acquire() as conn:
                week = await q.get_active_week(conn, str(ctx.guild.id))
                if not week:
                    await ctx.send("❌  No active week.")
                    return
                deleted = await q.reset_user_week_solves(conn, str(member.id), str(ctx.guild.id))
            embed = discord.Embed(
                title="✅  User Week Solves Reset",
                description=f"Cleared **{deleted}** record(s) for {member.mention} in **{week['label']}**.",
                color=COLOR_SUCCESS,
            )
        else:
            confirm_embed = discord.Embed(
                title=f"⚠️  Reset ALL solves for {member.display_name}?",
                description="This wipes every solve across all weeks. Type `yes` to confirm.",
                color=COLOR_WARN,
            )
            if not await _confirm(ctx, self.bot, confirm_embed):
                return
            async with pool.acquire() as conn:
                deleted = await q.reset_user_all_solves(conn, str(member.id), str(ctx.guild.id))
            embed = discord.Embed(
                title="✅  User All-Time Solves Reset",
                description=f"Wiped **{deleted}** record(s) for {member.mention}.",
                color=COLOR_SUCCESS,
            )

        embed.set_footer(text=f"By {ctx.author.display_name}")
        await ctx.send(embed=embed)

    # ── !resetproblem ────────────────────────────────────────────────────────

    @commands.command(name="resetproblem")
    @is_admin()
    async def reset_problem(self, ctx, db_id: int = None):
        """
        Un-mark all solves for a specific problem.
        Members can re-earn points on next !check.
        !resetproblem 42
        """
        if db_id is None:
            await ctx.send("**Usage:** `!resetproblem <db_id>`  ·  Find IDs with `!problems`")
            return

        pool = get_pool()
        async with pool.acquire() as conn:
            prob = await q.get_problem_by_id(conn, db_id)
            if not prob or prob["guild_id"] != str(ctx.guild.id):
                await ctx.send(f"❌  Problem `#{db_id}` not found.")
                return
            deleted = await q.unmark_problem_solves(conn, db_id, str(ctx.guild.id))

        embed = discord.Embed(
            title="✅  Problem Solves Un-marked",
            description=(
                f"Removed **{deleted}** solve record(s) for "
                f"`{prob['platform'].upper()} {prob['problem_id']}` (#{db_id}).\n"
                "Members can re-earn points with `!check`."
            ),
            color=COLOR_SUCCESS,
        )
        embed.set_footer(text=f"By {ctx.author.display_name}")
        await ctx.send(embed=embed)

    # ── !resetweekfull ───────────────────────────────────────────────────────

    @commands.command(name="resetweekfull")
    @is_admin()
    async def reset_week_full(self, ctx):
        """
        Full reset: delete solves + problems + deactivate the current week.
        !resetweekfull
        """
        pool = get_pool()
        async with pool.acquire() as conn:
            week = await q.get_active_week(conn, str(ctx.guild.id))

        if not week:
            await ctx.send("❌  No active week.")
            return

        embed = discord.Embed(
            title="⚠️  Full Week Reset?",
            description=(
                f"This will:\n"
                f"• Delete **all solves** for **{week['label']}**\n"
                f"• Delete **all problems** in this week\n"
                f"• Deactivate the week\n\n"
                "Type `yes` to confirm."
            ),
            color=discord.Color.orange(),
        )
        if not await _confirm(ctx, self.bot, embed):
            return

        async with pool.acquire() as conn:
            result = await q.reset_week_and_problems(conn, str(ctx.guild.id))

        embed = discord.Embed(
            title="🗑️  Full Week Reset Complete",
            description=(
                f"Week **#{result['week_id']}** deactivated.\n\n"
                f"• Solves deleted:   **{result['solves']}**\n"
                f"• Problems deleted: **{result['problems']}**\n\n"
                "Use `!setweek` to start a new week."
            ),
            color=COLOR_WARN,
        )
        embed.set_footer(text=f"By {ctx.author.display_name}")
        await ctx.send(embed=embed)

    # ── !saferemove ──────────────────────────────────────────────────────────

    @commands.command(name="saferemove")
    @is_admin()
    async def safe_remove(self, ctx, problem_db_id: int = None):
        """
        Remove a mistakenly added problem — safe version.

        • If 0 solves  → deletes immediately, no confirmation needed.
        • If N solves  → shows who solved it, asks you to type 'confirm' to
                         force-delete (removes the problem AND all its solves
                         so points are reversed).

        Usage:
          !saferemove 42
        """
        if problem_db_id is None:
            await ctx.send(
                "**Usage:** `!saferemove <problem_db_id>`\n"
                "Example: `!saferemove 42`  ·  Find IDs with `!problems`"
            )
            return

        pool     = get_pool()
        guild_id = str(ctx.guild.id)

        async with pool.acquire() as conn:
            problem = await q.get_problem_by_id(conn, problem_db_id)

        # ── Does this problem exist in this guild? ──────────────────────────
        if not problem or str(problem["guild_id"]) != guild_id:
            await ctx.send(
                f"❌  Problem `#{problem_db_id}` not found in this server.\n"
                "Use `!problems` to see the current week's problem IDs."
            )
            return

        prob_label = (
            f"`{problem['platform'].upper()} {problem['problem_id']}`"
            + (f"  —  {problem['title']}" if problem.get("title") else "")
        )

        async with pool.acquire() as conn:
            solve_count = await q.get_solve_count_for_problem(conn, problem_db_id, guild_id)

        # ── Case 1: No solves — delete straight away ────────────────────────
        if solve_count == 0:
            async with pool.acquire() as conn:
                await q.hard_remove_problem(conn, problem_db_id, guild_id)

            embed = discord.Embed(
                title="🗑️  Problem Removed",
                description=(
                    f"{prob_label}\n\n"
                    "✅  Deleted cleanly — **no solves were affected** "
                    "(nobody had solved this problem yet)."
                ),
                color=COLOR_SUCCESS,
            )
            embed.set_footer(text=f"Removed by {ctx.author.display_name}")
            await ctx.send(embed=embed)
            return

        # ── Case 2: Someone already solved it — warn and confirm ────────────
        async with pool.acquire() as conn:
            solvers = await conn.fetch(
                """
                SELECT s.discord_id, s.points_awarded
                FROM solves s
                WHERE s.problem_db_id = $1 AND s.guild_id = $2
                ORDER BY s.solved_at
                """,
                problem_db_id, guild_id,
            )

        solver_lines = []
        for row in solvers:
            member = ctx.guild.get_member(int(row["discord_id"]))
            name   = member.display_name if member else f"*(Left — {row['discord_id']})*"
            solver_lines.append(f"• **{name}**  (−{row['points_awarded']} pts reversed)")

        warn_embed = discord.Embed(
            title="⚠️  Problem Already Solved — Force Remove?",
            description=(
                f"**Problem:** {prob_label}\n"
                f"**Assigned date:** `{problem['assigned_date']}`\n\n"
                f"**{solve_count} member(s) already solved this:**\n"
                + "\n".join(solver_lines)
                + "\n\n"
                "Deleting this problem will **remove their solve records and reverse all points**.\n\n"
                "Type `confirm` within 30 s to proceed, or anything else to cancel."
            ),
            color=COLOR_WARN,
        )
        warn_embed.set_footer(text="This action cannot be undone.")
        await ctx.send(embed=warn_embed)

        def check(m):
            return (
                m.author  == ctx.author
                and m.channel == ctx.channel
                and m.content.lower() in ("confirm", "cancel", "no")
            )

        try:
            reply = await self.bot.wait_for("message", check=check, timeout=30.0)
        except asyncio.TimeoutError:
            await ctx.send("🚫  Timed out — problem was **not** removed.")
            return

        if reply.content.lower() != "confirm":
            await ctx.send("🚫  Cancelled — problem was **not** removed.")
            return

        # ── Confirmed: hard delete (CASCADE removes solves too) ─────────────
        async with pool.acquire() as conn:
            await q.hard_remove_problem(conn, problem_db_id, guild_id)

        embed = discord.Embed(
            title="🗑️  Problem Force-Removed",
            description=(
                f"{prob_label}\n\n"
                f"Deleted along with **{solve_count}** solve record(s).\n"
                "Points awarded for this problem have been reversed."
            ),
            color=COLOR_SUCCESS,
        )
        embed.set_footer(text=f"Force-removed by {ctx.author.display_name}")
        await ctx.send(embed=embed)

    @reset_daily.error
    @reset_week.error
    @reset_month.error
    @reset_all_time.error
    @reset_user.error
    @reset_problem.error
    @reset_week_full.error
    @safe_remove.error
    async def reset_error(self, ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.send(f"❌  You need the **{ADMIN_ROLE}** role or Administrator permission.")
        elif isinstance(error, (commands.MissingRequiredArgument, commands.BadArgument)):
            await ctx.send(f"❌  Invalid usage. Use `!help reset` for details.")


async def setup(bot):
    await bot.add_cog(Reset(bot))