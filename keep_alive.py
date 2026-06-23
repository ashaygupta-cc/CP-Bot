"""
keep_alive.py — Lightweight aiohttp HTTP server that:
  • Listens on $PORT (Render sets this automatically)
  • Exposes GET /health  → 200 OK  (used by cron-job.org to prevent sleep)
  • Pings Supabase on each /health call to keep the DB connection alive
"""

import asyncio
from aiohttp import web
from database.connection import ping_db


async def health(request: web.Request) -> web.Response:
    try:
        await ping_db()
        status = "ok"
    except Exception as e:
        status = f"db_error: {e}"
    return web.json_response({"status": status})


async def run_server(port: int):
    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/", health)      # Render also checks "/"

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🌐 Health server listening on port {port}")
