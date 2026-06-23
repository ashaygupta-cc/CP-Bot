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


class Registry(commands.Cog):
    """Handle registration of CP platform accounts."""

    def __init__(self, bot):
        self.bot = bot

    # ── !register ──────────────────────────────────────────────────────────

    @commands.command(name="register")
    async def register(self, ctx, platform: str = None, handle: str = None):
        """
        Link a competitive programming handle to your Discord account.
        Usage:  !register <platform> <handle>
        Example: !register cf tourist
        Platforms: cf · lc · cc · atcoder
        """
        if not platform or not handle:
            await ctx.send(
                f"**Usage:** `!register <platform> <handle>`\n"
                f"**Platforms:** {P.choices_str()}\n"
                f"**Example:** `!register cf tourist`"
            )
            return

        adapter = P.get(platform)
        if not adapter:
            await ctx.send(
                f"❌ Unknown platform `{platform}`.\n"
                f"Supported: {P.choices_str()}"
            )
            return

        msg = await ctx.send(f"🔍 Verifying `{handle}` on {adapter.NAME}...")
        valid, status = await adapter.verify_handle(handle)

        if not valid:
            await msg.edit(content=status)
            return

        pool = get_pool()
        async with pool.acquire() as conn:
            await q.upsert_user(conn, str(ctx.author.id), ctx.author.name)
            await q.set_handle(conn, str(ctx.author.id), adapter.KEY, handle)

        embed = discord.Embed(title="✅ Handle Registered", color=COLOR_SUCCESS)
        embed.add_field(name="Discord",   value=ctx.author.mention, inline=True)
        embed.add_field(name="Platform",  value=adapter.NAME,       inline=True)
        embed.add_field(name="Handle",    value=f"`{handle}`",       inline=True)
        embed.set_footer(text=status.replace("✅ ", ""))
        await msg.edit(content=None, embed=embed)

    # ── !unregister ────────────────────────────────────────────────────────

    @commands.command(name="unregister")
    async def unregister(self, ctx, platform: str = None):
        """Remove a linked platform handle.  !unregister cf"""
        if not platform or not P.get(platform):
            await ctx.send(f"Usage: `!unregister <platform>`  |  Supported: {P.choices_str()}")
            return

        adapter = P.get(platform)
        pool = get_pool()
        async with pool.acquire() as conn:
            handle = await q.get_handle(conn, str(ctx.author.id), adapter.KEY)
            if not handle:
                await ctx.send(f"❌ You don't have a `{platform}` handle registered.")
                return
            await q.delete_handle(conn, str(ctx.author.id), adapter.KEY)

        await ctx.send(f"✅ Unlinked your {adapter.NAME} handle (`{handle}`).")

    # ── !profile ───────────────────────────────────────────────────────────

    @commands.command(name="profile")
    async def profile(self, ctx, member: discord.Member = None):
        """View linked handles and stats.  !profile [@user]"""
        target = member or ctx.author
        pool   = get_pool()

        async with pool.acquire() as conn:
            handles = await q.get_user_handles(conn, str(target.id))
            solves  = await q.get_user_solves(conn, str(target.id), str(ctx.guild.id))

        embed = discord.Embed(
            title=f"👤 {target.display_name}",
            color=COLOR_INFO,
        )
        embed.set_thumbnail(url=target.display_avatar.url)

        if handles:
            platform_names = P.names()
            handle_lines = "\n".join(
                f"**{platform_names.get(h['platform'], h['platform'])}:** `{h['handle']}`"
                for h in handles
            )
            embed.add_field(name="Linked Accounts", value=handle_lines, inline=False)
        else:
            embed.add_field(name="Linked Accounts", value="None — use `!register`", inline=False)

        total_pts = sum(s["points_awarded"] for s in solves)
        embed.add_field(name="Problems Solved",  value=str(len(solves)), inline=True)
        embed.add_field(name="Total Points",     value=str(total_pts),   inline=True)
        await ctx.send(embed=embed)

    # ── !handles ───────────────────────────────────────────────────────────

    @commands.command(name="handles")
    async def handles(self, ctx, platform: str = None):
        """List all registered members, optionally filtered by platform."""
        pool = get_pool()
        async with pool.acquire() as conn:
            if platform:
                adapter = P.get(platform)
                if not adapter:
                    await ctx.send(f"Unknown platform `{platform}`.")
                    return
                rows = await q.get_all_handles_for_platform(conn, adapter.KEY)
                title = f"Registered {adapter.NAME} Handles"
            else:
                # All platforms
                rows  = []
                title = "All Registered Handles"
                for key in P.keys():
                    for r in await q.get_all_handles_for_platform(conn, key):
                        rows.append({**dict(r), "platform": key})

        if not rows:
            await ctx.send("No handles registered yet.")
            return

        lines = []
        for row in rows:
            member = ctx.guild.get_member(int(row["discord_id"]))
            name   = member.display_name if member else f"<Unknown>"
            plat   = P.names().get(row.get("platform", ""), row.get("platform", ""))
            lines.append(f"**{name}** — `{row['handle']}` ({plat})")

        embed = discord.Embed(
            title=f"📋 {title}",
            description="\n".join(lines[:25]),
            color=COLOR_INFO,
        )
        if len(lines) > 25:
            embed.set_footer(text=f"+{len(lines)-25} more")
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Registry(bot))
