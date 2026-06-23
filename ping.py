"""
ping.py — Keeps Render from sleeping by hitting /health every 10 minutes.

Run locally:   python ping.py
Or use cron-job.org (recommended — no local machine needed).

Set RENDER_URL in your .env or environment:
  RENDER_URL=https://your-bot-name.onrender.com
"""

import time
import urllib.request
import os
from dotenv import load_dotenv

load_dotenv()

RENDER_URL   = os.getenv("RENDER_URL", "").rstrip("/")
INTERVAL_SEC = 10 * 60      # 10 minutes


def ping():
    url = f"{RENDER_URL}/health"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            body = r.read().decode()
        print(f"[{time.strftime('%H:%M:%S')}] ✅  {url}  →  {r.status}  {body[:60]}")
    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}] ⚠️  Ping failed: {e}")


if __name__ == "__main__":
    if not RENDER_URL:
        print("❌ Set RENDER_URL in your .env file first.")
        raise SystemExit(1)

    print(f"🏓 Pinging {RENDER_URL}/health every {INTERVAL_SEC // 60} minutes.")
    print("   Press Ctrl+C to stop.\n")

    while True:
        ping()
        time.sleep(INTERVAL_SEC)
