"""
config.py — All environment variables and bot-wide constants.
v3: Added verification + inactivity config.
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
# Channel where the bot posts verification messages (no # prefix)
VERIFICATION_CHANNEL  = os.getenv("VERIFICATION_CHANNEL", "verification")
# Channel where welcome messages go after verification (no # prefix)
WELCOME_CHANNEL       = os.getenv("WELCOME_CHANNEL", "general")
# Full LinkedIn URL for the Follow button
LINKEDIN_URL          = os.getenv("LINKEDIN_URL", "https://www.linkedin.com/in/yourprofile")
# Role given to new members BEFORE verification (restricts access)
VERIFICATION_ROLE_NAME = os.getenv("VERIFICATION_ROLE", "Verification")
# Role given AFTER successful verification (grants full access)
MEMBER_ROLE_NAME       = os.getenv("MEMBER_ROLE", "Member")

# ── Inactivity system (cogs/inactivity.py) ────────────────────────────────────
# Channel for public 25-day warning (no # prefix)
INACTIVITY_CHANNEL      = os.getenv("INACTIVITY_CHANNEL", "general")

# ── Optional: channel ID for nightly auto-check summary (cogs/checker.py) ────
# Leave blank to disable the nightly summary embed
_checkall_ch = os.getenv("CHECKALL_CHANNEL_ID", "")
CHECKALL_CHANNEL_ID: int | None = int(_checkall_ch) if _checkall_ch.isdigit() else None