"""
database/connection.py — asyncpg connection pool.
Call init_pool() once at startup; use get_pool() everywhere else.

FIX: statement_cache_size=0
  Supabase uses PgBouncer in transaction pool mode. asyncpg by default
  caches prepared statements per connection, but PgBouncer can route
  different transactions to different backend connections — so a prepared
  statement created on connection A doesn't exist on connection B,
  causing:
      prepared statement "__asyncpg_stmt_5__" already exists
  Setting statement_cache_size=0 disables this cache. asyncpg sends
  plain queries instead — fully PgBouncer-compatible, negligible overhead
  for a Discord bot.
"""

import asyncpg
import config

_pool: asyncpg.Pool | None = None


async def init_pool() -> asyncpg.Pool:
    global _pool
    _pool = await asyncpg.create_pool(
        dsn=config.DATABASE_URL,
        min_size=2,
        max_size=10,
        command_timeout=30,
        statement_cache_size=0,   # Required for Supabase/PgBouncer transaction mode
    )
    return _pool


async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialised. Call init_pool() first.")
    return _pool


async def ping_db():
    """Lightweight keep-alive ping — called by the /health endpoint."""
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.fetchval("SELECT 1")