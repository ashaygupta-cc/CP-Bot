"""
bot.py — Entry point (v7)
Cogs: registry, admin, problems, checker, leaderboard,
      submissions, reset, points, verification, inactivity, contests, duels

v7 changes:
  • Added complete Duel System commands to !help and !adminhelp
  • Duels section shows all 8 user commands + modes reference
  • Admin section shows 4 admin duel commands (config, setrank, cancel)
  • Professional formatting with proper documentation

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
from api_server import run_server
import config
from database.connection import init_pool, close_pool, get_pool
from database import queries as q
# Serves /health plus the read-only REST API the website consumes.
# keep_alive.py is kept as a fallback: swap this import back to revert.
from api_server import run_server

intents = discord.Intents.default()
intents.message_content = True
intents.members = True          # Required for on_member_join + inactivity

bot = commands.Bot(command_prefix=config.PREFIX, intents=intents, help_command=None)

# ── Help webhook branding ─────────────────────────────────────────────────
# Same pattern as cogs/duels.py's Z4s webhook: a custom name + avatar for the
# message OUTSIDE the embed, kept separate from the logo used INSIDE the
# embed (thumbnail/author icon).
HELP_WEBHOOK_NAME = "Your Helper"
HELP_EMBED_LOGO = "https://raw.githubusercontent.com/ashaygupta-cc/ashaygupta-cc/main/Binary%20Beats.webp"
HELP_WEBHOOK_AVATAR = "https://raw.githubusercontent.com/ashaygupta-cc/ashaygupta-cc/main/Zodiac_Z408.png"

_help_webhook_cache: dict[int, discord.Webhook] = {}


async def _get_help_webhook(channel) -> discord.Webhook | None:
    """Get or create the branded webhook for this channel."""
    if channel.id in _help_webhook_cache:
        return _help_webhook_cache[channel.id]
    try:
        webhooks = await channel.webhooks()
        for wh in webhooks:
            if wh.name == HELP_WEBHOOK_NAME:
                _help_webhook_cache[channel.id] = wh
                return wh
        wh = await channel.create_webhook(name=HELP_WEBHOOK_NAME)
        _help_webhook_cache[channel.id] = wh
        return wh
    except Exception as e:
        print(f"[HELP_WEBHOOK] couldn't get/create webhook: {e}", flush=True)
        return None


async def _send_help_branded(channel, embed: discord.Embed):
    """Send via webhook for the custom name + avatar; falls back to a
    normal bot message if webhook creation/send fails for any reason
    (e.g. missing Manage Webhooks permission)."""
    wh = await _get_help_webhook(channel)
    if wh:
        try:
            return await wh.send(embed=embed, username=HELP_WEBHOOK_NAME,
                                 avatar_url=HELP_WEBHOOK_AVATAR, wait=True)
        except Exception as e:
            print(f"[HELP_WEBHOOK] send failed, falling back: {e}", flush=True)
    return await channel.send(embed=embed)

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
    "cogs.contests",       # ← upcoming contest reminders
    "cogs.duels",          # ← 1v1 duel system (CF/LC/ICPC + bot opponent)
    "cogs.website_sync",   # ← mirrors configured channels into PG for the website
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

    em = discord.Embed(
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
            "\u001b[1;33m██████╗  ██████╗ ████████╗\u001b[0m\n"
            "\u001b[1;33m██╔══██╗██╔═══██╗╚══██╔══╝\u001b[0m\n"
            "\u001b[1;32m██████╔╝██║   ██║   ██║   \u001b[0m\n"
            "\u001b[1;32m██╔══██╗██║   ██║   ██║   \u001b[0m\n"
            "\u001b[1;36m██████╔╝╚██████╔╝   ██║   \u001b[0m\n"
            "\u001b[1;36m╚═════╝  ╚═════╝    ╚═╝   \u001b[0m\n"
            "```\n"
            "> 🏆  **Competitive Programming Practice Tracker**\n"
            "> Multi-platform · Daily/Weekly/Monthly leaderboards · 1v1 Duels\n"
            "\n"
            "```\n"
            f"  Prefix    {config.PREFIX}\n"
            "  Platforms cf  ·  lc  ·  cc  ·  atcoder\n"
            "```"
        ),
        color=0x5865F2,
    )
    em.set_author(name="Command Reference", icon_url=HELP_EMBED_LOGO)

    em.add_field(name="__👤 Registration__", value=(
        "```\n"
        "!register <platform> <handle>   Link your handle\n"
        "!unregister <platform>          Remove a handle\n"
        "!profile [@user]                View handles + points\n"
        "!handles [platform]             List all members\n"
        "```"
    ), inline=False)

    em.add_field(name="__🔐 Verification__", value=(
        "```\n"
        "Automatic on join → Check #verification channel\n"
        "```"
    ), inline=False)

    em.add_field(name="__📅 Problems__", value=(
        "```\n"
        "!problems    This week's schedule, grouped by day\n"
        "```"
    ), inline=False)

    em.add_field(name="__🔍 Solve Checking__", value=(
        "```\n"
        "!check [@user]                  Check today's solve status\n"
        "!submissions <plat> [n] [@user] Browse recent submissions\n"
        "```\n"
        "**Auto-check schedule**\n"
        "```\n"
        "23:58 IST   Auto-checkall (before day closes)\n"
        "Every 6 h   Silent background point award\n"
        "```\n"
        "> 💡 **Tip:** `!check` is instant — auto is just a safety net."
    ), inline=False)

    em.add_field(name="__⚔️ Duels — Challenge & Play__", value=(
        "```\n"
        "!duel @user cp                  Challenge a player, DUEL mode (3-problem)\n"
        "!blitz @user cp                 Challenge a player, BLITZ mode (3-problem)\n"
        "!duel @user cp 2                Challenge a player, DUEL mode (2-problem)\n"
        "!blitz @user cp 2               Challenge a player, BLITZ mode (2-problem)\n"
        "```\n"
        "> `!duel` is always **DUEL** mode, `!blitz` is always **BLITZ** mode — "
        "the command name is the only thing that sets it, no extra token needed.\n"
        "> Format is optional (defaults to **3**-problem) — if you do specify it, "
        "only `2` or `3` are valid.\n"
        "> There's no manual bot match — challenge a player, and if they don't "
        "respond in time you're **auto-matched against the bot** automatically."
    ), inline=False)

    em.add_field(name="__⚔️ Duels — Ratings & Stats__", value=(
        "```\n"
        "!duelprofile [@user]            Your (or someone's) DUEL ratings (CP/DSA/ICPC)\n"
        "!blitzprofile [@user]           Your (or someone's) BLITZ ratings (CP/DSA/ICPC)\n"
        "!duel [cp|dsa|icpc] leaderboard  Top DUEL players in a family\n"
        "!blitz [cp|dsa|icpc] leaderboard Top BLITZ players in a family\n"
        "!duel [cp|dsa|icpc] rank        Your DUEL rank/tier\n"
        "!blitz [cp|dsa|icpc] rank       Your BLITZ rank/tier\n"
        "```\n"
        "> Same `!duel`/`!blitz` commands used to challenge — add `leaderboard` "
        "or `rank` instead of an opponent, family + keyword in any order, "
        "e.g. `!duel cp leaderboard` or `!duel leaderboard cp`.\n"
        "> Leave the family out for `!duel rank`/`!blitz rank` to see **all "
        "families**, or for `leaderboard` to default to **CP**."
    ), inline=False)

    em.add_field(name="__⚔️ Duels — Format & Modes__", value=(
        "```\n"
        "2-Problem Format   Medium + Medium\n"
        "3-Problem Format   Easy + Medium + Hard (Bo3)\n"
        "\n"
        "cp    blitz · duel      Codeforces  (real Elo)\n"
        "dsa   blitz · duel      LeetCode    (fixed points)\n"
        "icpc  blitz · duel      ICPC-style  (Codeforces problems, real Elo)\n"
        "\n"
        "Tiers: Newbie (800) → Pupil → Specialist → Expert →\n"
        "Master → GM → LGM (3000+)\n"
        "```\n"
        "> Tip: `!blitz @user <family> 2` or `!duel @user <family> 2` for quick 2-problem matches!"
    ), inline=False)

    em.add_field(name="__🏆 Leaderboards__", value=(
        "```\n"
        "!leaderboard           Daily + Weekly + Monthly in one view\n"
        "```\n"
        "**Reset schedule**\n"
        "```\n"
        "Daily    midnight IST (auto)\n"
        "Weekly   active week's end-date\n"
        "Monthly  active month's end-date\n"
        "```"
    ), inline=False)

    em.add_field(name="__ℹ️ Good to know__", value=(
        "```\n"
        "⏱  Points valid only on the problem's assigned day (00:00–23:59 IST)\n"
        "🔁  Daily board resets automatically at midnight IST\n"
        "🤖  Auto-checkall runs at 23:58 IST — !check gives instant results\n"
        "🔐  New members must verify via LinkedIn before accessing the server\n"
        "⚔️  Duel matches create private channels — only players + admins see them\n"
        "```"
    ), inline=False)

    em.set_footer(text=f"{HELP_WEBHOOK_NAME}  •  {config.PREFIX}help  •  Made with ❤️")

    await _send_help_branded(ctx.channel, em)


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

    # ── Duels — Admin (UPDATED) ────────────────────────────────────────────────
    admin_duel = discord.Embed(title="⚔️  Duels — Admin", color=0xED4245)
    admin_duel.add_field(name="__Rating Management__", value=(
        "```\n"
        "!duelsetrank @user <mode> <rating>  Set someone's duel rating\n"
        "```\n"
        "> `<mode>` here is the combined form: cp_blitz, cp_duel, dsa_blitz,\n"
        "> dsa_duel, icpc_blitz, icpc_duel."
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

    admin_sync = discord.Embed(title="🌐  Website Sync — Admin", color=0x5865F2)
    admin_sync.add_field(name="\u200b", value=(
        "```\n"
        "!syncultimate           Sync complete history across all channels\n"
        "!syncall                Sync last 10 messages across all channels\n"
        "!sync <key>             Sync last 10 messages of a specific channel\n"
        "!syncchannel <key> [n]  Sync custom number of messages of a channel\n"
        "!syncstatus             View row counts and last sync timestamps\n"
        "```\n"
        "> **Note:** Bot automatically syncs the last 10 messages in background every hour."
    ), inline=False)

    await ctx.send(embeds=[
        header, verif, problems, checking, lb, inact
    ])
    await ctx.send(embeds=[
        admin_cfg, admin_pts, admin_duel, admin_sync, admin_rst
    ])


# ── !setcookie  (ADMIN-ONLY) ──────────────────────────────────────────────────

@bot.command(name="setcookie")
@commands.check(_is_admin)
async def set_cookie(ctx, *, cookie_value: str = None):
    """
    Admin-only. Store the AtCoder REVEL_SESSION cookie so the bot can use a
    logged-in session for every AtCoder check.

    Usage:  !setcookie YOUR_COOKIE_VALUE

    The triggering message is auto-deleted after storing the value in the database,
    so the cookie is never visible in chat history.
    """
    if cookie_value is None:
        await ctx.send("❌  Provide the REVEL_SESSION value. Message will auto-delete.")
        return

    pool = get_pool()
    async with pool.acquire() as conn:
        await q.set_bot_config(conn, "atcoder_session", cookie_value)
        await q.set_bot_config(conn, "atcoder_cookie", cookie_value)

    try:
        await ctx.message.delete()
    except Exception:
        pass

    await ctx.send("✅  AtCoder REVEL_SESSION cookie stored. Message auto-deleted.", delete_after=5)


# ── Startup ──────────────────────────────────────────────────────────────────────

async def load_cogs():
    """Load all cogs from COGS list."""
    for cog in COGS:
        try:
            if cog in bot.extensions:
                continue
            await bot.load_extension(cog)
            print(f"  ✓  {cog}")
        except Exception as e:
            print(f"  ✗  {cog}: {e}")


async def main():
    """Initialize database, run web server immediately, apply async startup delay, & run bot with retry."""
    import os
    import sys
    import aiohttp

    # 1. Initialize DB pool
    await init_pool()

    # 2. Start API/health web server immediately so Render port checks pass
    port = int(os.getenv("PORT", 10000))
    asyncio.create_task(run_server(port, bot))

    # 3. Apply the startup delay asynchronously if set, letting web server handle port scans
    _delay = int(os.environ.get("BOT_STARTUP_DELAY", "45"))
    if os.environ.get("BOT_RESTARTED") == "1":
        # If the bot was restarted internally, we don't need the full startup cooldown again,
        # because we already slept before restarting. Keep a small 5s safety delay.
        _delay = min(_delay, 5)

    if _delay > 0:
        print(f"⏳  Startup cooldown: {_delay}s (set BOT_STARTUP_DELAY to change)...", flush=True)
        await asyncio.sleep(_delay)

    # 4. Start bot with restart retry for rate limits (429/1015) and connection errors
    retry_delay = int(os.environ.get("BOT_RETRY_DELAY", "60"))
    try:
        print("🚀  Starting Discord bot connection...", flush=True)
        async with bot:
            await load_cogs()
            await bot.start(config.DISCORD_TOKEN)
    except discord.LoginFailure as e:
        print("❌  LoginFailure: Invalid Discord token configured. Exiting.", flush=True)
        raise e
    except discord.HTTPException as e:
        if e.status == 429:
            print(f"⚠️  [DISCORD_RATE_LIMIT] Rate limited (429/1015). Sleeping {retry_delay}s and restarting process...", flush=True)
        else:
            print(f"⚠️  [DISCORD_HTTP_ERROR] HTTP error ({e.status}): {e}. Sleeping {retry_delay}s and restarting process...", flush=True)
        await asyncio.sleep(retry_delay)
        os.environ["BOT_RETRY_DELAY"] = str(min(retry_delay * 2, 600))
        os.environ["BOT_RESTARTED"] = "1"
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except (aiohttp.ClientConnectorError, ConnectionError, asyncio.TimeoutError) as e:
        print(f"⚠️  [CONNECTION_ERROR] Network connection issue: {e}. Sleeping {retry_delay}s and restarting process...", flush=True)
        await asyncio.sleep(retry_delay)
        os.environ["BOT_RETRY_DELAY"] = str(min(retry_delay * 2, 600))
        os.environ["BOT_RESTARTED"] = "1"
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        err_str = str(e).lower()
        if "connector" in err_str or "connection" in err_str or "timeout" in err_str or "socket" in err_str:
            print(f"⚠️  [CONNECTION_ERROR] Network connection issue: {e}. Sleeping {retry_delay}s and restarting process...", flush=True)
            await asyncio.sleep(retry_delay)
            os.environ["BOT_RETRY_DELAY"] = str(min(retry_delay * 2, 600))
            os.environ["BOT_RESTARTED"] = "1"
            os.execv(sys.executable, [sys.executable] + sys.argv)
        else:
            print(f"❌  Unexpected exception during bot execution:\n{tb}", flush=True)
            raise e


if __name__ == "__main__":
    import sys
    
    # Avoid UnicodeEncodeError on Windows consoles when printing emojis
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⏹  Shutting down...")
    except Exception as e:
        print(f"\n❌  Fatal error outside loop: {type(e).__name__}: {e}", flush=True)
        sys.exit(1)