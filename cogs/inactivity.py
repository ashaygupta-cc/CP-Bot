"""
cogs/inactivity.py
Inactivity tracker -- monitors member activity via solve history.

Warning schedule (based on last solve date):
  15 days inactive -> DM warning #1
  20 days inactive -> DM warning #2 (stronger)
  25 days inactive -> DM warning #3 + public mention in #general

Note: Auto-kick is disabled. This system only issues warnings.

What counts as "activity"?
  A member is considered active if they have a solve recorded in the DB
  within the last N days. No solve = inactive for that period.

Background task:
  Runs once per day at 09:00 IST. Checks every registered member.

Admin commands:
  !inactivity               -> Show all inactive members with their last solve date.
  !inactivitycheck          -> Manually trigger the inactivity check now (admin).
  !exemptinactivity @user   -> Exempt a member from inactivity warnings.
  !unexemptinactivity @user -> Remove exemption.

Config (add to .env):
  INACTIVITY_CHANNEL = general   (channel name for public 25-day warning)

How to wire into schema:
  No new DB tables needed -- uses existing solves table.
  Exemptions are stored in memory (reset on bot restart).
  For persistent exemptions, run the SQL in SETUP_README.md.
"""

import discord
from discord.ext import commands, tasks
from datetime import datetime, timezone, timedelta
import asyncio

from database.connection import get_pool
from database import queries as q
from config import (
    ADMIN_ROLE, COLOR_ERROR, COLOR_WARN, COLOR_INFO, COLOR_SUCCESS,
    INACTIVITY_CHANNEL,
)

IST = q.IST

# -- Thresholds ----------------------------------------------------------------
WARN_1_DAYS = 15
WARN_2_DAYS = 20
WARN_3_DAYS = 25


def _seconds_until_ist(hour: int, minute: int) -> float:
    now_ist    = datetime.now(IST)
    target_ist = now_ist.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target_ist <= now_ist:
        target_ist += timedelta(days=1)
    return (target_ist - now_ist).total_seconds()


