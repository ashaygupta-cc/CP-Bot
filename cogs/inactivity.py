"""
cogs/inactivity.py
Inactivity tracker -- monitors member activity via solve history.

Schedule:
  Runs ONLY on 15th, 20th, 25th, and 30th dates of each month at 09:00 IST.

Target Channel:
  Exclusively posts to Channel ID 1538429554996154388 (inactivity-info).
  Zero pings in #welcome or member DMs.
"""

import discord
from discord.ext import commands
from datetime import datetime, timezone, timedelta
import asyncio

from database.connection import get_pool
from database import queries as q
from config import (
    ADMIN_ROLE, COLOR_ERROR, COLOR_WARN, COLOR_INFO, COLOR_SUCCESS,
    INACTIVITY_CHANNEL_ID, INACTIVITY_CHANNEL,
)

IST = q.IST


def _seconds_until_ist(hour: int, minute: int) -> float:
    now_ist    = datetime.now(IST)
    target_ist = now_ist.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target_ist <= now_ist:
        target_ist += timedelta(days=1)
    return (target_ist - now_ist).total_seconds()


class Inactivity(commands.Cog):
    """Inactivity monitoring and scheduled reporting to #inactivity-info."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._exempted: set[int] = set()
        self._daily_task = None

    def cog_unload(self):
        if self._daily_task:
            self._daily_task.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        if self._daily_task is None or self._daily_task.done():
            self._daily_task = asyncio.create_task(self._daily_loop())
            print("[inactivity] Scheduled inactivity report task started (runs 15/20/25/30 dates at 09:00 IST).")

    async def _daily_loop(self):
        await self.bot.wait_until_ready()

        while not self.bot.is_closed():
            wait = _seconds_until_ist(9, 0)
            print(f"[inactivity] Next check wait: {wait/3600:.1f} h (09:00 IST)")
            await asyncio.sleep(wait)

            today = q.today_ist()
            # Only run on 15th, 20th, 25th, and 30th dates of the month
            if today.day not in (15, 20, 25, 30):
                print(f"[inactivity] Date {today.day} is not 15/20/25/30 — skipping report today.")
                await asyncio.sleep(23 * 3600)
                continue

            print(f"[inactivity] Running scheduled inactivity report for {today} (Date: {today.day})")

            for guild in self.bot.guilds:
                try:
                    await self._report_guild_inactivity(guild)
                except Exception as e:
                    print(f"[inactivity] Guild {guild.id} error: {e}")

            await asyncio.sleep(23 * 3600)

    async def _get_target_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        ch = guild.get_channel(INACTIVITY_CHANNEL_ID)
        if ch:
            return ch
        return discord.utils.get(guild.text_channels, name=INACTIVITY_CHANNEL)

    async def _report_guild_inactivity(self, guild: discord.Guild):
        target_ch = await self._get_target_channel(guild)
        if not target_ch:
            print(f"[inactivity] Target channel {INACTIVITY_CHANNEL_ID} ({INACTIVITY_CHANNEL}) not found in {guild.name}")
            return

        pool     = get_pool()
        guild_id = str(guild.id)
        now_utc  = datetime.now(timezone.utc)

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT u.discord_id, u.discord_username, MAX(s.solved_at) AS last_solve
                FROM users u
                LEFT JOIN solves s ON s.discord_id = u.discord_id AND s.guild_id = $1
                GROUP BY u.discord_id, u.discord_username
                """,
                guild_id,
            )

        inactive_15_29 = []
        inactive_30_49 = []
        inactive_50_plus = []

        for row in rows:
            member = guild.get_member(int(row["discord_id"]))
            if not member or member.bot or member.id in self._exempted:
                continue

            last_solve = row["last_solve"]
            if last_solve is None:
                days_inactive = 999
            else:
                last_utc = last_solve.astimezone(timezone.utc).replace(tzinfo=timezone.utc)
                days_inactive = (now_utc - last_utc).days

            if days_inactive < 15:
                continue

            item = (member, days_inactive)
            if 15 <= days_inactive < 30:
                inactive_15_29.append(item)
            elif 30 <= days_inactive < 50:
                inactive_30_49.append(item)
            else:
                inactive_50_plus.append(item)

        if not (inactive_15_29 or inactive_30_49 or inactive_50_plus):
            embed = discord.Embed(
                title="⚡  Inactivity Info Report",
                description="🎉 All members are active with solves logged in the last 15 days!",
                color=COLOR_SUCCESS,
            )
            embed.set_footer(text=f"{guild.name}  ·  Date: {q.today_ist()}")
            await target_ch.send(embed=embed)
            return

        embed = discord.Embed(
            title="⚠️  Inactivity Report — #inactivity-info",
            description=f"Scheduled inactivity report for **{guild.name}** (15th/20th/25th/30th date schedule).\n\n",
            color=COLOR_WARN,
        )

        if inactive_15_29:
            lines = [f"• {m.mention} — **{d} days inactive**" for m, d in inactive_15_29[:15]]
            embed.add_field(name="🟡  15–29 Days Inactive", value="\n".join(lines), inline=False)

        if inactive_30_49:
            lines = [f"• {m.mention} — **{d} days inactive**" for m, d in inactive_30_49[:15]]
            embed.add_field(name="🟠  30–49 Days Inactive", value="\n".join(lines), inline=False)

        if inactive_50_plus:
            lines = [f"• {m.mention} — **{d if d < 999 else '50+'} days inactive**" for m, d in inactive_50_plus[:15]]
            embed.add_field(name="🔴  50+ Days Inactive (Extended)", value="\n".join(lines), inline=False)

        embed.set_footer(text="Official inactivity tracking  ·  No DM notifications sent")
        await target_ch.send(embed=embed)

    @commands.command(name="inactivity")
    async def inactivity_report(self, ctx):
        """Show all inactive members and post report to #inactivity-info."""
        if not (
            any(r.name == ADMIN_ROLE for r in ctx.author.roles)
            or ctx.author.guild_permissions.administrator
        ):
            await ctx.send(f"You need the **{ADMIN_ROLE}** role.", delete_after=5)
            return

        await self._report_guild_inactivity(ctx.guild)
        await ctx.send("✅  Inactivity report sent to `#inactivity-info` channel.")

    @commands.command(name="inactivitycheck")
    async def inactivity_check_cmd(self, ctx):
        """Manually trigger the inactivity check right now."""
        if not (
            any(r.name == ADMIN_ROLE for r in ctx.author.roles)
            or ctx.author.guild_permissions.administrator
        ):
            await ctx.send(f"You need the **{ADMIN_ROLE}** role.", delete_after=5)
            return

        await self._report_guild_inactivity(ctx.guild)
        await ctx.send("✅  Manual inactivity check completed and posted to `#inactivity-info`.")

    @commands.command(name="exemptinactivity")
    async def exempt_member(self, ctx, member: discord.Member):
        self._exempted.add(member.id)
        await ctx.send(f"✅  **{member.display_name}** is now exempted from inactivity reports.")

    @commands.command(name="unexemptinactivity")
    async def unexempt_member(self, ctx, member: discord.Member):
        self._exempted.discard(member.id)
        await ctx.send(f"✅  **{member.display_name}** exemption removed.")


async def setup(bot):
    await bot.add_cog(Inactivity(bot))