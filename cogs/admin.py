"""
cogs/admin.py
Commands: !setweek, !currentweek, !setpoints, !points
"""

import discord
from discord.ext import commands
from datetime import date
from database.connection import get_pool
from database import queries as q
from config import COLOR_SUCCESS, COLOR_INFO, COLOR_ERROR, COLOR_WARN, ADMIN_ROLE, DEFAULT_DIFFICULTY_POINTS


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

    # ── !setweek ───────────────────────────────────────────────────────────

    @commands.command(name="setweek")
    @is_admin()
    async def set_week(self, ctx, label: str = None, start: str = None, end: str = None):
        """
        Create a new active week.
        !setweek "Week 1" 2024-01-01 2024-01-07
        Deactivates the previous week automatically.
        """
        if not label or not start or not end:
            await ctx.send(
                '**Usage:** `!setweek "<label>" <start_date> <end_date>`\n'
                '**Example:** `!setweek "Week 1" 2024-01-01 2024-01-07`\n'
                'Dates must be in `YYYY-MM-DD` format.'
            )
            return

        try:
            start_d = date.fromisoformat(start)
            end_d   = date.fromisoformat(end)
        except ValueError:
            await ctx.send("❌ Invalid date format. Use `YYYY-MM-DD`.")
            return

        if end_d < start_d:
            await ctx.send("❌ End date must be after start date.")
            return

        pool = get_pool()
        async with pool.acquire() as conn:
            week_id = await q.create_week(conn, str(ctx.guild.id), label, start_d, end_d)

        embed = discord.Embed(title="📅 New Week Created", color=COLOR_SUCCESS)
        embed.add_field(name="Label",   value=label,             inline=True)
        embed.add_field(name="ID",      value=f"#{week_id}",     inline=True)
        embed.add_field(name="Starts",  value=str(start_d),      inline=True)
        embed.add_field(name="Ends",    value=str(end_d),         inline=True)
        embed.set_footer(text=f"Set by {ctx.author.display_name}")
        await ctx.send(embed=embed)

    # ── !currentweek ───────────────────────────────────────────────────────

    @commands.command(name="currentweek", aliases=["current"])
    async def current_week(self, ctx):
        """Show the currently active week."""
        pool = get_pool()
        async with pool.acquire() as conn:
            week = await q.get_active_week(conn, str(ctx.guild.id))

        if not week:
            await ctx.send("❌ No active week. Admins: use `!setweek` to create one.")
            return

        embed = discord.Embed(
            title=f"📅 Current Week: {week['label']}",
            color=COLOR_INFO,
        )
        embed.add_field(name="Start",   value=str(week["start_date"]), inline=True)
        embed.add_field(name="End",     value=str(week["end_date"]),   inline=True)
        embed.add_field(name="Week ID", value=f"#{week['id']}",        inline=True)
        await ctx.send(embed=embed)

    # ── !setpoints ─────────────────────────────────────────────────────────

    @commands.command(name="setpoints")
    @is_admin()
    async def set_points(self, ctx, difficulty: str = None, points: int = None):
        """
        Set how many points a difficulty level is worth.
        !setpoints easy 8
        !setpoints hard 25
        Custom difficulties are allowed: !setpoints legendary 100
        """
        if not difficulty or points is None:
            await ctx.send(
                "**Usage:** `!setpoints <difficulty> <points>`\n"
                "**Example:** `!setpoints easy 8`\n"
                "Built-in difficulties: `easy` · `medium` · `hard` · `expert` · `master`\n"
                "You can also create custom ones: `!setpoints legendary 100`"
            )
            return

        if points < 0:
            await ctx.send("❌ Points must be ≥ 0.")
            return

        pool = get_pool()
        async with pool.acquire() as conn:
            await q.set_difficulty_points(conn, str(ctx.guild.id), difficulty, points)

        await ctx.send(
            f"✅ **{difficulty.capitalize()}** difficulty is now worth **{points} points**."
        )

    # ── !points ────────────────────────────────────────────────────────────

    @commands.command(name="points")
    async def show_points(self, ctx):
        """Show the current difficulty → points mapping."""
        pool = get_pool()
        async with pool.acquire() as conn:
            cfg = await q.get_difficulty_points(conn, str(ctx.guild.id))

        lines = [f"**{d.capitalize()}:** {p} pts" for d, p in sorted(cfg.items())]
        embed = discord.Embed(
            title="⚙️ Difficulty Points Config",
            description="\n".join(lines),
            color=COLOR_INFO,
        )
        embed.set_footer(text="Change with !setpoints <difficulty> <points>")
        await ctx.send(embed=embed)

    # ── Error handler ──────────────────────────────────────────────────────

    @set_week.error
    @set_points.error
    async def admin_error(self, ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.send(f"❌ You need the **{ADMIN_ROLE}** role or Administrator permission.")


async def setup(bot):
    await bot.add_cog(Admin(bot))
