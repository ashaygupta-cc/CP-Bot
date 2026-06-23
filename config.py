"""
config.py — All environment variables and bot-wide constants.
Set these in a .env file locally or in Render's environment settings.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Discord ───────────────────────────────────────────────────────────────────
DISCORD_TOKEN   = os.getenv("DISCORD_TOKEN", "")
PREFIX          = os.getenv("PREFIX", "!")
ADMIN_ROLE      = os.getenv("ADMIN_ROLE", "Admin")       # Role name for admin commands

# ── Database (Supabase PostgreSQL) ────────────────────────────────────────────
DATABASE_URL    = os.getenv("DATABASE_URL", "")          # postgres://user:pass@host:5432/db

# ── Render keep-alive ─────────────────────────────────────────────────────────
RENDER_URL      = os.getenv("RENDER_URL", "")            # https://your-bot.onrender.com
PORT            = int(os.getenv("PORT", 10000))          # Render sets this automatically

# ── Default difficulty → points mapping (admins can override per guild) ───────
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
