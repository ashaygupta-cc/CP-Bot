"""
bot.py — Entry point (v4)
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

    # ── Header ────────────────────────────────────────────────────────────────
    header = discord.Embed(
        title="",
        description=(
            "```ansi\n"
            "\u001b[1;33m ██████╗██████╗\u001b[0m\n"
            "\u001b[1;33m██╔════╝██╔══██╗\u001b[0m\n"
            "\u001b[1;32m██║     ██████╔╝\u001b[0m\n"
            "\u001b[1;32m██║     ██╔═══╝\u001b[0m\n"
            "\u001b[1;36m╚██████╗██║\u001b[0m\n"
            "\u001b[1;36m ╚═════╝╚═╝\u001b[0m\n"
            "\n"
            "\u001b[1;33m ██████╗  ██████╗ ████████╗\u001b[0m\n"
            "\u001b[1;33m██╔══██╗██╔═══██╗╚══██╔══╝\u001b[0m\n"
            "\u001b[1;32m██████╔╝██║   ██║   ██║   \u001b[0m\n"
            "\u001b[1;32m██╔══██╗██║   ██║   ██║   \u001b[0m\n"
            "\u001b[1;36m██████╔╝╚██████╔╝   ██║   \u001b[0m\n"
            "\u001b[1;36m╚═════╝  ╚═════╝    ╚═╝   \u001b[0m\n"
            "```\n"
            "> 🏆  Competitive Programming Practice Tracker\n"
            "> Multi-platform · Daily/Weekly/Monthly leaderboards\n"
            f"\n"
            f"```\n"
            f"  Prefix    {config.PREFIX}\n"
            f"  Platforms cf  ·  lc  ·  cc  ·  atcoder\n"
            f"```"
        ),
        color=0x5865F2,
    )
    header.set_author(name="CP Practice Bot  —  Command Reference",
                      icon_url=bot.user.display_avatar.url)
    header.set_thumbnail(url=bot.user.display_avatar.url)

    # ── Registration ──────────────────────────────────────────────────────────
    reg = discord.Embed(title="👤  Registration", color=0x7289DA)
    reg.add_field(name="\u200b", value=(
        "```\n"
        "!register <platform> <handle>   Link your handle\n"
        "!unregister <platform>          Remove a handle\n"
        "!profile [@user]                View handles + points\n"
        "!handles [platform]             List all members\n"
        "```"
    ), inline=False)

    # ── Problems ──────────────────────────────────────────────────────────────
    problems = discord.Embed(title="📅  Problems", color=0x57F287)
    problems.add_field(name="🙋  Member", value=(
        "```\n"
        "!problems    This week's schedule, grouped by day\n"
        "```"
    ), inline=False)
    problems.add_field(name="🔒  Admin only", value=(
        "```\n"
        "!addproblem <plat> <id> <diff> <date> [pts]\n"
        "!rius <id>           Safely remove problem (blocked if already solved)\n"
        "!removeproblem <id> [keep_history]\n"
        "!setdifficulty <id> <diff>\n"
        "```"
    ), inline=False)

    # ── Solve Checking ────────────────────────────────────────────────────────
    checking = discord.Embed(title="🔍  Solve Checking", color=0xFEE75C)
    checking.add_field(name="🙋  Member", value=(
        "```\n"
        "!check [@user]                  Check today's solve status\n"
        "!submissions <plat> [n] [@user] Browse recent submissions\n"
        "```"
    ), inline=False)
    checking.add_field(name="🔒  Admin only", value=(
        "```\n"
        "!checkall    Bulk-check all members at once\n"
        "```"
    ), inline=False)
    checking.add_field(name="⏱️  Auto-check schedule", value=(
        "```\n"
        "23:45 IST   Auto-checkall (slow/safe mode)\n"
        "Every 6 h   Silent background point award\n"
        "```\n"
        "> 💡 **Tip:** `!check` is instant and preferred — auto is just a safety net."
    ), inline=False)

    # ── Leaderboards ──────────────────────────────────────────────────────────
    lb = discord.Embed(title="🏆  Leaderboards", color=0xF1C40F)
    lb.add_field(name="\u200b", value=(
        "```\n"
        "!leaderboard           Daily + Weekly + Monthly in one view\n"
        "!lbfull [scope]        Admin: full list, all users, paginated\n"
        "!lbdaily               Admin shortcut → !lbfull daily\n"
        "!lbweekly              Admin shortcut → !lbfull weekly\n"
        "!lbmonthly             Admin shortcut → !lbfull monthly\n"
        "```"
    ), inline=False)
    lb.add_field(name="🔄  Reset schedule", value=(
        "```\n"
        "Daily    midnight IST (auto)\n"
        "Weekly   active week's end-date  (manual: !resetweek)\n"
        "Monthly  active month's end-date (manual: !resetmonth)\n"
        "```"
    ), inline=False)

    # ── Admin Config ──────────────────────────────────────────────────────────
    admin_cfg = discord.Embed(title="⚙️  Admin — Configuration", color=0xEB459E)
    admin_cfg.add_field(name="📆  Periods", value=(
        "```\n"
        '!setweek  "Label" YYYY-MM-DD YYYY-MM-DD   Create/activate week\n'
        '!setmonth "Label" YYYY-MM-DD YYYY-MM-DD   Create/activate month\n'
        "!currentweek                               Show active week & month\n"
        "```"
    ), inline=False)
    admin_cfg.add_field(name="💯  Points config", value=(
        "```\n"
        "!setpoints <difficulty> <pts>   Set points per difficulty tier\n"
        "!points                         View full difficulty → points table\n"
        "```"
    ), inline=False)

    # ── Admin Manual Points ───────────────────────────────────────────────────
    admin_pts = discord.Embed(title="🎛️  Admin — Manual Points", color=0xED4245)
    admin_pts.add_field(name="\u200b", value=(
        "```\n"
        "!addpoints    @user <n> [reason]   Grant bonus points\n"
        "!subpoints    @user <n> [reason]   Deduct points\n"
        "!setmemberpoints @user <n> [reason]  Force-set adjustment total\n"
        "!pointlog     [@user]              Audit recent adjustments\n"
        "```"
    ), inline=False)

    # ── Admin Reset ───────────────────────────────────────────────────────────
    admin_rst = discord.Embed(title="🔄  Admin — Reset", color=0x99AAB5)
    admin_rst.add_field(name="🎯  Scoped resets", value=(
        "```\n"
        "!resetdaily     Today's daily board only\n"
        "!resetweek      Weekly board (daily is subset)\n"
        "!resetmonth     Monthly board (weekly/daily untouched)\n"
        "!resetalltime   ☢️  Wipe ALL data — irreversible\n"
        "```"
    ), inline=False)
    admin_rst.add_field(name="🔬  Targeted resets", value=(
        "```\n"
        "!resetuser @user [week|all]   Reset one member\n"
        "!resetproblem <db_id>         Un-mark all solves for a problem\n"
        "!resetweekfull                Delete solves + problems + deactivate week\n"
        "!saferemove <db_id>           Remove problem (safe — warns if solved)\n"
        "```"
    ), inline=False)
    admin_rst.add_field(name="⚠️  Isolation guarantee", value=(
        "> `!resetdaily`  only deletes **daily-only** problems (no `week_id`).\n"
        "> Weekly and monthly data is **never** touched by a daily reset."
    ), inline=False)

    # ── Footer ────────────────────────────────────────────────────────────────
    footer = discord.Embed(
        description=(
            "```\n"
            "⏱  Points valid only on the problem's assigned day  (00:00 – 23:59 IST)\n"
            "🔁  Daily board resets automatically at midnight IST\n"
            "🤖  Auto-checkall runs at 23:45 IST — use !check for instant results\n"
            "```"
        ),
        color=0x2B2D31,
    )
    footer.set_footer(
        text=f"CP Practice Bot  •  {config.PREFIX}help  •  Made with ❤️",
        icon_url=bot.user.display_avatar.url,
    )

    await ctx.send(embeds=[header, reg, problems, checking, lb, admin_cfg, admin_pts, admin_rst, footer])


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