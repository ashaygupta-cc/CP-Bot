"""
keep_alive.py — Lightweight aiohttp HTTP server that:
  • Listens on $PORT (Render sets this automatically)
  • Exposes GET /health  → 200 OK  (used by Render health checks)
  • Exposes GET / → 200 OK (fallback endpoint)
  • Pings Supabase on each /health call to keep DB connection alive
"""

import asyncio
from aiohttp import web


async def health(request: web.Request) -> web.Response:
    """Health check endpoint — tries to ping DB if available."""
    status_info = {"status": "ok"}
    
    # Try to ping database to keep connection alive
    try:
        from database.connection import ping_db
        await ping_db()
        status_info["database"] = "ok"
    except ImportError:
        # ping_db not available yet, that's fine
        status_info["database"] = "skipped"
    except Exception as e:
        # DB ping failed, but still return 200 OK to Render
        status_info["database"] = f"error: {str(e)[:50]}"
    
    return web.json_response(status_info)


async def run_server(port: int):
    """Start the HTTP server on given port."""
    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/", health)      # Render also checks "/"

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🌐 Health server listening on port {port}")
    
    # Keep server running indefinitely
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        await runner.cleanup()