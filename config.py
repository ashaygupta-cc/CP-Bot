"""
config.py — All environment variables and bot-wide constants.
v5: Added duel mode channel routing for dedicated duel spaces.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Discord ───────────────────────────────────────────────────────────────────
DISCORD_TOKEN   = os.getenv("DISCORD_TOKEN", "")
PREFIX          = os.getenv("PREFIX", "!")
ADMIN_ROLE      = os.getenv("ADMIN_ROLE", "Admin")

# ── Database (Supabase PostgreSQL) ────────────────────────────────────────────
DATABASE_URL    = os.getenv("DATABASE_URL", "")

# ── Render keep-alive ─────────────────────────────────────────────────────────
RENDER_URL      = os.getenv("RENDER_URL", "")
PORT            = int(os.getenv("PORT", 10000))

# ── Timezone (IST = UTC+5:30) ─────────────────────────────────────────────────
IST_OFFSET_HOURS = 5
IST_OFFSET_MINS  = 30

# ── Default difficulty → points mapping ───────────────────────────────────────
DEFAULT_DIFFICULTY_POINTS: dict[str, int] = {
    "easy":   5,
    "medium": 10,
    "hard":   20,
    "expert": 35,
    "master": 50,
}

# ── Embed colours ─────────────────────────────────────────────────────────────
COLOR_SUCCESS = 0x57F287   # green
COLOR_ERROR   = 0xED4245   # red
COLOR_INFO    = 0x5865F2   # blurple
COLOR_WARN    = 0xFEE75C   # yellow
COLOR_GOLD    = 0xFFD700   # gold
COLOR_PURPLE  = 0x9B59B6   # purple (monthly)
COLOR_CYAN    = 0x00D9FF   # cyan (duel ranks)

# ── Verification system (cogs/verification.py) ────────────────────────────────
VERIFICATION_CHANNEL   = os.getenv("VERIFICATION_CHANNEL",  "verification")
WELCOME_CHANNEL        = os.getenv("WELCOME_CHANNEL",        "general")
LINKEDIN_URL           = os.getenv("LINKEDIN_URL",           "https://www.linkedin.com/in/yourprofile")
VERIFICATION_ROLE_NAME = os.getenv("VERIFICATION_ROLE",      "Verification")
MEMBER_ROLE_NAME       = os.getenv("MEMBER_ROLE",            "Member")

# ── Inactivity system (cogs/inactivity.py) ────────────────────────────────────
INACTIVITY_CHANNEL     = os.getenv("INACTIVITY_CHANNEL",    "inactivity-info")
_inactivity_ch_id      = os.getenv("INACTIVITY_CHANNEL_ID", "1538429554996154388")
INACTIVITY_CHANNEL_ID  = int(_inactivity_ch_id) if _inactivity_ch_id.isdigit() else 1538429554996154388

# ── Nightly auto-check summary channel (cogs/checker.py) ─────────────────────
_checkall_ch = os.getenv("CHECKALL_CHANNEL_ID", "")
CHECKALL_CHANNEL_ID: int | None = int(_checkall_ch) if _checkall_ch.isdigit() else None

# ── Week/Month-end leaderboard announcement (cogs/checker.py) ────────────────
# Fires automatically from the 23:58 IST nightly check, ONLY on the exact
# last day of the currently active week / month (never on a normal daily run).
# Channel where the announcement gets posted:
_lb_announce_ch = os.getenv("LEADERBOARD_ANNOUNCE_CHANNEL_ID", "")
LEADERBOARD_ANNOUNCE_CHANNEL_ID: int | None = int(_lb_announce_ch) if _lb_announce_ch.isdigit() else None
# Role to ping in that announcement — plain numeric role ID (no <@&> wrapper).
# Leave blank to skip the ping and just post the message.
LEADERBOARD_PING_ROLE_ID = os.getenv("LEADERBOARD_PING_ROLE_ID", "")

# ── Contest reminder system (cogs/contests.py) ────────────────────────────────
# Channel name where reminders are posted (no # prefix)
CONTEST_REMINDER_CHANNEL = os.getenv("CONTEST_REMINDER_CHANNEL", "contest-reminder")
# Role to ping — "everyone" for @everyone, exact role name like "Member", or "" for no ping
CONTEST_REMINDER_ROLE    = os.getenv("CONTEST_REMINDER_ROLE",    "everyone")

# ── Duel system channel routing ────────────────────────────────────────────────
# Each mode gets its own parent category/channel for match rooms.
# Format: DUEL_<MODE>_CHANNEL = channel ID (int) or channel name (str).
# If not set, matches can be created in any channel where the command is run.
# Recommended setup:
#   - Create a "Duels" category with subcategories: CP, DSA, ICPC
#   - Create channels: cp-duels, cp-blitz, dsa-duels, dsa-blitz, icpc-duels, icpc-blitz
#   - Set these env vars to those channel IDs
DUEL_CP_DUEL_CHANNEL   = os.getenv("DUEL_CP_DUEL_CHANNEL", "")     # CP Duel matches
DUEL_CP_BLITZ_CHANNEL  = os.getenv("DUEL_CP_BLITZ_CHANNEL", "")    # CP Blitz matches
DUEL_DSA_DUEL_CHANNEL  = os.getenv("DUEL_DSA_DUEL_CHANNEL", "")    # DSA (LC) Duel matches
DUEL_DSA_BLITZ_CHANNEL = os.getenv("DUEL_DSA_BLITZ_CHANNEL", "")   # DSA (LC) Blitz matches
DUEL_ICPC_DUEL_CHANNEL = os.getenv("DUEL_ICPC_DUEL_CHANNEL", "")   # ICPC Duel matches
DUEL_ICPC_BLITZ_CHANNEL= os.getenv("DUEL_ICPC_BLITZ_CHANNEL", "")  # ICPC Blitz matches

# Map mode → channel ID for easy lookup
DUEL_MODE_CHANNELS = {
    "cp_duel":    DUEL_CP_DUEL_CHANNEL,
    "cp_blitz":   DUEL_CP_BLITZ_CHANNEL,
    "dsa_duel":   DUEL_DSA_DUEL_CHANNEL,
    "dsa_blitz":  DUEL_DSA_BLITZ_CHANNEL,
    "icpc_duel":  DUEL_ICPC_DUEL_CHANNEL,
    "icpc_blitz": DUEL_ICPC_BLITZ_CHANNEL,
}

# ── Website integration (Phase 1) ───────────────────────────────────────────
# The REST API in api_server.py has no Discord context, so the guild it should
# report on must come from the environment. Right-click the server in Discord
# with Developer Mode enabled → Copy Server ID.
GUILD_ID = os.getenv("GUILD_ID", "")

# Shared secret between this bot and the website backend. Only /api/internal/*
# routes require it; public read routes do not. Generate with:
#   openssl rand -hex 32
BB_API_KEY = os.getenv("BB_API_KEY", "")

# Browser origins allowed to call the API directly. The website normally goes
# through its own server-side proxy, so this mainly covers local development.
BB_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "BB_ALLOWED_ORIGINS",
        "http://localhost:5173,http://localhost:4000",
    ).split(",")
    if o.strip()
]


# ── Website channel sync (Phase 2) ──────────────────────────────────────────
# Channels mirrored into Postgres for the website. Anything NOT listed here is
# never stored — ordinary server chat stays in Discord only.
#
# The key is what the website asks for (/api/channels/server_updates), so
# channel ids can change without touching frontend code.
SYNCED_CHANNELS = {
    "1437074829235982356": "contest_reminder",
    "1453501768179650570": "server_updates",
    "1453499579583565969": "updates_official",
    "1456216598846378057": "competitions_info",
    "1433862332534096105": "ideas_feedback",
    "1518192178520657981": "self_promo",
    "1524890934095904920": "arena_guide",
    "1518900916021887046": "maths_lounge",
    "1526145287993688104": "cp_dsa_roadmap",
    "1520284027008061562": "daily_editorials",
    "1453508409864486912": "server_info",
    "1433864900484009985": "team_info",
    "1453507423125110815": "find_us_online",
    "1526149678796898305": "oa_questions",
}
