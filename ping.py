"""
ping.py — Keeps Render from sleeping by hitting /health every 10 minutes.

This is optional — you can use this locally, or use cron-job.org (recommended).

Usage:
  Local:      python ping.py
  Cron:       Set URL to https://your-bot-name.onrender.com/health
  Schedule:   Every 10 minutes

Set RENDER_URL in your .env or environment:
  RENDER_URL=https://cp-discord-bot.onrender.com
"""

import time
import urllib.request
import os
import sys
from dotenv import load_dotenv

load_dotenv()

RENDER_URL   = os.getenv("RENDER_URL", "").rstrip("/")
INTERVAL_SEC = 10 * 60      # 10 minutes


def ping():
    """Ping the health endpoint."""
    url = f"{RENDER_URL}/health"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            body = r.read().decode()
        print(f"[{time.strftime('%H:%M:%S')}] ✅  {r.status}  {url}")
        return True
    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}] ❌  Ping failed: {e}")
        return False


if __name__ == "__main__":
    if not RENDER_URL:
        print("❌ ERROR: Set RENDER_URL in your .env file first.")
        print("   Example: RENDER_URL=https://cp-discord-bot.onrender.com")
        sys.exit(1)

    print(f"🏓 Pinging {RENDER_URL}/health every {INTERVAL_SEC // 60} minutes")
    print("   Press Ctrl+C to stop.\n")

    while True:
        ping()
        time.sleep(INTERVAL_SEC)