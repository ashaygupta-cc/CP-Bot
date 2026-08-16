"""
config.py — All environment variables and bot-wide constants.
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
# All "day" boundaries are midnight IST
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
