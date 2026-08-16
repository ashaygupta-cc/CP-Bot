"""
cogs/registry.py
Commands: !register, !unregister, !profile, !handles
"""

import discord
from discord.ext import commands
from database.connection import get_pool
from database import queries as q
import platforms as P
from config import COLOR_SUCCESS, COLOR_ERROR, COLOR_INFO, ADMIN_ROLE

PLATFORM_ICONS = {"cf": "🔵", "lc": "🟡", "cc": "🟤", "atcoder": "🔴"}


class Registry(commands.Cog):
    """Handle registration of CP platform accounts."""

    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="register")
    async def register(self, ctx, platform: str = None, handle: str = None):
        """
        Link your CP handle.
        !register cf tourist
        """
        if not platform or not handle:
            embed = discord.Embed(title="📝  Register Handle  —  Usage", color=COLOR_INFO)
            embed.add_field(name="Command",   value="`!register <platform> <handle>`", inline=False)
            embed.add_field(name="Example",   value="`!register cf tourist`",          inline=False)
            embed.add_field(name="Platforms", value=P.choices_str(),                   inline=False)
            await ctx.send(embed=embed)
            return

        adapter = P.get(platform)
        if not adapter:
            await ctx.send(f"❌  Unknown platform `{platform}`. Supported: {P.choices_str()}")
            return

        msg = await ctx.send(f"🔍  Verifying `{handle}` on **{adapter.NAME}**…")
        valid, status = await adapter.verify_handle(handle)

        if not valid:
            await msg.edit(content=status)
            return

        pool = get_pool()
        async with pool.acquire() as conn:
            await q.upsert_user(conn, str(ctx.author.id), ctx.author.name)
            await q.set_handle(conn, str(ctx.author.id), adapter.KEY, handle)

        pemoji = PLATFORM_ICONS.get(adapter.KEY, "⚪")
        embed = discord.Embed(title="✅  Handle Registered", color=COLOR_SUCCESS)
        embed.set_thumbnail(url=ctx.author.display_avatar.url)
        embed.add_field(name="Member",   value=ctx.author.mention, inline=True)
        embed.add_field(name="Platform", value=f"{pemoji}  {adapter.NAME}", inline=True)
        embed.add_field(name="Handle",   value=f"`{handle}`",        inline=True)
        embed.set_footer(text=status.replace("✅ ", ""))
        await msg.edit(content=None, embed=embed)

    @commands.command(name="unregister")
    async def unregister(self, ctx, platform: str = None):
        """!unregister cf"""
        if not platform or not P.get(platform):
            await ctx.send(f"Usage: `!unregister <platform>`  ·  Supported: {P.choices_str()}")
            return

        adapter = P.get(platform)
        pool    = get_pool()
        async with pool.acquire() as conn:
            handle = await q.get_handle(conn, str(ctx.author.id), adapter.KEY)
            if not handle:
                await ctx.send(f"❌  No `{platform}` handle registered.")
                return
            await q.delete_handle(conn, str(ctx.author.id), adapter.KEY)

        pemoji = PLATFORM_ICONS.get(adapter.KEY, "⚪")
        await ctx.send(f"✅  Unlinked {pemoji} **{adapter.NAME}** handle `{handle}`.")

    @commands.command(name="profile")
    async def profile(self, ctx, member: discord.Member = None):
        """!profile [@user]"""
        target = member or ctx.author
        pool   = get_pool()

        async with pool.acquire() as conn:
            handles     = await q.get_user_handles(conn, str(target.id))
            solves      = await q.get_user_solves(conn, str(target.id), str(ctx.guild.id))
            adj_total   = await q.get_user_adjustment_total(conn, str(ctx.guild.id), str(target.id))

        solve_pts   = sum(s["points_awarded"] for s in solves)
        total_pts   = solve_pts + adj_total
        today       = q.today_ist()
        today_pts   = sum(
            s["points_awarded"] for s in solves
            if hasattr(s["assigned_date"], "year") and s["assigned_date"] == today
        )

        embed = discord.Embed(
            title=f"👤  {target.display_name}",
            color=COLOR_INFO,
        )
        embed.set_thumbnail(url=target.display_avatar.url)

        # Linked handles
        if handles:
            handle_lines = "\n".join(
                f"{PLATFORM_ICONS.get(h['platform'], '⚪')}  **{P.names().get(h['platform'], h['platform'])}:** `{h['handle']}`"
                for h in handles
            )
        else:
            handle_lines = "*None — use `!register`*"
        embed.add_field(name="🔗  Linked Accounts", value=handle_lines, inline=False)

        # Stats
        embed.add_field(name="🏅  Total Points",  value=f"**{total_pts}**",       inline=True)
        embed.add_field(name="✅  Solved",         value=f"**{len(solves)}**",     inline=True)
        embed.add_field(name="☀️  Today",          value=f"**{today_pts} pts**",   inline=True)

        if adj_total != 0:
            embed.add_field(
                name="🎛️  Manual Adjustments",
                value=f"`{adj_total:+d} pts`",
                inline=False,
            )

        embed.set_footer(text=f"Solve pts: {solve_pts}  ·  Adjustments: {adj_total:+d}")
        await ctx.send(embed=embed)

    @commands.command(name="handles")
    async def handles(self, ctx, platform: str = None):
        """!handles [platform]"""
        pool = get_pool()
        async with pool.acquire() as conn:
            if platform:
                adapter = P.get(platform)
                if not adapter:
                    await ctx.send(f"Unknown platform `{platform}`.")
                    return
                rows  = await q.get_all_handles_for_platform(conn, adapter.KEY)
                title = f"{PLATFORM_ICONS.get(adapter.KEY, '⚪')}  {adapter.NAME} Handles"
            else:
                rows, title = [], "📋  All Registered Handles"
                for key in P.keys():
                    for r in await q.get_all_handles_for_platform(conn, key):
                        rows.append({**dict(r), "platform": key})

        if not rows:
            await ctx.send("No handles registered yet.")
            return

        lines = []
        for row in rows:
            member = ctx.guild.get_member(int(row["discord_id"]))
            name   = f"**{member.display_name}**" if member else "*Left server*"
            pemoji = PLATFORM_ICONS.get(row.get("platform", ""), "⚪")
            pname  = P.names().get(row.get("platform", ""), "")
            lines.append(f"{pemoji}  {name} — `{row['handle']}`  *({pname})*")

        embed = discord.Embed(title=title, description="\n".join(lines[:25]), color=COLOR_INFO)
        if len(lines) > 25:
            embed.set_footer(text=f"+{len(lines)-25} more  ·  {len(lines)} total")
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Registry(bot))
