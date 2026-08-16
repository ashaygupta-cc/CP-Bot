"""
cogs/leaderboard.py  — v4
!leaderboard  → Daily + Weekly + Monthly, clean professional design.
  - No emoji rank badges, no progress bars
  - Clean numbered list with name, pts, solved count
  - Week label hidden when week is over; shows "Awaiting new week" instead
  - Weekly shows empty state when week is over (same as daily)
"""

import discord
from discord.ext import commands
from database.connection import get_pool
from database import queries as q
from config import COLOR_INFO, COLOR_WARN, COLOR_GOLD, COLOR_PURPLE, COLOR_SUCCESS

MEDAL_ICONS = ["🥇", "🥈", "🥉"]


def _rank_line(rank: int, name: str, pts: int, solved: int) -> str:
    medal = MEDAL_ICONS[rank] if rank < 3 else f"**#{rank + 1}**"
    return f"{medal}  **{name}**  —  {pts} pts  ·  {solved} solved"


def _build_embed(title: str, color: int, rows: list, guild: discord.Guild,
                 footer: str = "", empty_msg: str = "No scores yet — start solving!",
                 max_rows: int = 3) -> discord.Embed:
    embed = discord.Embed(title=title, color=color)

    if not rows:
        embed.description = f"*{empty_msg}*"
        if footer:
            embed.set_footer(text=footer)
        return embed

    lines = [
        _rank_line(
            rank,
            (guild.get_member(int(r["discord_id"])) or type("x", (), {"display_name": "Left server"})()).display_name,
            r["total"],
            r.get("solved_count", 0),
        )
        for rank, r in enumerate(rows[:max_rows])
    ]
    embed.description = "\n".join(lines)
    if len(rows) > max_rows:
        embed.description += f"\n*+{len(rows) - max_rows} more*"
    if footer:
        embed.set_footer(text=footer)
    return embed


