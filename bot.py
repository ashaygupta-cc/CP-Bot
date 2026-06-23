"""
bot.py — Entry point.
Starts the keep-alive HTTP server, connects to Supabase, loads all cogs, runs the bot.
"""

import asyncio
import discord
from discord.ext import commands

import config
from database.connection import init_pool, close_pool
from keep_alive import run_server

# ── Intents ───────────────────────────────────────────────────────────────────
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
]

# ── Events ─────────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    print(f"   Servers: {len(bot.guilds)}")
    print("─" * 40)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing argument: `{error.param.name}`. Use `!help {ctx.command}` for usage.")
        return
    if isinstance(error, commands.BadArgument):
        await ctx.send(f"❌ Invalid argument. Use `!help {ctx.command}` for usage.")
        return
    # Re-raise unexpected errors
    raise error


# ── !help ─────────────────────────────────────────────────────────────────────

@bot.command(name="help")
async def help_cmd(ctx, command_name: str = None):
    embed = discord.Embed(
        title="📋 CP Practice Bot — Commands",
        description="A competitive programming tracker with multi-platform support.",
        color=0x5865F2,
    )
    embed.add_field(
        name="👤 Registration",
        value=(
            "`!register <platform> <handle>` — Link your CP handle (verified)\n"
            "`!unregister <platform>` — Unlink a handle\n"
            "`!profile [@user]` — View handles & points\n"
            "`!handles [platform]` — List all registered members"
        ),
        inline=False,
    )
    embed.add_field(
        name="📅 Problems",
        value=(
            "`!problems` — This week's problems\n"
            "`!addproblem <plat> <id> <diff> [pts]` — Add problem *(admin)*\n"
            "`!removeproblem <id>` — Remove a problem *(admin)*\n"
            "`!setdifficulty <id> <diff>` — Change difficulty *(admin)*"
        ),
        inline=False,
    )
    embed.add_field(
        name="🔍 Checking",
        value=(
            "`!check [@user]` — Check solve status for this week\n"
            "`!checkall` — Bulk-check all members *(admin)*\n"
            "`!submissions <plat> [count] [@user]` — View recent submissions"
        ),
        inline=False,
    )
    embed.add_field(
        name="🏆 Leaderboard",
        value=(
            "`!leaderboard` — This week's rankings\n"
            "`!leaderboard all` — All-time rankings"
        ),
        inline=False,
    )
    embed.add_field(
        name="⚙️ Admin",
        value=(
            "`!setweek \"Label\" YYYY-MM-DD YYYY-MM-DD` — Create a new week\n"
            "`!currentweek` — Show active week\n"
            "`!setpoints <difficulty> <pts>` — Configure points per difficulty\n"
            "`!points` — Show current difficulty → points table"
        ),
        inline=False,
    )
    embed.add_field(
        name="🔄 Reset *(admin only)*",
        value=(
            "`!resetweek` — Clear solves for current week (problems kept)\n"
            "`!resetweekfull` — Delete solves + problems + deactivate week\n"
            "`!resetalltime` — ☢️ Wipe ALL solves ever (nuclear)\n"
            "`!resetuser @user [week|all]` — Reset a member's solves\n"
            "`!resetproblem <db_id>` — Un-mark solves for one problem"
        ),
        inline=False,
    )
    embed.add_field(
        name="🌐 Platforms",
        value="`cf` Codeforces · `lc` LeetCode · `cc` CodeChef · `atcoder` AtCoder",
        inline=False,
    )
    embed.set_footer(text=f"Prefix: {config.PREFIX}  |  More platforms can be added easily.")
    await ctx.send(embed=embed)


# ── Startup ───────────────────────────────────────────────────────────────────

async def main():
    # 1. Start HTTP health server (keeps Render from sleeping)
    asyncio.create_task(run_server(config.PORT))

    # 2. Connect to Supabase PostgreSQL
    print("🔌 Connecting to database...")
    await init_pool()
    print("✅ Database connected.")

    # 3. Load all cogs
    async with bot:
        for cog in COGS:
            await bot.load_extension(cog)
            print(f"   ✓ Loaded {cog}")

        # 4. Run the bot
        await bot.start(config.DISCORD_TOKEN)

    # Cleanup
    await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
