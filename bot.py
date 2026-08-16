"""
bot.py — Entry point (v6)
Cogs: registry, admin, problems, checker, leaderboard,
      submissions, reset, points, verification, inactivity

v6 changes:
  • !help is now MEMBER-ONLY — admins commands are no longer shown to
    regular users, so members never learn admin command names/syntax.
  • !adminhelp is a NEW command, admin-only (Administrator permission
    or the ADMIN_ROLE role). Non-admins who try it get a clean refusal
    and see none of the admin command text.
  • !setcookie <value> — admin-only. Stores the AtCoder REVEL_SESSION
    cookie in the database (bot_config table) so atcoder.py can use a
    logged-in session for every check. The triggering message is
    deleted immediately so the cookie value never sits in chat history.
"""

import asyncio
import discord
from discord.ext import commands

import config
from database.connection import init_pool, close_pool, get_pool
from database import queries as q
from keep_alive import run_server

intents = discord.Intents.default()
intents.message_content = True
intents.members = True          # Required for on_member_join + inactivity

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
    "cogs.verification",   # ← LinkedIn verification on join
    "cogs.inactivity",     # ← 15/20/25 day inactivity warnings
]


def _is_admin(ctx) -> bool:
    """Administrator permission OR the configured ADMIN_ROLE — same rule
    used everywhere else in the bot (e.g. !checkall)."""
    if ctx.author.guild_permissions.administrator:
        return True
    role_names = {r.name for r in getattr(ctx.author, "roles", [])}
    return config.ADMIN_ROLE in role_names


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


# ── !help  (MEMBER-ONLY — no admin commands shown, ever) ─────────────────────

@bot.command(name="help")
async def help_cmd(ctx, section: str = None):
    """Show member commands only. Admins: use !adminhelp for admin commands."""

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

    # ── Verification ──────────────────────────────────────────────────────────
    verif = discord.Embed(title="🔐  Verification", color=0x5865F2)
    verif.add_field(name="🙋  Member", value=(
        "```\n"
        "Automatic on join → Check #verification channel\n"
        "```"
    ), inline=False)

    # ── Problems ──────────────────────────────────────────────────────────────
    problems = discord.Embed(title="📅  Problems", color=0x57F287)
    problems.add_field(name="\u200b", value=(
        "```\n"
        "!problems    This week's schedule, grouped by day\n"
        "```"
    ), inline=False)

    # ── Solve Checking ────────────────────────────────────────────────────────
    checking = discord.Embed(title="🔍  Solve Checking", color=0xFEE75C)
    checking.add_field(name="\u200b", value=(
        "```\n"
        "!check [@user]                  Check today's solve status\n"
        "!submissions <plat> [n] [@user] Browse recent submissions\n"
        "```"
    ), inline=False)
    checking.add_field(name="⏱️  Auto-check schedule", value=(
        "```\n"
        "23:58 IST   Auto-checkall (before day closes)\n"
        "Every 6 h   Silent background point award\n"
        "```\n"
        "> 💡 **Tip:** `!check` is instant — auto is just a safety net."
    ), inline=False)

    # ── Leaderboards ──────────────────────────────────────────────────────────
    lb = discord.Embed(title="🏆  Leaderboards", color=0xF1C40F)
    lb.add_field(name="\u200b", value=(
        "```\n"
        "!leaderboard           Daily + Weekly + Monthly in one view\n"
        "```"
    ), inline=False)
    lb.add_field(name="🔄  Reset schedule", value=(
        "```\n"
        "Daily    midnight IST (auto)\n"
        "Weekly   active week's end-date\n"
        "Monthly  active month's end-date\n"
        "```"
    ), inline=False)

    # ── Footer ────────────────────────────────────────────────────────────────
    footer = discord.Embed(
        description=(
            "```\n"
            "⏱  Points valid only on the problem's assigned day  (00:00 – 23:59 IST)\n"
            "🔁  Daily board resets automatically at midnight IST\n"
            "🤖  Auto-checkall runs at 23:58 IST — use !check for instant results\n"
            "🔐  New members must verify via LinkedIn before accessing the server\n"
            "```"
        ),
        color=0x2B2D31,
    )
    footer.set_footer(
        text=f"CP Practice Bot  •  {config.PREFIX}help  •  Made with ❤️",
        icon_url=bot.user.display_avatar.url,
    )

    await ctx.send(embeds=[
        header, reg, verif, problems, checking, lb, footer
    ])


# ── !adminhelp  (ADMIN-ONLY — refuses cleanly for everyone else) ─────────────