class Leaderboard(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    # ── !leaderboard ──────────────────────────────────────────────────────────

    @commands.command(name="leaderboard", aliases=["lb", "rank", "top", "standings"])
    async def leaderboard(self, ctx):
        pool     = get_pool()
        today    = q.today_ist()
        guild_id = str(ctx.guild.id)

        async with pool.acquire() as conn:
            week  = await q.get_active_week(conn, guild_id)
            month = await q.get_active_month(conn, guild_id)

            daily_rows = await q.get_daily_leaderboard(conn, guild_id, today)

            week_over   = week and today > week["end_date"]
            weekly_rows = (
                []
                if not week or week_over
                else await q.get_weekly_leaderboard(conn, guild_id, week["id"])
            )

            monthly_rows = (
                await q.get_monthly_leaderboard(
                    conn, guild_id, month["start_date"], month["end_date"]
                )
                if month else []
            )

        # ── Daily ─────────────────────────────────────────────────────────────
        daily_embed = _build_embed(
            title  = f"Daily  ·  {today.strftime('%d %b %Y')}",
            color  = COLOR_GOLD,
            rows   = daily_rows,
            guild  = ctx.guild,
            footer = f"Resets at midnight IST  ·  {today.strftime('%A')}",
        )

        # ── Weekly ────────────────────────────────────────────────────────────
        if not week:
            weekly_embed = discord.Embed(
                title       = "Weekly",
                description = "*No active week — admin: `!setweek`*",
                color       = COLOR_SUCCESS,
            )
        elif week_over:
            weekly_embed = _build_embed(
                title      = "Weekly  ·  Awaiting new week",
                color      = COLOR_SUCCESS,
                rows       = [],
                guild      = ctx.guild,
                footer     = f"Last week ended {week['end_date']}",
                empty_msg  = "New week coming soon",
            )
        else:
            weekly_embed = _build_embed(
                title  = f"Weekly  ·  {week['label']}",
                color  = COLOR_SUCCESS,
                rows   = weekly_rows,
                guild  = ctx.guild,
                footer = f"Ends {week['end_date']}  ·  {week['start_date']} → {week['end_date']}",
            )

        # ── Monthly ───────────────────────────────────────────────────────────
        if not month:
            monthly_embed = discord.Embed(
                title       = "Monthly",
                description = "*No active month — admin: `!setmonth`*",
                color       = COLOR_PURPLE,
            )
        else:
            monthly_embed = _build_embed(
                title  = f"Monthly  ·  {month['label']}",
                color  = COLOR_PURPLE,
                rows   = monthly_rows,
                guild  = ctx.guild,
                footer = f"Ends {month['end_date']}",
            )

        await ctx.send(embed=daily_embed)
        await ctx.send(embed=weekly_embed)
        await ctx.send(embed=monthly_embed)

    # ── !lbfull (admin) ───────────────────────────────────────────────────────

    @commands.command(name="lbfull")
    @commands.has_permissions(administrator=True)
    async def lbfull(self, ctx, scope: str = "daily"):
        scope    = scope.lower().strip()
        pool     = get_pool()
        today    = q.today_ist()
        guild_id = str(ctx.guild.id)

        if scope not in ("daily", "weekly", "monthly"):
            await ctx.send("❌  Use: `!lbfull daily` · `!lbfull weekly` · `!lbfull monthly`")
            return

        async with pool.acquire() as conn:
            week  = await q.get_active_week(conn, guild_id)
            month = await q.get_active_month(conn, guild_id)

            if scope == "daily":
                rows   = await q.get_daily_leaderboard(conn, guild_id, today)
                title  = f"Daily Leaderboard  ·  {today.strftime('%d %b %Y')}"
                color  = COLOR_GOLD
                footer = f"All users  ·  Resets midnight IST"

            elif scope == "weekly":
                if not week:
                    await ctx.send("❌  No active week.")
                    return
                week_over = today > week["end_date"]
                rows   = [] if week_over else await q.get_weekly_leaderboard(conn, guild_id, week["id"])
                title  = f"Weekly Leaderboard  ·  {'Ended' if week_over else week['label']}"
                color  = COLOR_SUCCESS
                footer = f"All users  ·  {'Week ended' if week_over else 'Week ends'} {week['end_date']}"

            else:
                if not month:
                    await ctx.send("❌  No active month.")
                    return
                rows   = await q.get_monthly_leaderboard(
                    conn, guild_id, month["start_date"], month["end_date"]
                )
                title  = f"Monthly Leaderboard  ·  {month['label']}"
                color  = COLOR_PURPLE
                footer = f"All users  ·  Month ends {month['end_date']}"

        if not rows:
            await ctx.send(embed=discord.Embed(
                title       = title,
                description = "*No scores yet.*",
                color       = color,
            ).set_footer(text=footer))
            return

        PAGE_SIZE = 10
        pages = [rows[i : i + PAGE_SIZE] for i in range(0, len(rows), PAGE_SIZE)]

        for page_num, page in enumerate(pages):
            embed = discord.Embed(
                title = title if page_num == 0 else f"{title}  (cont.)",
                color = color,
            )
            lines = []
            for rank, row in enumerate(page):
                global_rank = page_num * PAGE_SIZE + rank
                member      = ctx.guild.get_member(int(row["discord_id"]))
                name        = member.display_name if member else f"Left server ({row['discord_id']})"
                medal       = MEDAL_ICONS[global_rank] if global_rank < 3 else f"#{global_rank + 1}"
                lines.append(
                    f"{medal}  **{name}**  —  {row['total']} pts  ·  {row.get('solved_count', 0)} solved"
                )
            embed.description = "\n".join(lines)
            if page_num == len(pages) - 1:
                embed.set_footer(text=f"{footer}  ·  {len(rows)} participants")
            await ctx.send(embed=embed)

    @lbfull.error
    async def lbfull_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("🚫  Admins only.")
        else:
            raise error

    @commands.command(name="lbdaily")
    @commands.has_permissions(administrator=True)
    async def lbdaily(self, ctx):
        await ctx.invoke(self.lbfull, scope="daily")

    @commands.command(name="lbweekly")
    @commands.has_permissions(administrator=True)
    async def lbweekly(self, ctx):
        await ctx.invoke(self.lbfull, scope="weekly")

    @commands.command(name="lbmonthly")
    @commands.has_permissions(administrator=True)
    async def lbmonthly(self, ctx):
        await ctx.invoke(self.lbfull, scope="monthly")


async def setup(bot):
    await bot.add_cog(Leaderboard(bot))