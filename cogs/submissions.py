"""
cogs/submissions.py
Command: /submissions [platform] [count] [@user]
v2: professional embed, uses / prefix
"""

import discord
from discord.ext import commands
from datetime import datetime, timezone
from database.connection import get_pool
from database import queries as q
import platforms as P
from config import COLOR_INFO, COLOR_ERROR

VERDICT_EMOJI = {
    "AC": "✅", "OK": "✅",
    "WA": "❌", "TLE": "⏱️", "MLE": "💾",
    "RE": "💥", "CE": "🔧",
}
PLATFORM_ICONS = {"cf": "🔵", "lc": "🟡", "cc": "🟤", "atcoder": "🔴"}


class Submissions(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="submissions", aliases=["subs", "recent"])
    async def submissions(self, ctx, platform: str = None,
                          count: int = 10, member: discord.Member = None):
        """
        Show recent submissions.
        /submissions cf
        /submissions lc 5 @friend
        """
        target = member or ctx.author

        if not platform:
            embed = discord.Embed(title="📊  Submissions  —  Usage", color=COLOR_INFO)
            embed.add_field(name="Command",   value="`!submissions <platform> [count] [@user]`", inline=False)
            embed.add_field(name="Example",   value="`!submissions cf 10`",                      inline=False)
            embed.add_field(name="Platforms", value=P.choices_str(),                              inline=False)
            await ctx.send(embed=embed)
            return

        adapter = P.get(platform)
        if not adapter:
            await ctx.send(f"❌  Unknown platform `{platform}`.")
            return

        count = max(1, min(count, 20))
        pool  = get_pool()

        async with pool.acquire() as conn:
            handle = await q.get_handle(conn, str(target.id), adapter.KEY)

        if not handle:
            await ctx.send(
                f"❌  {target.display_name} has no **{adapter.NAME}** handle.\n"
                f"Use `!register {platform} <handle>` to link one."
            )
            return

        pemoji = PLATFORM_ICONS.get(adapter.KEY, "⚪")
        msg = await ctx.send(f"🔍  Fetching **{handle}**'s recent {adapter.NAME} submissions…")

        try:
            subs = await adapter.get_recent_submissions(handle, limit=count)
        except RuntimeError as e:
            await msg.edit(content=f"⚠️  {e}")
            return

        if not subs:
            await msg.edit(content=f"📭  No submissions found for `{handle}` on {adapter.NAME}.")
            return

        embed = discord.Embed(
            title=f"{pemoji}  {adapter.NAME}  —  Recent Submissions",
            color=COLOR_INFO,
        )
        embed.set_author(name=f"{target.display_name}  ·  {handle}", icon_url=target.display_avatar.url)

        lines = []
        for sub in subs[:count]:
            emoji  = VERDICT_EMOJI.get(sub.verdict, "❓")
            ts_str = f"<t:{int(sub.timestamp)}:R>" if sub.timestamp else "N/A"
            title  = sub.title or sub.problem_id
            link   = f"[{title}]({sub.url})" if sub.url else title
            lang   = f" · `{sub.language}`" if sub.language else ""
            lines.append(f"{emoji}  {link} · `{sub.verdict}`{lang} · {ts_str}")

        embed.description = "\n".join(lines)
        embed.set_footer(text=f"Showing {len(subs)} submission(s)")
        await msg.edit(content=None, embed=embed)


async def setup(bot):
    await bot.add_cog(Submissions(bot))
