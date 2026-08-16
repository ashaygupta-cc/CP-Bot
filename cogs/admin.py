"""
cogs/admin.py
Commands: /setweek, /setmonth, /currentweek, /setpoints, /points
v2: Added /setmonth, professional embeds
"""

import discord
from discord.ext import commands
from datetime import date
from database.connection import get_pool
from database import queries as q
from config import (COLOR_SUCCESS, COLOR_INFO, COLOR_ERROR, COLOR_WARN,
                    COLOR_PURPLE, ADMIN_ROLE, DEFAULT_DIFFICULTY_POINTS)


def is_admin():
    async def predicate(ctx):
        return (
            any(r.name == ADMIN_ROLE for r in ctx.author.roles)
            or ctx.author.guild_permissions.administrator
        )
    return commands.check(predicate)


class Admin(commands.Cog):
    """Admin-only server configuration commands."""

    def __init__(self, bot):
        self.bot = bot

    # ── /setweek ────────────────────────────────────────────────────────────

    @commands.command(name="setweek")
    @is_admin()
    async def set_week(self, ctx, label: str = None, start: str = None, end: str = None):
        """
        Create a new active week (deactivates previous).
        /setweek "Week 1" 2026-06-23 2026-06-29
        """
        if not label or not start or not end:
            embed = discord.Embed(title="📅  Set Week  —  Usage", color=COLOR_INFO)
            embed.add_field(
                name="Command",
                value='`!setweek "<label>" <start> <end>`',
                inline=False,
            )
            embed.add_field(
                name="Example",
                value='`!setweek "Week 1" 2026-06-23 2026-06-29`',
                inline=False,
            )
            embed.set_footer(text="Dates: YYYY-MM-DD  ·  Previous week auto-deactivated")
            await ctx.send(embed=embed)
            return

        try:
            start_d = date.fromisoformat(start)
            end_d   = date.fromisoformat(end)
        except ValueError:
            await ctx.send("❌  Invalid date format. Use `YYYY-MM-DD`.")
            return

        if end_d < start_d:
            await ctx.send("❌  End date must be after start date.")
            return

        pool = get_pool()
        async with pool.acquire() as conn:
            week_id = await q.create_week(conn, str(ctx.guild.id), label, start_d, end_d)

        embed = discord.Embed(
            title="✅  New Week Activated",
            color=COLOR_SUCCESS,
        )
        embed.add_field(name="Label",   value=f"**{label}**",     inline=True)
        embed.add_field(name="Week ID", value=f"`#{week_id}`",    inline=True)
        embed.add_field(name="‎",        value="‎",                 inline=True)
        embed.add_field(name="Starts",  value=f"`{start_d}`",     inline=True)
        embed.add_field(name="Ends",    value=f"`{end_d}`",       inline=True)
        embed.add_field(name="‎",        value="‎",                 inline=True)
        embed.set_footer(text=f"Set by {ctx.author.display_name}  ·  Add problems with /addproblem")
        await ctx.send(embed=embed)

    # ── /setmonth ───────────────────────────────────────────────────────────

    @commands.command(name="setmonth")
    @is_admin()
    async def set_month(self, ctx, label: str = None, start: str = None, end: str = None):
        """
        Create a new active month for monthly leaderboard tracking.
        /setmonth "June 2026" 2026-06-01 2026-06-30
        """
        if not label or not start or not end:
            embed = discord.Embed(title="📆  Set Month  —  Usage", color=COLOR_INFO)
            embed.add_field(
                name="Command",
                value='`!setmonth "<label>" <start> <end>`',
                inline=False,
            )
            embed.add_field(
                name="Example",
                value='`!setmonth "June 2026" 2026-06-01 2026-06-30`',
                inline=False,
            )
            await ctx.send(embed=embed)
            return

        try:
            start_d = date.fromisoformat(start)
            end_d   = date.fromisoformat(end)
        except ValueError:
            await ctx.send("❌  Invalid date format. Use `YYYY-MM-DD`.")
            return

        if end_d < start_d:
            await ctx.send("❌  End date must be after start date.")
            return

        pool = get_pool()
        async with pool.acquire() as conn:
            month_id = await q.create_month(conn, str(ctx.guild.id), label, start_d, end_d)

        embed = discord.Embed(
            title="✅  New Month Activated",
            color=COLOR_PURPLE,
        )
        embed.add_field(name="Label",    value=f"**{label}**",    inline=True)
        embed.add_field(name="Month ID", value=f"`#{month_id}`",  inline=True)
        embed.add_field(name="‎",         value="‎",                inline=True)
        embed.add_field(name="Starts",   value=f"`{start_d}`",    inline=True)
        embed.add_field(name="Ends",     value=f"`{end_d}`",      inline=True)
        embed.add_field(name="‎",         value="‎",                inline=True)
        embed.set_footer(text=f"Set by {ctx.author.display_name}  ·  Problems auto-linked when added via /addproblem")
        await ctx.send(embed=embed)

    # ── /currentweek ────────────────────────────────────────────────────────

    @commands.command(name="currentweek", aliases=["week"])
    async def current_week(self, ctx):
        """Show the currently active week and month."""
        pool = get_pool()
        async with pool.acquire() as conn:
            week  = await q.get_active_week(conn, str(ctx.guild.id))
            month = await q.get_active_month(conn, str(ctx.guild.id))

        if not week and not month:
            await ctx.send("❌  No active week or month. Admins: `!setweek` and `!setmonth`")
            return

        embed = discord.Embed(title="📅  Active Periods", color=COLOR_INFO)

        if week:
            today = q.today_ist()
            days_left = (week["end_date"] - today).days
            status = f"Ends in **{days_left} day(s)**" if days_left >= 0 else "⚠️ Ended"
            embed.add_field(
                name="📅  Current Week",
                value=(
                    f"**{week['label']}**  `#{week['id']}`\n"
                    f"`{week['start_date']}` → `{week['end_date']}`\n"
                    f"{status}"
                ),
                inline=False,
            )
        else:
            embed.add_field(name="📅  Week", value="No active week", inline=False)

        if month:
            embed.add_field(
                name="📆  Current Month",
                value=(
                    f"**{month['label']}**  `#{month['id']}`\n"
                    f"`{month['start_date']}` → `{month['end_date']}`"
                ),
                inline=False,
            )

        embed.set_footer(text=f"Today (IST): {q.today_ist()}")
        await ctx.send(embed=embed)

    # ── /setpoints ──────────────────────────────────────────────────────────

    @commands.command(name="setpoints")
    @is_admin()
    async def set_points(self, ctx, difficulty: str = None, points: int = None):
        """
        Set how many points a difficulty level is worth.
        /setpoints easy 8
        """
        if not difficulty or points is None:
            embed = discord.Embed(title="⚙️  Set Points  —  Usage", color=COLOR_INFO)
            embed.add_field(name="Command", value="`!setpoints <difficulty> <points>`", inline=False)
            embed.add_field(name="Example", value="`!setpoints hard 25`", inline=False)
            embed.add_field(
                name="Built-in Difficulties",
                value="`easy` · `medium` · `hard` · `expert` · `master`\nCustom allowed: `!setpoints legendary 100`",
                inline=False,
            )
            await ctx.send(embed=embed)
            return

        if points < 0:
            await ctx.send("❌  Points must be ≥ 0.")
            return

        pool = get_pool()
        async with pool.acquire() as conn:
            await q.set_difficulty_points(conn, str(ctx.guild.id), difficulty, points)

        await ctx.send(
            f"✅  **{difficulty.capitalize()}** is now worth **{points} pts**."
        )

    # ── /points ─────────────────────────────────────────────────────────────

    @commands.command(name="points")
    async def show_points(self, ctx):
        """Show the current difficulty → points mapping."""
        pool = get_pool()
        async with pool.acquire() as conn:
            cfg = await q.get_difficulty_points(conn, str(ctx.guild.id))

        DIFF_EMOJI = {"easy": "🟢", "medium": "🟡", "hard": "🔴", "expert": "🟣", "master": "⚫"}
        lines = [
            f"{DIFF_EMOJI.get(d, '⚪')}  **{d.capitalize()}** — `{p} pts`"
            for d, p in sorted(cfg.items())
        ]
        embed = discord.Embed(
            title="⚙️  Difficulty Points Table",
            description="\n".join(lines),
            color=COLOR_INFO,
        )
        embed.set_footer(text="Change with /setpoints <difficulty> <points>")
        await ctx.send(embed=embed)

    @set_week.error
    @set_month.error
    @set_points.error
    async def admin_error(self, ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.send(f"❌  You need the **{ADMIN_ROLE}** role or Administrator permission.")


async def setup(bot):
    await bot.add_cog(Admin(bot))
