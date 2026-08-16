"""
bot.py — Entry point (v2)
Prefix changed to /  ·  Cogs: registry, admin, problems, checker,
leaderboard, submissions, reset, points
"""

import asyncio
import discord
from discord.ext import commands

import config
from database.connection import init_pool, close_pool
from keep_alive import run_server

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix=config.PREFIX, intents=intents, help_command=None)

COGS = [
    "cogs.registry",
    "cogs.admin",
    "cogs.problems",
    "cogs.checker",
    "cogs.leaderboard",
    "cogs.submissions",
    "cogs.reset",
    "cogs.points",
]


@bot.event
async def on_ready():
    print(f"✅  Logged in as {bot.user} (ID: {bot.user.id})")
    print(f"    Prefix : {config.PREFIX}")
    print(f"    Servers: {len(bot.guilds)}")
    print("─" * 40)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌  Missing argument: `{error.param.name}`. Use `!help` for usage.")
        return
    if isinstance(error, commands.BadArgument):
        await ctx.send(f"❌  Invalid argument. Use `!help` for usage.")
        return
    raise error


# ── /help ────────────────────────────────────────────────────────────────────

@bot.command(name="help")
async def help_cmd(ctx, section: str = None):
    """Show all commands."""

    embed = discord.Embed(
        title="📖  CP Practice Bot  —  Command Reference",
        description=(
            "*A competitive programming tracker with multi-platform support.*\n"
            f"Prefix: `{config.PREFIX}`"
        ),
        color=0x5865F2,
    )
    embed.set_thumbnail(url=bot.user.display_avatar.url)

    embed.add_field(
        name="👤  Registration",
        value=(
            "`!register <platform> <handle>` — Link your CP handle\n"
            "`!unregister <platform>` — Unlink a handle\n"
            "`!profile [@user]` — View handles & point stats\n"
            "`!handles [platform]` — List all registered members"
        ),
        inline=False,
    )
    embed.add_field(
        name="📅  Problems",
        value=(
            "`!problems` — This week's schedule (grouped by day)\n"
            "`!addproblem <plat> <id> <diff> <date> [pts]` — Add problem *(admin)*\n"
            "`!removeproblem <id> [keep_history]` — Remove problem *(admin)*\n"
            "`!setdifficulty <id> <diff>` — Change difficulty *(admin)*"
        ),
        inline=False,
    )
    embed.add_field(
        name="🔍  Solve Checking",
        value=(
            "`!check [@user]` — Check today's problems\n"
            "`!checkall` — Bulk-check all members *(admin)*\n"
            "`!submissions <plat> [count] [@user]` — Recent submissions"
        ),
        inline=False,
    )
    embed.add_field(
        name="🏆  Leaderboards",
        value=(
            "`!leaderboard` — Daily + Weekly + Monthly in one shot\n"
            "*Daily resets midnight IST · Weekly resets on week end-date · Monthly on month end-date*"
        ),
        inline=False,
    )
    embed.add_field(
        name="⚙️  Admin — Config",
        value=(
            "`!setweek \"Label\" YYYY-MM-DD YYYY-MM-DD` — Create/activate a week\n"
            "`!setmonth \"Label\" YYYY-MM-DD YYYY-MM-DD` — Create/activate a month\n"
            "`!currentweek` — Show active week & month\n"
            "`!setpoints <difficulty> <pts>` — Configure points per difficulty\n"
            "`!points` — Show difficulty → points table"
        ),
        inline=False,
    )
    embed.add_field(
        name="🎛️  Admin — Manual Points",
        value=(
            "`!addpoints @user <n> [reason]` — Add bonus points\n"
            "`!subpoints @user <n> [reason]` — Deduct points\n"
            "`!setmemberpoints @user <n> [reason]` — Force adjustment total\n"
            "`!pointlog [@user]` — View recent adjustments"
        ),
        inline=False,
    )
    embed.add_field(
        name="🔄  Admin — Reset",
        value=(
            "`!resetdaily` — Clear today's daily leaderboard only\n"
            "`!resetweek` — Clear weekly (daily is a subset, also cleared)\n"
            "`!resetmonth` — Clear monthly (daily + weekly also cleared)\n"
            "`!resetalltime` — ☢️ Wipe everything\n"
            "`!resetuser @user [week|all]` — Reset one member\n"
            "`!resetproblem <db_id>` — Un-mark a problem's solves\n"
            "`!resetweekfull` — Delete solves + problems + deactivate week"
        ),
        inline=False,
    )
    embed.add_field(
        name="🌐  Platforms",
        value="`cf` Codeforces · `lc` LeetCode · `cc` CodeChef · `atcoder` AtCoder",
        inline=False,
    )
    embed.set_footer(text="Points only awarded for problems solved on their assigned day (midnight–midnight IST)")
    await ctx.send(embed=embed)


# ── Startup ───────────────────────────────────────────────────────────────────

async def main():
    asyncio.create_task(run_server(config.PORT))

    print("🔌  Connecting to database…")
    await init_pool()
    print("✅  Database connected.")

    async with bot:
        for cog in COGS:
            await bot.load_extension(cog)
            print(f"   ✓  Loaded {cog}")
        await bot.start(config.DISCORD_TOKEN)

    await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
