"""
cogs/points.py  (NEW)
Manual point management: /addpoints, /subpoints, /setuserpointsmanual, /pointlog
Admin-only. Points stored in point_adjustments table.
"""

import discord
from discord.ext import commands
from database.connection import get_pool
from database import queries as q
from config import COLOR_SUCCESS, COLOR_ERROR, COLOR_WARN, COLOR_INFO, ADMIN_ROLE


def is_admin():
    async def predicate(ctx):
        return (
            any(r.name == ADMIN_ROLE for r in ctx.author.roles)
            or ctx.author.guild_permissions.administrator
        )
    return commands.check(predicate)


class Points(commands.Cog):
    """Manual point adjustment commands (admin only)."""

    def __init__(self, bot):
        self.bot = bot

    # ── /addpoints ──────────────────────────────────────────────────────────

    @commands.command(name="addpoints")
    @is_admin()
    async def add_points(self, ctx, member: discord.Member = None,
                         amount: int = None, *, reason: str = "Manual adjustment"):
        """
        Add points to a member manually.
        /addpoints @user 10 Bonus for participation
        """
        if not member or amount is None:
            embed = discord.Embed(title="➕  Add Points  —  Usage", color=COLOR_INFO)
            embed.add_field(name="Command", value="`!addpoints @user <amount> [reason]`", inline=False)
            embed.add_field(name="Example", value="`!addpoints @Alice 10 Bonus for participation`", inline=False)
            await ctx.send(embed=embed)
            return

        if amount <= 0:
            await ctx.send("❌  Amount must be a positive integer.")
            return

        pool = get_pool()
        async with pool.acquire() as conn:
            await q.upsert_user(conn, str(member.id), member.name)
            await q.add_point_adjustment(
                conn,
                guild_id    = str(ctx.guild.id),
                discord_id  = str(member.id),
                delta       = amount,
                reason      = reason,
                adjusted_by = str(ctx.author.id),
            )
            total_adj = await q.get_user_adjustment_total(conn, str(ctx.guild.id), str(member.id))

        embed = discord.Embed(
            title="➕  Points Added",
            color=COLOR_SUCCESS,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Member",     value=member.mention,     inline=True)
        embed.add_field(name="Added",      value=f"**+{amount} pts**", inline=True)
        embed.add_field(name="Adj. Total", value=f"`{total_adj} pts`",  inline=True)
        embed.add_field(name="Reason",     value=reason,             inline=False)
        embed.set_footer(text=f"By {ctx.author.display_name}")
        await ctx.send(embed=embed)

    # ── /subpoints ──────────────────────────────────────────────────────────

    @commands.command(name="subpoints")
    @is_admin()
    async def sub_points(self, ctx, member: discord.Member = None,
                         amount: int = None, *, reason: str = "Manual deduction"):
        """
        Subtract points from a member.
        /subpoints @user 5 Late submission penalty
        """
        if not member or amount is None:
            embed = discord.Embed(title="➖  Subtract Points  —  Usage", color=COLOR_INFO)
            embed.add_field(name="Command", value="`!subpoints @user <amount> [reason]`", inline=False)
            embed.add_field(name="Example", value="`!subpoints @Alice 5 Late submission`", inline=False)
            await ctx.send(embed=embed)
            return

        if amount <= 0:
            await ctx.send("❌  Amount must be a positive integer.")
            return

        pool = get_pool()
        async with pool.acquire() as conn:
            await q.upsert_user(conn, str(member.id), member.name)
            await q.add_point_adjustment(
                conn,
                guild_id    = str(ctx.guild.id),
                discord_id  = str(member.id),
                delta       = -amount,
                reason      = reason,
                adjusted_by = str(ctx.author.id),
            )
            total_adj = await q.get_user_adjustment_total(conn, str(ctx.guild.id), str(member.id))

        embed = discord.Embed(
            title="➖  Points Deducted",
            color=COLOR_WARN,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Member",     value=member.mention,       inline=True)
        embed.add_field(name="Deducted",   value=f"**−{amount} pts**", inline=True)
        embed.add_field(name="Adj. Total", value=f"`{total_adj} pts`",  inline=True)
        embed.add_field(name="Reason",     value=reason,               inline=False)
        embed.set_footer(text=f"By {ctx.author.display_name}")
        await ctx.send(embed=embed)

    # ── /setpoints ──────────────────────────────────────────────────────────
    # Note: /setpoints in admin.py sets difficulty→points config.
    # This is /setmemberpoints to set a member's adjustment total to an exact value.

    @commands.command(name="setmemberpoints")
    @is_admin()
    async def set_member_points(self, ctx, member: discord.Member = None,
                                target: int = None, *, reason: str = "Manual override"):
        """
        Force a member's adjustment total to an exact value.
        Calculates the delta needed from current total.
        /setmemberpoints @user 50 Override for contest
        """
        if not member or target is None:
            embed = discord.Embed(title="🎯  Set Member Points  —  Usage", color=COLOR_INFO)
            embed.add_field(
                name="Command",
                value="`!setmemberpoints @user <target_adjustment_total> [reason]`",
                inline=False,
            )
            embed.add_field(
                name="Note",
                value="This sets the *adjustment* total (manual bonus/penalty pool), not solve points.",
                inline=False,
            )
            await ctx.send(embed=embed)
            return

        pool = get_pool()
        async with pool.acquire() as conn:
            await q.upsert_user(conn, str(member.id), member.name)
            current = await q.get_user_adjustment_total(conn, str(ctx.guild.id), str(member.id))
            delta   = target - current
            if delta == 0:
                await ctx.send(f"ℹ️  {member.mention} already has an adjustment total of `{current} pts`. No change.")
                return
            await q.add_point_adjustment(
                conn,
                guild_id    = str(ctx.guild.id),
                discord_id  = str(member.id),
                delta       = delta,
                reason      = f"[Set override] {reason}",
                adjusted_by = str(ctx.author.id),
            )

        embed = discord.Embed(
            title="🎯  Points Override Applied",
            color=COLOR_SUCCESS,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Member",    value=member.mention,                  inline=True)
        embed.add_field(name="Previous",  value=f"`{current} pts`",              inline=True)
        embed.add_field(name="Now Set To",value=f"**`{target} pts`**",           inline=True)
        embed.add_field(name="Delta",     value=f"`{delta:+d} pts`",             inline=True)
        embed.add_field(name="Reason",    value=reason,                          inline=False)
        embed.set_footer(text=f"By {ctx.author.display_name}")
        await ctx.send(embed=embed)

    # ── /pointlog ───────────────────────────────────────────────────────────

    @commands.command(name="pointlog")
    async def point_log(self, ctx, member: discord.Member = None):
        """
        Show recent manual point adjustments for a member.
        /pointlog @user
        """
        target = member or ctx.author
        pool   = get_pool()

        async with pool.acquire() as conn:
            adjustments = await q.get_user_adjustments(conn, str(ctx.guild.id), str(target.id))
            total_adj   = await q.get_user_adjustment_total(conn, str(ctx.guild.id), str(target.id))

        embed = discord.Embed(
            title=f"📋  Point Log  —  {target.display_name}",
            color=COLOR_INFO,
        )
        embed.set_thumbnail(url=target.display_avatar.url)

        if not adjustments:
            embed.description = "*No manual adjustments recorded.*"
        else:
            lines = []
            for adj in adjustments:
                sign  = "➕" if adj["delta"] > 0 else "➖"
                admin = ctx.guild.get_member(int(adj["adjusted_by"]))
                aname = admin.display_name if admin else "Admin"
                ts    = f"<t:{int(adj['created_at'].timestamp())}:d>"
                lines.append(
                    f"{sign}  **{adj['delta']:+d} pts**  ·  {ts}\n"
                    f"> {adj['reason'] or 'No reason given'}  ·  *by {aname}*"
                )
            embed.description = "\n\n".join(lines)

        embed.set_footer(text=f"Adjustment total: {total_adj:+d} pts  ·  Last 10 shown")
        await ctx.send(embed=embed)

    @add_points.error
    @sub_points.error
    @set_member_points.error
    async def admin_error(self, ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.send(f"❌  You need the **{ADMIN_ROLE}** role or Administrator permission.")
        elif isinstance(error, commands.BadArgument):
            await ctx.send("❌  Invalid argument. Mention a valid member and enter a whole number.")


async def setup(bot):
    await bot.add_cog(Points(bot))
