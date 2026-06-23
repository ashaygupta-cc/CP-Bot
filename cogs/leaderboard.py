"""
cogs/leaderboard.py
Commands: !leaderboard [all]
"""

import discord
from discord.ext import commands
from database.connection import get_pool
from database import queries as q
from config import COLOR_INFO, COLOR_WARN

MEDALS = {0: "🥇", 1: "🥈", 2: "🥉"}


async def _build_embed(rows: list, guild: discord.Guild, title: str) -> discord.Embed:
    if not rows:
        return discord.Embed(title=title, description="No scores yet!", color=COLOR_INFO)

    lines = []
    for rank, row in enumerate(rows[:20]):
        member = guild.get_member(int(row["discord_id"]))
        name   = member.display_name if member else f"<Left server>"
        medal  = MEDALS.get(rank, f"`#{rank + 1:>2}`")
        lines.append(f"{medal} **{name}** — {row['total']} pts")

    embed = discord.Embed(title=title, description="\n".join(lines), color=COLOR_WARN)
    embed.set_footer(text=f"{len(rows)} participants")
    return embed


class Leaderboard(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="leaderboard", aliases=["lb", "rank", "top"])
    async def leaderboard(self, ctx, mode: str = "weekly"):
        """
        Show the leaderboard.
        !leaderboard          — current week
        !leaderboard all      — all-time
        """
        pool = get_pool()
        async with pool.acquire() as conn:
            if mode.lower() in ("all", "alltime", "total", "overall"):
                rows  = await q.get_alltime_leaderboard(conn, str(ctx.guild.id))
                title = "🏆 All-Time Leaderboard"
            else:
                week = await q.get_active_week(conn, str(ctx.guild.id))
                if not week:
                    await ctx.send("❌ No active week. Admin: `!setweek`")
                    return
                rows  = await q.get_weekly_leaderboard(conn, str(ctx.guild.id), week["id"])
                title = f"📅 Leaderboard — {week['label']}"

        embed = await _build_embed(rows, ctx.guild, title)
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Leaderboard(bot))
