"""
cogs/problems.py  — v3
!problems  → shows TODAY's problems only, with week label + today's date.
             Once the day ends (midnight IST) those problems are gone from the view
             and the next day's problems appear automatically.
!addproblem / !removeproblem / !setdifficulty unchanged.
"""

import asyncio
import discord
from discord.ext import commands
from datetime import date
from database.connection import get_pool
from database import queries as q
import platforms as P
from config import COLOR_SUCCESS, COLOR_ERROR, COLOR_INFO, COLOR_WARN, ADMIN_ROLE

PLATFORM_EMOJIS = {"cf": "🔵", "lc": "🟡", "cc": "🟤", "atcoder": "🔴"}
DIFF_EMOJIS     = {"easy": "🟢", "medium": "🟡", "hard": "🔴", "expert": "🟣", "master": "⚫"}

# Fixed display order for !problems — easy → medium → hard → expert → (master, anything else)
DIFF_ORDER = {"easy": 0, "medium": 1, "hard": 2, "expert": 3, "master": 4}


def _difficulty_sort_key(prob):
    return (DIFF_ORDER.get(prob["difficulty"], 99), prob["id"])


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

    # ── !addproblem ──────────────────────────────────────────────────────────

    @commands.command(name="addproblem")
    @is_admin()
    async def add_problem(self, ctx, platform: str = None, problem_id: str = None,
                          difficulty: str = None, assigned_date: str = None,
                          custom_points: int = None):
        """
        Add a problem to the current week for a specific date.
        !addproblem cf 1234A hard 2026-06-26
        !addproblem lc two-sum easy 2026-06-27 7
        """
        if not platform or not problem_id or not difficulty or not assigned_date:
            embed = discord.Embed(title="📌  Add Problem  —  Usage", color=COLOR_INFO)
            embed.add_field(
                name="Command",
                value="`!addproblem <platform> <problem_id> <difficulty> <YYYY-MM-DD> [points]`",
                inline=False,
            )
            embed.add_field(
                name="Examples",
                value=(
                    "`!addproblem cf 1234A hard 2026-06-26`\n"
                    "`!addproblem lc two-sum easy 2026-06-27 7`\n"
                    "`!addproblem atcoder abc123_a medium 2026-06-28`"
                ),
                inline=False,
            )
            embed.add_field(name="Platforms", value=P.choices_str(), inline=False)
            await ctx.send(embed=embed)
            return

        adapter = P.get(platform)
        if not adapter:
            await ctx.send(f"❌  Unknown platform `{platform}`. Supported: {P.choices_str()}")
            return

        try:
            a_date = date.fromisoformat(assigned_date)
        except ValueError:
            await ctx.send("❌  Date must be `YYYY-MM-DD` — e.g. `2026-06-26`.")
            return

        pool = get_pool()
        async with pool.acquire() as conn:
            week = await q.get_active_week(conn, str(ctx.guild.id))
            if not week:
                await ctx.send("❌  No active week. Create one with `!setweek \"Week 1\" YYYY-MM-DD YYYY-MM-DD`.")
                return

            if not (week["start_date"] <= a_date <= week["end_date"]):
                await ctx.send(
                    f"❌  Date `{a_date}` is outside **{week['label']}** "
                    f"(`{week['start_date']}` → `{week['end_date']}`)."
                )
                return

            if custom_points is not None:
                points = custom_points
            else:
                cfg    = await q.get_difficulty_points(conn, str(ctx.guild.id))
                points = cfg.get(difficulty.lower())
                if points is None:
                    diffs = ", ".join(f"`{d}`" for d in cfg)
                    await ctx.send(f"❌  Unknown difficulty `{difficulty}`. Available: {diffs}")
                    return

            month    = await q.get_active_month(conn, str(ctx.guild.id))
            month_id = month["id"] if month else None

            pid        = adapter.format_problem_id(problem_id)
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

        url    = adapter.problem_url(pid)
        demoji = DIFF_EMOJIS.get(difficulty.lower(), "⚪")
        pemoji = PLATFORM_EMOJIS.get(adapter.KEY, "⚪")

        embed = discord.Embed(
            title=f"{pemoji}  Problem Added  —  {week['label']}",
            color=COLOR_SUCCESS,
        )
        embed.add_field(name="Platform",   value=f"{pemoji}  {adapter.NAME}",              inline=True)
        embed.add_field(name="Problem ID", value=f"`{pid}`",                                inline=True)
        embed.add_field(name="Difficulty", value=f"{demoji}  {difficulty.capitalize()}",    inline=True)
        embed.add_field(name="Points",     value=f"**{points} pts**",                       inline=True)
        embed.add_field(name="Assigned",   value=f"`{a_date}` ({a_date.strftime('%A')})",   inline=True)
        embed.add_field(name="DB ID",      value=f"`#{prob_db_id}`",                        inline=True)
        if url:
            embed.add_field(name="🔗  Link", value=f"[Open Problem]({url})", inline=False)
        embed.set_footer(text=f"Added by {ctx.author.display_name}  ·  Points awarded only on {a_date}")
        await ctx.send(embed=embed)

    # ── !removeproblem ───────────────────────────────────────────────────────

    @commands.command(name="removeproblem")
    @is_admin()
    async def remove_problem(self, ctx, problem_db_id: int = None, keep_history: str = "yes"):
        """
        Remove a problem. Solve history is kept by default.
        !removeproblem 42          — removes problem, keeps past solve records
        !removeproblem 42 no       — removes problem AND deletes solve records
        """
        if problem_db_id is None:
            embed = discord.Embed(title="🗑️  Remove Problem  —  Usage", color=COLOR_INFO)
            embed.add_field(
                name="Commands",
                value=(
                    "`!removeproblem <db_id>`        — remove, keep solve history\n"
                    "`!removeproblem <db_id> no`     — remove AND delete solve records\n\n"
                    "Find `db_id` with `!problems`"
                ),
                inline=False,
            )
            await ctx.send(embed=embed)
            return

        pool = get_pool()
        async with pool.acquire() as conn:
            prob = await q.get_problem_by_id(conn, problem_db_id)
            if not prob or prob["guild_id"] != str(ctx.guild.id):
                await ctx.send(f"❌  Problem `#{problem_db_id}` not found in this server.")
                return

            if keep_history.lower() in ("no", "false", "0"):
                await q.hard_remove_problem(conn, problem_db_id, str(ctx.guild.id))
                note  = "❗ Solve records also deleted — points removed from leaderboards."
                color = COLOR_ERROR
            else:
                await q.remove_problem_keep_solves(conn, problem_db_id, str(ctx.guild.id))
                note  = "✅ Past solve records kept — points preserved in leaderboards."
                color = COLOR_WARN

        pemoji = PLATFORM_EMOJIS.get(prob["platform"], "⚪")
        embed  = discord.Embed(
            title=f"{pemoji}  Problem Removed",
            description=(
                f"**{prob['platform'].upper()}  `{prob['problem_id']}`**  (DB `#{problem_db_id}`)\n\n"
                f"{note}"
            ),
            color=color,
        )
        embed.set_footer(text=f"By {ctx.author.display_name}")
        await ctx.send(embed=embed)

    # ── !problems ────────────────────────────────────────────────────────────

    @commands.command(name="problems")
    async def list_problems(self, ctx):
        """
        Shows ONLY today's problems — nothing else from the week.
        - Title: "Week1  —  Day 6 of 7  —  YYYY-MM-DD"
        - Once midnight IST hits, today's problems disappear from the view
          and tomorrow's appear automatically (handled by get_problems_for_day).
        - No full-week list, no past/future problems shown — just today.
        """
        pool  = get_pool()
        today = q.today_ist()

        async with pool.acquire() as conn:
            week = await q.get_active_week(conn, str(ctx.guild.id))
            if not week:
                await ctx.send("❌  No active week. Admin: `!setweek`")
                return

            # Only today's problems
            todays_probs = await q.get_problems_for_day(conn, str(ctx.guild.id), today)
            # Also get total week problems for context
            all_probs    = await q.get_problems_for_week(conn, str(ctx.guild.id), week["id"])

        # Always display in fixed order: easy → medium → hard → expert (→ master/other)
        todays_probs = sorted(todays_probs, key=_difficulty_sort_key)

        # Work out where today sits in the week
        days_into_week = (today - week["start_date"]).days + 1
        days_in_week   = (week["end_date"] - week["start_date"]).days + 1
        week_progress  = f"Day {days_into_week} of {days_in_week}"

        # Day window times for display (IST)
        day_start_ist = f"{today.strftime('%d %b %Y')}  00:00 IST"
        day_end_ist   = f"{today.strftime('%d %b %Y')}  23:59 IST"

        embed = discord.Embed(
            title=f"📋  {week['label']}  —  {week_progress}  —  {today.isoformat()}",
            description=(
                f"⏰  Points window: `{day_start_ist}` → `{day_end_ist}`\n"
            ),
            color=COLOR_INFO,
        )
        if ctx.guild.icon:
            embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon.url)

        if not todays_probs:
            # Check if week has ANY problems
            if not all_probs:
                embed.add_field(
                    name="No problems this week yet",
                    value=(
                        "Admins can add problems with:\n"
                        "`!addproblem <platform> <id> <difficulty> <YYYY-MM-DD>`"
                    ),
                    inline=False,
                )
            else:
                # Week has problems but none for today
                # Find next day with problems
                future_days = sorted(set(
                    p["assigned_date"] for p in all_probs
                    if p["assigned_date"] > today
                ))
                if future_days:
                    next_day    = future_days[0]
                    next_probs  = [p for p in all_probs if p["assigned_date"] == next_day]
                    embed.add_field(
                        name="📭  No problems assigned for today",
                        value=(
                            f"Rest day! 💤\n\n"
                            f"**Next problems:** `{next_day.strftime('%A, %d %b')}` "
                            f"— {len(next_probs)} problem(s)"
                        ),
                        inline=False,
                    )
                else:
                    embed.add_field(
                        name="📭  No more problems this week",
                        value="All days done! Wait for the next week to be set up.",
                        inline=False,
                    )
        else:
            # Show today's problems
            lines = []
            for i, prob in enumerate(todays_probs, 1):
                adapter = P.get(prob["platform"])
                pemoji  = PLATFORM_EMOJIS.get(prob["platform"], "⚪")
                demoji  = DIFF_EMOJIS.get(prob["difficulty"], "⚪")
                url     = adapter.problem_url(prob["problem_id"]) if adapter else None

                if url:
                    problem_link = f"[**{prob['problem_id']}**]({url})"
                else:
                    problem_link = f"**`{prob['problem_id']}`**"

                lines.append(
                    f"`{i}.`  {pemoji}  {problem_link}\n"
                    f"       {demoji} {prob['difficulty'].capitalize()}  ·  "
                    f"**{prob['points']} pts**  ·  DB `#{prob['id']}`"
                )

            embed.add_field(
                name=f"🎯  {len(todays_probs)} Problem(s) for Today",
                value="\n\n".join(lines),
                inline=False,
            )

        total_today = len(todays_probs)
        embed.set_footer(
            text=(
                f"{total_today} problem(s) today  ·  "
                f"Use !check to verify solves  ·  Points only awarded today"
            )
        )
        await ctx.send(embed=embed)

    # ── !setdifficulty ───────────────────────────────────────────────────────

    @commands.command(name="setdifficulty")
    @is_admin()
    async def set_difficulty(self, ctx, problem_db_id: int = None, difficulty: str = None):
        """Change difficulty (and recalculate points) for a problem. !setdifficulty 42 hard"""
        if problem_db_id is None or difficulty is None:
            await ctx.send(
                "**Usage:** `!setdifficulty <db_id> <difficulty>`\n"
                "**Example:** `!setdifficulty 42 hard`"
            )
            return

        pool = get_pool()
        async with pool.acquire() as conn:
            prob = await q.get_problem_by_id(conn, problem_db_id)
            if not prob or prob["guild_id"] != str(ctx.guild.id):
                await ctx.send(f"❌  Problem `#{problem_db_id}` not found.")
                return

            cfg    = await q.get_difficulty_points(conn, str(ctx.guild.id))
            points = cfg.get(difficulty.lower())
            if points is None:
                await ctx.send(f"❌  Unknown difficulty `{difficulty}`. Use `!setpoints` to add it.")
                return

            await q.set_problem_difficulty(conn, problem_db_id, difficulty, points)

        demoji = DIFF_EMOJIS.get(difficulty.lower(), "⚪")
        embed  = discord.Embed(
            title="✅  Difficulty Updated",
            description=(
                f"Problem `#{problem_db_id}` (`{prob['platform'].upper()} {prob['problem_id']}`)\n"
                f"{demoji}  **{difficulty.capitalize()}**  →  **{points} pts**"
            ),
            color=COLOR_SUCCESS,
        )
        embed.set_footer(text=f"By {ctx.author.display_name}")
        await ctx.send(embed=embed)

    # ── !removeifunsolved (alias: !rius) ────────────────────────────────────

    @commands.command(name="removeifunsolved", aliases=["rius"])
    @is_admin()
    async def remove_if_unsolved(self, ctx, problem_db_id: int = None):
        """
        Remove a problem ONLY if no one has solved it yet.
        Safe alternative to !removeproblem for accidental additions.

        0 solves  → hard deletes immediately, no confirmation needed.
        N solves  → shows who solved it + asks for confirm to force-delete.

        Alias: !rius
        Usage: !rius 42
        """
        if problem_db_id is None:
            await ctx.send(
                "**Usage:** `!removeifunsolved <db_id>`  ·  Alias: `!rius <db_id>`\n"
                "Find DB IDs with `!problems`"
            )
            return

        pool     = get_pool()
        guild_id = str(ctx.guild.id)

        async with pool.acquire() as conn:
            problem = await q.get_problem_by_id(conn, problem_db_id)

        if not problem or str(problem["guild_id"]) != guild_id:
            await ctx.send(
                f"❌  Problem `#{problem_db_id}` not found in this server.\n"
                "Use `!problems` to see today's problem IDs."
            )
            return

        pemoji     = PLATFORM_EMOJIS.get(problem["platform"], "⚪")
        prob_label = (
            f"{pemoji}  `{problem['platform'].upper()} {problem['problem_id']}`"
            + (f"  —  {problem['title']}" if problem.get("title") else "")
        )

        async with pool.acquire() as conn:
            solve_count = await q.get_solve_count_for_problem(conn, problem_db_id, guild_id)

        # ── Case 1: No solves — hard delete immediately ──────────────────
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

        # ── Case 2: Already solved — warn and ask to confirm ─────────────
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
            solver_lines.append(
                f"• **{name}**  (−{row['points_awarded']} pts will be reversed)"
            )

        warn_embed = discord.Embed(
            title="⚠️  Problem Already Solved — Force Remove?",
            description=(
                f"**Problem:** {prob_label}\n"
                f"**Assigned date:** `{problem['assigned_date']}`\n\n"
                f"**{solve_count} member(s) already solved this:**\n"
                + "\n".join(solver_lines)
                + "\n\nDeleting this problem will **remove their solve records and reverse all points**.\n\n"
                "Type `confirm` within 30 s to proceed, or anything else to cancel."
            ),
            color=COLOR_WARN,
        )
        warn_embed.set_footer(text="This action cannot be undone.")
        await ctx.send(embed=warn_embed)

        def check(m):
            return (
                m.author    == ctx.author
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


    @add_problem.error
    @remove_if_unsolved.error
    @set_difficulty.error
    async def admin_error(self, ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.send(f"❌  You need the **{ADMIN_ROLE}** role or Administrator permission.")
        elif isinstance(error, commands.BadArgument):
            await ctx.send("❌  Invalid argument. Check the usage with `!help`.")


async def setup(bot):
    await bot.add_cog(Problems(bot))