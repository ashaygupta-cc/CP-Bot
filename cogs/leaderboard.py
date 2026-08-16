"""
cogs/leaderboard.py
Command: /leaderboard  — shows Daily + Weekly + Monthly in one response
v3: Three fully isolated leaderboards — no cross-contamination between scopes.

Isolation rules:
  Daily   → only solve_date = today
  Weekly  → only week_id    = active_week.id
  Monthly → solve_date BETWEEN month_start AND month_end
            BUT excludes any solve that belongs to the active week
            (weekly rows carry a week_id; those are skipped in monthly aggregation)
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
    for rank, row in enumerate(rows[:3]):
        member = guild.get_member(int(row["discord_id"]))
        name   = member.display_name if member else "*(Left server)*"
        solved = row.get("solved_count", 0)
        lines.append(_rank_line(rank, name, row["total"], solved))

    embed.description = "\n\n".join(lines)
    if len(rows) > 3:
        embed.description += f"\n\n*+{len(rows)-3} more participants*"
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

            # ── Each scope is fully isolated — no overlap ──────────────────
            # Daily   : only solve_date = today
            # Weekly  : only week_id    = active week's ID
            # Monthly : date range BUT active week's solves are excluded
            #           (pass active_week_id so the query filters them out)
            daily_rows   = await q.get_daily_leaderboard(conn, guild_id, today)
            weekly_rows  = await q.get_weekly_leaderboard(conn, guild_id, week["id"]) if week else []
            monthly_rows = (
                await q.get_monthly_leaderboard(
                    conn,
                    guild_id,
                    month["start_date"],
                    month["end_date"],
                    exclude_week_id=week["id"] if week else None,  # ← KEY FIX
                )
                if month else []
            )

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

    # ── Admin: Full leaderboards (all users, paginated) ───────────────────────

    @commands.command(name="lbfull")
    @commands.has_permissions(administrator=True)
    async def lbfull(self, ctx, scope: str = "daily"):
        """
        (Admin) Full leaderboard — ALL users with complete point breakdown.
        !lbfull           → daily (default)
        !lbfull daily
        !lbfull weekly
        !lbfull monthly
        """
        scope    = scope.lower().strip()
        pool     = get_pool()
        today    = q.today_ist()
        guild_id = str(ctx.guild.id)

        if scope not in ("daily", "weekly", "monthly"):
            await ctx.send(
                "❌  Invalid scope. Use: `!lbfull daily` · `!lbfull weekly` · `!lbfull monthly`"
            )
            return

        async with pool.acquire() as conn:
            week  = await q.get_active_week(conn, guild_id)
            month = await q.get_active_month(conn, guild_id)

            if scope == "daily":
                rows   = await q.get_daily_leaderboard(conn, guild_id, today)
                title  = f"☀️  Full Daily Leaderboard  —  {today.strftime('%d %b %Y')}"
                color  = COLOR_GOLD
                footer = "Admin view · All users · Resets midnight IST"

            elif scope == "weekly":
                if not week:
                    await ctx.send("❌  No active week. Use `!setweek` first.")
                    return
                rows   = await q.get_weekly_leaderboard(conn, guild_id, week["id"])
                title  = f"📅  Full Weekly Leaderboard  —  {week['label']}"
                color  = COLOR_SUCCESS
                footer = f"Admin view · All users · Week ends {week['end_date']}"

            else:  # monthly
                if not month:
                    await ctx.send("❌  No active month. Use `!setmonth` first.")
                    return
                rows   = await q.get_monthly_leaderboard(
                    conn,
                    guild_id,
                    month["start_date"],
                    month["end_date"],
                    exclude_week_id=week["id"] if week else None,  # ← KEY FIX
                )
                title  = f"📆  Full Monthly Leaderboard  —  {month['label']}"
                color  = COLOR_PURPLE
                footer = f"Admin view · All users · Month ends {month['end_date']}"

        if not rows:
            await ctx.send(f"📭  No data yet for `{scope}` leaderboard.")
            return

        # Paginate — Discord embed description limit ~4096 chars; 10 users/page is safe
        PAGE_SIZE = 10
        pages     = [rows[i : i + PAGE_SIZE] for i in range(0, len(rows), PAGE_SIZE)]

        for page_num, page in enumerate(pages):
            embed = discord.Embed(
                title=title if page_num == 0 else f"{title}  (cont.)",
                color=color,
            )
            lines = []
            for rank, row in enumerate(page):
                global_rank = page_num * PAGE_SIZE + rank
                member      = ctx.guild.get_member(int(row["discord_id"]))
                name        = member.display_name if member else f"*(Left — {row['discord_id']})*"
                solved      = row.get("solved_count", 0)
                pts         = row["total"]

                badge = RANK_EMO[global_rank] if global_rank < len(RANK_EMO) else f"`#{global_rank + 1:>2}`"
                bar   = "▰" * min(solved, 10)
                lines.append(
                    f"{badge}  **{name}**\n"
                    f"`{bar:<10}`  `{pts} pts`  ·  `{solved} solved`"
                )

            embed.description = "\n\n".join(lines)
            if page_num == len(pages) - 1:
                embed.set_footer(text=f"{footer}  ·  {len(rows)} total participants")
            await ctx.send(embed=embed)

    @lbfull.error
    async def lbfull_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("🚫  This command is for admins only.")
        else:
            raise error

    # ── Shortcut aliases ──────────────────────────────────────────────────────

    @commands.command(name="lbdaily")
    @commands.has_permissions(administrator=True)
    async def lbdaily(self, ctx):
        """(Admin) Shortcut → !lbfull daily"""
        await ctx.invoke(self.lbfull, scope="daily")

    @commands.command(name="lbweekly")
    @commands.has_permissions(administrator=True)
    async def lbweekly(self, ctx):
        """(Admin) Shortcut → !lbfull weekly"""
        await ctx.invoke(self.lbfull, scope="weekly")

    @commands.command(name="lbmonthly")
    @commands.has_permissions(administrator=True)
    async def lbmonthly(self, ctx):
        """(Admin) Shortcut → !lbfull monthly"""
        await ctx.invoke(self.lbfull, scope="monthly")


async def setup(bot):
    await bot.add_cog(Leaderboard(bot))