class Inactivity(commands.Cog):
    """Daily inactivity monitoring and automated warnings."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # In-memory exemption set -- persistent version needs DB (see README)
        self._exempted: set[int] = set()
        # Track which (member_id, threshold) combos we've already warned today
        # Format: { member_id: {15, 20, 25} }
        self._warned_today: dict[int, set[int]] = {}
        self._daily_task = None

    def cog_unload(self):
        if self._daily_task:
            self._daily_task.cancel()

    # -- Start daily loop on bot ready -----------------------------------------

    @commands.Cog.listener()
    async def on_ready(self):
        if self._daily_task is None or self._daily_task.done():
            self._daily_task = asyncio.create_task(self._daily_loop())
            print("[inactivity] Daily check task started (09:00 IST).")

    # -- Core: Daily loop ------------------------------------------------------

    async def _daily_loop(self):
        await self.bot.wait_until_ready()

        while not self.bot.is_closed():
            wait = _seconds_until_ist(9, 0)
            print(f"[inactivity] Next check in {wait/3600:.1f} h (09:00 IST)")
            await asyncio.sleep(wait)

            # Reset today's warning tracker at the start of each new day
            self._warned_today.clear()

            today = q.today_ist()
            print(f"[inactivity] Running daily inactivity check for {today}")

            for guild in self.bot.guilds:
                try:
                    await self._check_guild(guild)
                except Exception as e:
                    print(f"[inactivity] Guild {guild.id} error: {e}")

            # Sleep 23h to avoid double-firing
            await asyncio.sleep(23 * 3600)

    # -- Core: Check all members in a guild ------------------------------------

    async def _check_guild(self, guild: discord.Guild):
        pool     = get_pool()
        guild_id = str(guild.id)
        today    = q.today_ist()

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT discord_id FROM handles",
            )

        for row in rows:
            member = guild.get_member(int(row["discord_id"]))
            if not member or member.bot:
                continue
            if member.id in self._exempted:
                continue

            async with pool.acquire() as conn:
                last_solve_row = await conn.fetchrow(
                    """
                    SELECT MAX(s.solved_at) AS last_solve
                    FROM solves s
                    WHERE s.discord_id = $1 AND s.guild_id = $2
                    """,
                    str(member.id), guild_id,
                )

            last_solve = last_solve_row["last_solve"] if last_solve_row else None

            if last_solve is None:
                async with pool.acquire() as conn:
                    user_row = await conn.fetchrow(
                        "SELECT created_at FROM users WHERE discord_id = $1",
                        str(member.id),
                    )
                if not user_row:
                    continue
                last_activity = user_row["created_at"].astimezone(timezone.utc).replace(tzinfo=timezone.utc)
            else:
                last_activity = last_solve.astimezone(timezone.utc).replace(tzinfo=timezone.utc)

            now_utc       = datetime.now(timezone.utc)
            days_inactive = (now_utc - last_activity).days

            await self._handle_inactivity(guild, member, days_inactive, last_activity)
            await asyncio.sleep(0.5)

    # -- Core: Handle a single member's inactivity ----------------------------

    async def _handle_inactivity(
        self,
        guild: discord.Guild,
        member: discord.Member,
        days_inactive: int,
        last_activity: datetime,
    ):
        warned   = self._warned_today.setdefault(member.id, set())
        last_str = f"<t:{int(last_activity.timestamp())}:D>"

        # -- 25 days -> Warning #3 + public mention ----------------------------
        if days_inactive >= WARN_3_DAYS and WARN_3_DAYS not in warned:
            warned.add(WARN_3_DAYS)
            try:
                embed = discord.Embed(
                    title="Inactivity Notice — Final Warning",
                    description=(
                        f"Hi **{member.display_name}**,\n\n"
                        f"Your last recorded solve on **{guild.name}** was {last_str} "
                        f"— **{days_inactive} days ago**.\n\n"
                        "This is your final notice. Please log a solve and run `!check` "
                        "at your earliest convenience to remain in good standing.\n\n"
                        "*If you are taking a planned break, contact an admin to be exempted.*"
                    ),
                    color=COLOR_ERROR,
                )
                embed.set_footer(text=guild.name)
                await member.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                pass

            inact_ch = discord.utils.get(guild.text_channels, name=INACTIVITY_CHANNEL)
            if inact_ch:
                pub_embed = discord.Embed(
                    title="Inactivity Notice",
                    description=(
                        f"{member.mention} has not logged a solve in **{days_inactive} days**.\n"
                        f"Last activity: {last_str}\n\n"
                        "Please submit a solution and run `!check` to update your status."
                    ),
                    color=COLOR_ERROR,
                )
                pub_embed.set_footer(text=guild.name)
                await inact_ch.send(embed=pub_embed)
            return

        # -- 20 days -> Warning #2 ---------------------------------------------
        if days_inactive >= WARN_2_DAYS and WARN_2_DAYS not in warned:
            warned.add(WARN_2_DAYS)
            try:
                embed = discord.Embed(
                    title="Inactivity Notice — Second Warning",
                    description=(
                        f"Hi **{member.display_name}**,\n\n"
                        f"You have not submitted a solve on **{guild.name}** in **{days_inactive} days** "
                        f"(last solve: {last_str}).\n\n"
                        "Please log a solve and run `!check` to stay active. "
                        "A third and final notice will follow if no activity is recorded.\n\n"
                        "*If you are taking a planned break, contact an admin to be exempted.*"
                    ),
                    color=COLOR_WARN,
                )
                embed.set_footer(text=guild.name)
                await member.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                pass
            return

        # -- 15 days -> Warning #1 ---------------------------------------------
        if days_inactive >= WARN_1_DAYS and WARN_1_DAYS not in warned:
            warned.add(WARN_1_DAYS)
            try:
                embed = discord.Embed(
                    title="Inactivity Notice",
                    description=(
                        f"Hi **{member.display_name}**,\n\n"
                        f"We noticed you haven't logged a solve on **{guild.name}** in **{days_inactive} days** "
                        f"(last solve: {last_str}).\n\n"
                        "Submit a solution and run `!check` to keep your activity status current.\n\n"
                        "*If you are taking a planned break, contact an admin to be exempted.*"
                    ),
                    color=COLOR_INFO,
                )
                embed.set_footer(text=guild.name)
                await member.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                pass

    # -- !inactivity -----------------------------------------------------------

    @commands.command(name="inactivity")
    async def inactivity_report(self, ctx):
        """
        Show all inactive members and their last solve date.
        !inactivity
        """
        if not (
            any(r.name == ADMIN_ROLE for r in ctx.author.roles)
            or ctx.author.guild_permissions.administrator
        ):
            await ctx.send(f"You need the **{ADMIN_ROLE}** role.", delete_after=5)
            return

        pool     = get_pool()
        guild_id = str(ctx.guild.id)
        now_utc  = datetime.now(timezone.utc)

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    u.discord_id,
                    u.discord_username,
                    MAX(s.solved_at) AS last_solve
                FROM users u
                LEFT JOIN solves s ON s.discord_id = u.discord_id AND s.guild_id = $1
                GROUP BY u.discord_id, u.discord_username
                ORDER BY last_solve ASC NULLS FIRST
                """,
                guild_id,
            )

        if not rows:
            await ctx.send("No registered members found.")
            return

        lines_15 = []
        lines_20 = []
        lines_25 = []
        lines_ok = []

        for row in rows:
            member = ctx.guild.get_member(int(row["discord_id"]))
            if not member or member.bot:
                continue

            name = member.display_name
            if row["last_solve"] is None:
                days = 999
                last_str = "*Never solved*"
            else:
                last_solve = row["last_solve"].astimezone(timezone.utc)
                days = (now_utc - last_solve).days
                last_str = f"<t:{int(last_solve.timestamp())}:D>"

            tag = f"**{name}** — {last_str} (`{days}d ago`)"

            if days >= WARN_3_DAYS:
                lines_25.append(f"[critical]  {tag}")
            elif days >= WARN_2_DAYS:
                lines_20.append(f"[warning2]  {tag}")
            elif days >= WARN_1_DAYS:
                lines_15.append(f"[warning1]  {tag}")
            else:
                lines_ok.append(f"{name} (`{days}d ago`)")

        embed = discord.Embed(title="Inactivity Report", color=COLOR_WARN)

        if lines_25:
            embed.add_field(
                name=f"Critical (25+ days)",
                value="\n".join(lines_25[:10]) or "None",
                inline=False,
            )
        if lines_20:
            embed.add_field(
                name=f"Warning 2 (20-24 days)",
                value="\n".join(lines_20[:10]) or "None",
                inline=False,
            )
        if lines_15:
            embed.add_field(
                name=f"Warning 1 (15-19 days)",
                value="\n".join(lines_15[:10]) or "None",
                inline=False,
            )

        active_count = len(lines_ok)
        embed.add_field(
            name=f"Active (< 15 days)",
            value=f"*{active_count} member(s) active*",
            inline=False,
        )
        embed.set_footer(
            text=f"Today (IST): {q.today_ist()}"
        )
        await ctx.send(embed=embed)

    # -- !inactivitycheck ------------------------------------------------------

    @commands.command(name="inactivitycheck")
    async def inactivity_check_now(self, ctx):
        """
        (Admin) Manually trigger the inactivity check right now.
        !inactivitycheck
        """
        if not (
            any(r.name == ADMIN_ROLE for r in ctx.author.roles)
            or ctx.author.guild_permissions.administrator
        ):
            await ctx.send(f"You need the **{ADMIN_ROLE}** role.", delete_after=5)
            return

        msg = await ctx.send("Running inactivity check...")
        self._warned_today.clear()
        for guild in self.bot.guilds:
            try:
                await self._check_guild(guild)
            except Exception as e:
                await ctx.send(f"Error for guild `{guild.id}`: `{e}`")

        await msg.edit(content="Inactivity check complete. Warnings sent as applicable.")

    # -- !exemptinactivity -----------------------------------------------------

    @commands.command(name="exemptinactivity", aliases=["exemptinact"])
    async def exempt_inactivity(self, ctx, member: discord.Member = None):
        """
        (Admin) Exempt a member from inactivity warnings.
        !exemptinactivity @user
        """
        if not (
            any(r.name == ADMIN_ROLE for r in ctx.author.roles)
            or ctx.author.guild_permissions.administrator
        ):
            await ctx.send(f"You need the **{ADMIN_ROLE}** role.", delete_after=5)
            return

        if not member:
            await ctx.send("Usage: `!exemptinactivity @user`")
            return

        self._exempted.add(member.id)
        await ctx.send(
            f"**{member.display_name}** is now exempt from inactivity warnings.\n"
            f"*(Note: exemption resets on bot restart — see README for persistent exemptions)*"
        )

    # -- !unexemptinactivity ---------------------------------------------------

    @commands.command(name="unexemptinactivity", aliases=["unexemptinact"])
    async def unexempt_inactivity(self, ctx, member: discord.Member = None):
        """
        (Admin) Remove inactivity exemption for a member.
        !unexemptinactivity @user
        """
        if not (
            any(r.name == ADMIN_ROLE for r in ctx.author.roles)
            or ctx.author.guild_permissions.administrator
        ):
            await ctx.send(f"You need the **{ADMIN_ROLE}** role.", delete_after=5)
            return

        if not member:
            await ctx.send("Usage: `!unexemptinactivity @user`")
            return

        self._exempted.discard(member.id)
        await ctx.send(f"Inactivity exemption removed for **{member.display_name}**.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Inactivity(bot))