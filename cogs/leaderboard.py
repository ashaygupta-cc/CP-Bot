"""
cogs/leaderboard.py
Command: /leaderboard  — shows Daily + Weekly + Monthly in one response
v2: Three separate leaderboards, medal system, professional layout
"""

import discord
from discord.ext import commands
from database.connection import get_pool
from database import queries as q
from config import COLOR_INFO, COLOR_WARN, COLOR_GOLD, COLOR_PURPLE, COLOR_SUCCESS

MEDALS   = {0: "🥇", 1: "🥈", 2: "🥉"}
RANK_EMO = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]


def _rank_line(rank: int, name: str, total: int, solved: int) -> str:
    badge = RANK_EMO[rank] if rank < len(RANK_EMO) else f"`#{rank+1:>2}`"
    bar   = "▰" * min(solved, 10)
    return f"{badge}  **{name}**\n`{bar}`  `{total} pts`  ·  `{solved} solved`"


async def _build_board(rows: list, guild: discord.Guild, title: str, color: int,
                       footer: str = "") -> discord.Embed:
    embed = discord.Embed(title=title, color=color)

    if not rows:
        embed.description = "*No scores yet — start solving!*"
        embed.set_footer(text=footer)
        return embed

    lines = []
    for rank, row in enumerate(rows[:10]):
        member = guild.get_member(int(row["discord_id"]))
        name   = member.display_name if member else "*(Left server)*"
        solved = row.get("solved_count", 0)
        lines.append(_rank_line(rank, name, row["total"], solved))

    embed.description = "\n\n".join(lines)
    if len(rows) > 10:
        embed.description += f"\n\n*+{len(rows)-10} more participants*"
    embed.set_footer(text=footer)
    return embed


class Leaderboard(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="leaderboard", aliases=["lb", "rank", "top", "standings"])
    async def leaderboard(self, ctx):
        """
        Shows Daily, Weekly and Monthly leaderboards in one go.
        /leaderboard
        """
        pool  = get_pool()
        today = q.today_ist()
        guild_id = str(ctx.guild.id)

        async with pool.acquire() as conn:
            week  = await q.get_active_week(conn, guild_id)
            month = await q.get_active_month(conn, guild_id)

            daily_rows   = await q.get_daily_leaderboard(conn, guild_id, today)
            weekly_rows  = await q.get_weekly_leaderboard(conn, guild_id, week["id"]) if week else []
            monthly_rows = await q.get_monthly_leaderboard(conn, guild_id, month["id"]) if month else []

        # ── Daily ──────────────────────────────────────────────────────────
        daily_embed = await _build_board(
            daily_rows, ctx.guild,
            title  = f"☀️  Daily Leaderboard  —  {today.strftime('%d %b %Y')}",
            color  = COLOR_GOLD,
            footer = "Resets at midnight IST  ·  Only today's problems count",
        )

        # ── Weekly ─────────────────────────────────────────────────────────
        if week:
            weekly_embed = await _build_board(
                weekly_rows, ctx.guild,
                title  = f"📅  Weekly Leaderboard  —  {week['label']}",
                color  = COLOR_SUCCESS,
                footer = f"Week ends {week['end_date']}  ·  Resets at midnight IST on end date",
            )
        else:
            weekly_embed = discord.Embed(
                title="📅  Weekly Leaderboard",
                description="*No active week. Admin: `!setweek`*",
                color=COLOR_WARN,
            )

        # ── Monthly ────────────────────────────────────────────────────────
        if month:
            monthly_embed = await _build_board(
                monthly_rows, ctx.guild,
                title  = f"📆  Monthly Leaderboard  —  {month['label']}",
                color  = COLOR_PURPLE,
                footer = f"Month ends {month['end_date']}  ·  Set month with /setmonth",
            )
        else:
            monthly_embed = discord.Embed(
                title="📆  Monthly Leaderboard",
                description="*No active month. Admin: `!setmonth`*",
                color=COLOR_PURPLE,
            )

        await ctx.send(embed=daily_embed)
        await ctx.send(embed=weekly_embed)
        await ctx.send(embed=monthly_embed)


async def setup(bot):
    await bot.add_cog(Leaderboard(bot))