@bot.command(name="adminhelp")
async def admin_help_cmd(ctx):
    """Show admin commands. Restricted to Administrator / ADMIN_ROLE."""
    if not _is_admin(ctx):
        await ctx.send("❌  This command is restricted to admins.")
        return

    header = discord.Embed(
        title="🔒  CP Practice Bot — Admin Command Reference",
        description=f"Prefix: `{config.PREFIX}`  ·  Visible to admins only.",
        color=0xED4245,
    )
    header.set_thumbnail(url=bot.user.display_avatar.url)

    verif = discord.Embed(title="🔐  Verification — Admin", color=0x5865F2)
    verif.add_field(name="\u200b", value=(
        "```\n"
        "!sendverification       Post verification message in current channel\n"
        "!reverify @user         Reset a member back to pending verification\n"
        "!verificationstatus     Show pending vs verified members\n"
        "```"
    ), inline=False)

    problems = discord.Embed(title="📅  Problems — Admin", color=0x57F287)
    problems.add_field(name="\u200b", value=(
        "```\n"
        "!addproblem <plat> <id> <diff> <date> [pts]\n"
        "!rius <id>           Safely remove problem (blocked if already solved)\n"
        "!removeproblem <id> [keep_history]\n"
        "!setdifficulty <id> <diff>\n"
        "```"
    ), inline=False)

    checking = discord.Embed(title="🔍  Solve Checking — Admin", color=0xFEE75C)
    checking.add_field(name="\u200b", value=(
        "```\n"
        "!checkall    Bulk-check all members at once\n"
        "```"
    ), inline=False)

    lb = discord.Embed(title="🏆  Leaderboards — Admin", color=0xF1C40F)
    lb.add_field(name="\u200b", value=(
        "```\n"
        "!lbfull [scope]        Full list, all users, paginated\n"
        "!lbdaily               Shortcut → !lbfull daily\n"
        "!lbweekly              Shortcut → !lbfull weekly\n"
        "!lbmonthly             Shortcut → !lbfull monthly\n"
        "```"
    ), inline=False)

    inact = discord.Embed(title="⏰  Inactivity Tracker — Admin", color=0xED4245)
    inact.add_field(name="\u200b", value=(
        "```\n"
        "!inactivity              Full inactivity report\n"
        "!inactivitycheck         Manually trigger check now\n"
        "!exemptinactivity @user  Exempt a member from warnings\n"
        "!unexemptinactivity @u   Remove exemption\n"
        "```"
    ), inline=False)
    inact.add_field(name="⚙️  Schedule", value=(
        "```\n"
        "15 days → DM Warning #1\n"
        "20 days → DM Warning #2\n"
        "25 days → DM Warning #3 + public mention\n"
        "```"
    ), inline=False)

    admin_cfg = discord.Embed(title="⚙️  Configuration", color=0xEB459E)
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
    admin_cfg.add_field(name="🍪  AtCoder session", value=(
        "```\n"
        "!setcookie <REVEL_SESSION value>   Store/refresh AtCoder session cookie\n"
        "```\n"
        "> Your message is auto-deleted right after — the value never stays in chat."
    ), inline=False)

    admin_pts = discord.Embed(title="🎛️  Manual Points", color=0xED4245)
    admin_pts.add_field(name="\u200b", value=(
        "```\n"
        "!addpoints    @user <n> [reason]   Grant bonus points\n"
        "!subpoints    @user <n> [reason]   Deduct points\n"
        "!setmemberpoints @user <n> [reason]  Force-set adjustment total\n"
        "!pointlog     [@user]              Audit recent adjustments\n"
        "```"
    ), inline=False)

    admin_rst = discord.Embed(title="🔄  Reset", color=0x99AAB5)
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

    await ctx.send(embeds=[
        header, verif, problems, checking, lb, inact, admin_cfg, admin_pts, admin_rst
    ])


# ── !setcookie  (ADMIN-ONLY) ──────────────────────────────────────────────────

@bot.command(name="setcookie")
@commands.has_permissions(administrator=True)
async def set_cookie(ctx, *, cookie_value: str = None):
    """
    (Admin) Store/refresh the AtCoder REVEL_SESSION cookie used for !check.
    Usage: !setcookie <value>
    The triggering message is deleted immediately so the cookie value
    never sits visible in chat history.
    """
    try:
        await ctx.message.delete()
    except discord.Forbidden:
        pass  # bot may lack Manage Messages — not fatal, just a heads-up below

    if not cookie_value:
        await ctx.send(
            "❌  Usage: `!setcookie <REVEL_SESSION value>`\n"
            "> ⚠️ I couldn't delete your message automatically — please delete it "
            "manually if it contained a cookie value.",
            delete_after=15,
        )
        return

    pool = get_pool()
    async with pool.acquire() as conn:
        await q.set_config(conn, "atcoder_session", cookie_value.strip(), str(ctx.author.id))

    await ctx.send(
        "✅  AtCoder session cookie updated. `!check` will now use it for AtCoder.",
        delete_after=10,
    )


@set_cookie.error
async def set_cookie_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(f"❌  You need the **{config.ADMIN_ROLE}** role or Administrator permission.")


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