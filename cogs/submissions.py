"""
cogs/submissions.py
Command: !submissions [platform] [count] [@user]
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


class Submissions(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="submissions", aliases=["subs", "recent"])
    async def submissions(
        self,
        ctx,
        platform: str = None,
        count: int = 10,
        member: discord.Member = None,
    ):
        """
        Show recent submissions from a CP platform.
        !submissions cf            — your last 10 CF submissions
        !submissions lc 5          — your last 5 LC submissions
        !submissions cf 10 @friend — someone else's CF submissions
        """
        target = member or ctx.author

        if not platform:
            await ctx.send(
                f"**Usage:** `!submissions <platform> [count] [@user]`\n"
                f"**Example:** `!submissions cf 10`\n"
                f"**Platforms:** {P.choices_str()}"
            )
            return

        adapter = P.get(platform)
        if not adapter:
            await ctx.send(f"❌ Unknown platform `{platform}`. Supported: {P.choices_str()}")
            return

        count = max(1, min(count, 20))   # clamp between 1 and 20

        pool = get_pool()
        async with pool.acquire() as conn:
            handle = await q.get_handle(conn, str(target.id), adapter.KEY)

        if not handle:
            await ctx.send(
                f"❌ {target.display_name} hasn't registered a {adapter.NAME} handle.\n"
                f"Use `!register {platform} <handle>` to link one."
            )
            return

        msg = await ctx.send(f"🔍 Fetching `{handle}`'s recent {adapter.NAME} submissions...")

        try:
            subs = await adapter.get_recent_submissions(handle, limit=count)
        except RuntimeError as e:
            await msg.edit(content=f"⚠️ {e}")
            return

        if not subs:
            await msg.edit(content=f"📭 No recent submissions found for `{handle}` on {adapter.NAME}.")
            return

        embed = discord.Embed(
            title=f"📊 {adapter.NAME} — Recent Submissions for `{handle}`",
            color=COLOR_INFO,
        )
        embed.set_thumbnail(url=target.display_avatar.url)

        lines = []
        for sub in subs[:count]:
            emoji   = VERDICT_EMOJI.get(sub.verdict, "❓")
            dt      = datetime.fromtimestamp(sub.timestamp, tz=timezone.utc)
            ts_str  = f"<t:{int(sub.timestamp)}:R>" if sub.timestamp else "N/A"
            title   = sub.title or sub.problem_id
            link    = f"[{title}]({sub.url})" if sub.url else title
            lang    = f" · {sub.language}" if sub.language else ""
            lines.append(f"{emoji} {link} · `{sub.verdict}`{lang} · {ts_str}")

        embed.description = "\n".join(lines)
        embed.set_footer(text=f"Showing {len(subs)} submissions")
        await msg.edit(content=None, embed=embed)


async def setup(bot):
    await bot.add_cog(Submissions(bot))
