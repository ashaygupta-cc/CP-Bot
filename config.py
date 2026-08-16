"""
config.py — All environment variables and bot-wide constants.
v4: Added contest reminder config. Removed clist.by dependency.
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

# ── Verification system (cogs/verification.py) ────────────────────────────────
VERIFICATION_CHANNEL   = os.getenv("VERIFICATION_CHANNEL",  "verification")
WELCOME_CHANNEL        = os.getenv("WELCOME_CHANNEL",        "general")
LINKEDIN_URL           = os.getenv("LINKEDIN_URL",           "https://www.linkedin.com/in/yourprofile")
VERIFICATION_ROLE_NAME = os.getenv("VERIFICATION_ROLE",      "Verification")
MEMBER_ROLE_NAME       = os.getenv("MEMBER_ROLE",            "Member")

# ── Inactivity system (cogs/inactivity.py) ────────────────────────────────────
INACTIVITY_CHANNEL     = os.getenv("INACTIVITY_CHANNEL",    "general")

# ── Nightly auto-check summary channel (cogs/checker.py) ─────────────────────
_checkall_ch = os.getenv("CHECKALL_CHANNEL_ID", "")
CHECKALL_CHANNEL_ID: int | None = int(_checkall_ch) if _checkall_ch.isdigit() else None

# ── Contest reminder system (cogs/contests.py) ────────────────────────────────
# Channel name where reminders are posted (no # prefix)
CONTEST_REMINDER_CHANNEL = os.getenv("CONTEST_REMINDER_CHANNEL", "contest-reminder")
# Role to ping — "everyone" for @everyone, exact role name like "Member", or "" for no ping
CONTEST_REMINDER_ROLE    = os.getenv("CONTEST_REMINDER_ROLE",    "everyone")