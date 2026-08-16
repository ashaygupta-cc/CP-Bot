"""
api_server.py — Public read API for the Binary Beats website.

Drop this next to keep_alive.py and call `run_server(port)` from here
instead of from keep_alive.py (this module re-exports /health and /, so
it is a straight replacement).

Design rule: the BOT stays the single source of truth. This file adds NO
business logic — every route is a SELECT over tables the bot already
writes. Nothing here mutates rating, points, or duel state.

Auth model:
  • GET routes are public (the website shows leaderboards to logged-out
    visitors by design).
  • Anything under /api/internal/ requires header  X-BB-Key: $BB_API_KEY.
"""

import os
import json
import datetime as dt
from aiohttp import web

from database.connection import get_pool, ping_db
from database import queries, duel_queries
import config

GUILD_ID = os.getenv("GUILD_ID", "")
BB_API_KEY = os.getenv("BB_API_KEY", "")
ALLOWED_ORIGINS = [o.strip() for o in os.getenv(
    "BB_ALLOWED_ORIGINS",
    "http://localhost:5173,https://binarybeats.vercel.app",
).split(",") if o.strip()]

_bot_instance = None


# ─────────────────────────────  helpers  ─────────────────────────────

def _json(data, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=_dumps)


def _dumps(obj) -> str:
    import json

    def default(o):
        if isinstance(o, (dt.datetime, dt.date)):
            return o.isoformat()
        raise TypeError(f"not serialisable: {type(o)}")

    return json.dumps(obj, default=default)


def _rows(records) -> list[dict]:
    return [dict(r) for r in records]


def _guild(request: web.Request) -> str:
    return request.query.get("guild_id") or GUILD_ID


async def _cors(app, handler):
    async def middleware(request: web.Request):
        origin = request.headers.get("Origin", "")
        if request.method == "OPTIONS":
            resp = web.Response(status=204)
        else:
            try:
                resp = await handler(request)
            except web.HTTPException as exc:
                resp = exc
            except Exception as exc:  # noqa: BLE001
                resp = _json({"error": str(exc)[:200]}, status=500)
        if origin in ALLOWED_ORIGINS:
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Access-Control-Allow-Credentials"] = "true"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type,X-BB-Key"
            resp.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
        return resp
    return middleware


def _require_key(request: web.Request) -> None:
    if not BB_API_KEY or request.headers.get("X-BB-Key") != BB_API_KEY:
        raise web.HTTPUnauthorized(text='{"error":"bad key"}',
                                   content_type="application/json")


# ─────────────────────────────  routes  ─────────────────────────────

async def health(request: web.Request) -> web.Response:
    info = {"status": "ok"}
    try:
        await ping_db()
        info["database"] = "ok"
    except Exception as e:  # noqa: BLE001
        info["database"] = f"error: {str(e)[:50]}"
    return _json(info)


async def daily_problems(request: web.Request) -> web.Response:
    """GET /api/problems?limit=30&platform=codeforces
    Public view of the daily problem feed the bot posts."""
    limit = min(int(request.query.get("limit", 30)), 100)
    platform = request.query.get("platform")
    sql = """
        SELECT p.id, p.platform, p.problem_id, p.title, p.difficulty,
               p.points, p.assigned_date, p.set_by,
               w.label AS week_label, m.label AS month_label,
               (SELECT COUNT(*) FROM solves s WHERE s.problem_db_id = p.id)
                 AS solve_count
        FROM problems p
        LEFT JOIN weeks  w ON w.id = p.week_id
        LEFT JOIN months m ON m.id = p.month_id
        WHERE p.guild_id = $1
          AND ($2::text IS NULL OR p.platform = $2)
        ORDER BY p.assigned_date DESC, p.id DESC
        LIMIT $3
    """
    async with get_pool().acquire() as conn:
        recs = await conn.fetch(sql, _guild(request), platform, limit)
    return _json({"problems": _rows(recs)})


async def problem_solvers(request: web.Request) -> web.Response:
    """GET /api/problems/{id}/solvers"""
    pid = int(request.match_info["id"])
    sql = """
        SELECT u.discord_id, u.discord_username,
               s.solved_at, s.points_awarded
        FROM solves s
        JOIN users u ON u.discord_id = s.discord_id
        WHERE s.problem_db_id = $1
        ORDER BY s.solved_at ASC NULLS LAST
    """
    async with get_pool().acquire() as conn:
        recs = await conn.fetch(sql, pid)
    return _json({"problem_id": pid, "solvers": _rows(recs)})


async def leaderboard_points(request: web.Request) -> web.Response:
    """GET /api/leaderboard/points?scope=all|daily|week|month&limit=100

    Delegates to database/queries.py so the website can never drift from
    what the bot's own !leaderboard shows. In particular the monthly board
    must read monthly_solves, not solves — !resetweek deletes from solves,
    which is exactly the bug the v2.2 migration exists to fix.
    """
    scope = request.query.get("scope", "all")
    limit = min(int(request.query.get("limit", 100)), 500)
    gid = _guild(request)

    async with get_pool().acquire() as conn:
        if scope == "daily":
            target = request.query.get("date")
            day = dt.date.fromisoformat(target) if target else dt.date.today()
            rows = await queries.get_daily_leaderboard(conn, gid, day)
        elif scope == "week":
            week = await queries.get_active_week(conn, gid)
            if week is None:
                return _json({"scope": scope, "entries": [], "note": "no active week"})
            rows = await queries.get_weekly_leaderboard(conn, gid, week["id"])
        elif scope == "month":
            month = await queries.get_active_month(conn, gid)
            if month is None:
                return _json({"scope": scope, "entries": [], "note": "no active month"})
            rows = await queries.get_monthly_leaderboard(
                conn, gid, month["start_date"], month["end_date"]
            )
        else:
            rows = await queries.get_alltime_leaderboard(conn, gid)

        # Attach usernames — the bot's queries return discord_id only,
        # because in Discord it resolves them from the guild member cache.
        ids = [r["discord_id"] for r in rows][:limit]
        names = {}
        if ids:
            urows = await conn.fetch(
                "SELECT discord_id, discord_username FROM users WHERE discord_id = ANY($1::text[])",
                ids,
            )
            names = {u["discord_id"]: u["discord_username"] for u in urows}

    entries = []
    for i, r in enumerate(rows[:limit], start=1):
        did = r["discord_id"]
        entries.append({
            "rank": i,
            "discord_id": did,
            "discord_username": names.get(did, did),
            "points": int(r["total"]),
            "solved": int(r["solved_count"]),
        })
    return _json({"scope": scope, "entries": entries})


async def leaderboard_rating(request: web.Request) -> web.Response:
    """GET /api/leaderboard/rating?mode=cp_duel&limit=100

    One board per mode, never merged. Valid modes are exactly what the bot
    writes into duel_ratings.mode: cp_duel, cp_blitz, dsa_duel, dsa_blitz,
    icpc_duel, icpc_blitz (underscores, not hyphens).
    """
    mode = request.query.get("mode", "cp_duel")
    limit = min(int(request.query.get("limit", 100)), 500)
    gid = _guild(request)

    async with get_pool().acquire() as conn:
        rows = await duel_queries.get_leaderboard(conn, gid, mode, limit)
        ids = [r["discord_id"] for r in rows]
        names = {}
        extra = {}
        if ids:
            urows = await conn.fetch(
                "SELECT discord_id, discord_username FROM users WHERE discord_id = ANY($1::text[])",
                ids,
            )
            names = {u["discord_id"]: u["discord_username"] for u in urows}
            # get_leaderboard omits these two columns; fetch them so the
            # website's profile cards and the board agree.
            erows = await conn.fetch(
                """SELECT discord_id, bot_matches, updated_at FROM duel_ratings
                   WHERE guild_id = $1 AND mode = $2 AND discord_id = ANY($3::text[])""",
                gid, mode, ids,
            )
            extra = {e["discord_id"]: e for e in erows}

    entries = []
    for i, r in enumerate(rows, start=1):
        did = r["discord_id"]
        ex = extra.get(did)
        entries.append({
            "rank": i,
            "discord_id": did,
            "discord_username": names.get(did, did),
            "rating": r["rating"],
            "wins": r["wins"],
            "losses": r["losses"],
            "draws": r["draws"],
            "streak": r["streak"],
            "bot_matches": ex["bot_matches"] if ex else 0,
            "updated_at": ex["updated_at"] if ex else None,
        })
    return _json({"mode": mode, "entries": entries})


async def modes(request: web.Request) -> web.Response:
    """GET /api/modes — lets the website build its leaderboard tabs from
    live data instead of a hardcoded list."""
    async with get_pool().acquire() as conn:
        recs = await conn.fetch(
            "SELECT DISTINCT mode FROM duel_ratings WHERE guild_id = $1 ORDER BY mode",
            _guild(request),
        )
    return _json({"modes": [r["mode"] for r in recs]})


async def profile(request: web.Request) -> web.Response:
    """GET /api/users/{discord_id} — everything the profile page needs."""
    did = request.match_info["discord_id"]
    gid = _guild(request)
    async with get_pool().acquire() as conn:
        user = await conn.fetchrow(
            "SELECT discord_id, discord_username, created_at FROM users WHERE discord_id = $1",
            did,
        )
        if user is None:
            return _json({"error": "not found"}, status=404)

        handles = await conn.fetch(
            "SELECT platform, handle, verified, linked_at FROM handles WHERE discord_id = $1",
            did,
        )
        # Same call the bot's own !profile uses.
        ratings = await duel_queries.get_profile(conn, did, gid)
        points = await conn.fetchrow(
            """SELECT
                 COALESCE((SELECT SUM(points_awarded) FROM solves
                           WHERE discord_id = $1 AND guild_id = $2), 0)::int
               + COALESCE((SELECT SUM(delta) FROM point_adjustments
                           WHERE discord_id = $1 AND guild_id = $2), 0)::int AS points,
                 (SELECT COUNT(*) FROM solves
                  WHERE discord_id = $1 AND guild_id = $2)::int AS solved""",
            did, gid,
        )
        recent = await conn.fetch(
            """SELECT p.platform, p.problem_id, p.title, p.difficulty,
                      s.points_awarded, s.solved_at
               FROM solves s JOIN problems p ON p.id = s.problem_db_id
               WHERE s.discord_id = $1 AND s.guild_id = $2
               ORDER BY s.solved_at DESC NULLS LAST LIMIT 20""",
            did, gid,
        )
        streak = await conn.fetchval(
            """WITH days AS (
                   SELECT DISTINCT p.assigned_date AS d
                   FROM solves s JOIN problems p ON p.id = s.problem_db_id
                   WHERE s.discord_id = $1 AND s.guild_id = $2
               ), grp AS (
                   SELECT d, d - (ROW_NUMBER() OVER (ORDER BY d))::int AS anchor
                   FROM days
               )
               SELECT COUNT(*)::int FROM grp
               WHERE anchor = (SELECT anchor FROM grp ORDER BY d DESC LIMIT 1)""",
            did, gid,
        )

    return _json({
        "user": dict(user),
        "handles": _rows(handles),
        "ratings": _rows(ratings),
        "points": points["points"],
        "solved": points["solved"],
        "streak": streak or 0,
        "recent_solves": _rows(recent),
    })


async def match_history(request: web.Request) -> web.Response:
    """GET /api/duels?discord_id=...&mode=...&limit=25"""
    did = request.query.get("discord_id")
    mode = request.query.get("mode")
    limit = min(int(request.query.get("limit", 25)), 100)
    sql = """
        SELECT d.id, d.mode, d.player1_id, d.player2_id, d.is_bot_match,
               d.bot_rating, d.status, d.winner_id, d.p1_games_won,
               d.p2_games_won, d.total_games, d.duel_number,
               d.started_at, d.ended_at,
               u1.discord_username AS player1_name,
               u2.discord_username AS player2_name
        FROM duels d
        LEFT JOIN users u1 ON u1.discord_id = d.player1_id
        LEFT JOIN users u2 ON u2.discord_id = d.player2_id
        WHERE d.guild_id = $1
          AND ($2::text IS NULL OR d.player1_id = $2 OR d.player2_id = $2)
          AND ($3::text IS NULL OR d.mode = $3)
          AND d.status = 'finished'
        ORDER BY d.ended_at DESC NULLS LAST
        LIMIT $4
    """
    async with get_pool().acquire() as conn:
        recs = await conn.fetch(sql, _guild(request), did, mode, limit)
    return _json({"duels": _rows(recs)})


async def live_duels(request: web.Request) -> web.Response:
    """GET /api/duels/live — powers the 'happening now' strip on the site."""
    sql = """
        SELECT d.id, d.mode, d.duel_number, d.is_bot_match, d.started_at,
               d.current_game, d.total_games,
               u1.discord_username AS player1_name,
               u2.discord_username AS player2_name
        FROM duels d
        LEFT JOIN users u1 ON u1.discord_id = d.player1_id
        LEFT JOIN users u2 ON u2.discord_id = d.player2_id
        WHERE d.guild_id = $1 AND d.status = 'active'
        ORDER BY d.started_at DESC
    """
    async with get_pool().acquire() as conn:
        recs = await conn.fetch(sql, _guild(request))
    return _json({"live": _rows(recs)})


async def create_duel_api(request: web.Request) -> web.Response:
    """POST /api/duels/create
    Body: {"mode": "dsa_blitz", "player1_id": "...", "player2_id": "...", "is_bot_match": false, "total_games": 3}
    """
    try:
        body = await request.json()
        mode = body.get("mode", "dsa_blitz")
        p1_id = str(body.get("player1_id", ""))
        p2_id = str(body.get("player2_id", "")) if body.get("player2_id") else None
        is_bot = bool(body.get("is_bot_match", False))
        total_games = int(body.get("total_games", 3))
        gid = _guild(request)

        if not p1_id:
            return _json({"error": "player1_id is required"}, status=400)

        pool = get_pool()
        async with pool.acquire() as conn:
            p1_rating = await duel_queries.get_or_create_rating(conn, p1_id, gid, mode)
            p2_rating = await duel_queries.get_or_create_rating(conn, p2_id, gid, mode) if (p2_id and not is_bot) else {"rating": 800}

            used = await duel_queries.get_active_duel_numbers(conn, gid)
            duel_num = duel_queries.next_free_number(used)
            bot_rating = 800 if is_bot else None

            duel_id = await duel_queries.create_duel(
                conn, gid, mode, p1_id, p2_id if not is_bot else None,
                is_bot_match=is_bot, bot_rating=bot_rating,
                total_games=total_games, duel_number=duel_num
            )
            await duel_queries.activate_duel(conn, duel_id, "")

            platform = "cf" if (mode.startswith("cp") or mode.startswith("icpc")) else "lc"
            icpc = "icpc" in mode
            pkey = duel_queries.pair_key(p1_id, p2_id if not is_bot else None)
            pair_history = await duel_queries.get_pair_history(conn, pkey, platform)

            if platform == "cf":
                from cogs.duels import _base_rating, _cf_targets
                from platforms.duel_cf_pool import pick_cf_problems_at_ratings
                base = _base_rating(p1_rating["rating"], p2_rating["rating"])
                targets = _cf_targets(base, total_games, icpc)
                probs = await pick_cf_problems_at_ratings(targets, pair_history, icpc=icpc)
            else:
                from platforms.duel_lc_pool import pick_sequence
                probs = await pick_sequence(total_games, pair_history)

            deadlines = [
                dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=25)
                for _ in range(total_games)
            ]

            problem_rows = []
            for i, prob in enumerate(probs, 1):
                pid = await duel_queries.add_duel_problem(
                    conn, duel_id, i, prob["platform"], prob["problem_id"],
                    prob["title"], prob.get("difficulty", "Medium"),
                    prob.get("rating"), prob.get("url", ""), deadlines[i-1]
                )
                problem_rows.append({
                    "id": pid, "game_number": i, "platform": prob["platform"],
                    "problem_id": prob["problem_id"], "title": prob["title"],
                    "difficulty_label": prob.get("difficulty", "Medium"),
                    "rating": prob.get("rating"), "url": prob.get("url", "")
                })

        return _json({
            "duel_id": duel_id,
            "mode": mode,
            "status": "active",
            "duel_number": duel_num,
            "player1_id": p1_id,
            "player2_id": p2_id,
            "is_bot_match": is_bot,
            "problems": problem_rows,
        })
    except Exception as e:
        print(f"[api/duels/create] error: {e}")
        return _json({"error": str(e)}, status=500)


async def get_duel_state_api(request: web.Request) -> web.Response:
    """GET /api/duels/state/{id}"""
    try:
        duel_id = int(request.match_info["id"])
        async with get_pool().acquire() as conn:
            duel = await duel_queries.get_duel(conn, duel_id)
            if not duel:
                return _json({"error": "duel not found"}, status=404)
            cur_prob = await duel_queries.get_current_problem(conn, duel_id, duel["current_game"])
            probs = await conn.fetch("SELECT * FROM duel_problems WHERE duel_id = $1 ORDER BY game_number", duel_id)
        return _json({
            "duel": dict(duel),
            "current_problem": dict(cur_prob) if cur_prob else None,
            "all_problems": _rows(probs),
        })
    except Exception as e:
        return _json({"error": str(e)}, status=500)


async def verify_duel_submission_api(request: web.Request) -> web.Response:
    """POST /api/duels/verify
    Body: {"duel_id": 123, "discord_id": "..."}
    """
    try:
        body = await request.json()
        duel_id = int(body.get("duel_id", 0))
        discord_id = str(body.get("discord_id", ""))

        if not duel_id or not discord_id:
            return _json({"error": "duel_id and discord_id are required"}, status=400)

        pool = get_pool()
        async with pool.acquire() as conn:
            duel = await duel_queries.get_duel(conn, duel_id)
            if not duel or duel["status"] != "active":
                return _json({"error": "duel not active or not found"}, status=404)

            cur_game = duel["current_game"]
            prob = await duel_queries.get_current_problem(conn, duel_id, cur_game)
            if not prob:
                return _json({"error": "no active problem for current game"}, status=404)

            platform = prob["platform"]
            handle = await duel_queries.get_handle_for_user(conn, discord_id, platform)
            if not handle:
                return _json({"error": f"No handle linked for '{platform}' platform for user {discord_id}"}, status=400)

            started_at = duel["started_at"]
            since_ts = started_at.timestamp() if started_at else 0

            import platforms as P
            adapter = P.PLATFORMS.get(platform)
            if not adapter:
                return _json({"error": f"Unsupported platform {platform}"}, status=400)

            solved = await adapter.check_solved(handle, prob["problem_id"], since_ts, None)
            if not solved:
                return _json({
                    "verified": False,
                    "message": f"No accepted submission found on {platform.upper()} for handle '{handle}' since match start."
                })

            side = "p1" if discord_id == duel["player1_id"] else "p2"
            now = dt.datetime.now(dt.timezone.utc)
            await duel_queries.mark_solved(conn, prob["id"], side, now)
            await duel_queries.set_game_winner(conn, prob["id"], discord_id)
            await duel_queries.bump_game_score(conn, duel_id, side)

            updated_duel = await duel_queries.get_duel(conn, duel_id)
            p1_won = updated_duel["p1_games_won"]
            p2_won = updated_duel["p2_games_won"]
            total = updated_duel["total_games"]

            finished = False
            winner_id = None
            if p1_won > total / 2 or p2_won > total / 2 or (p1_won + p2_won) >= total:
                finished = True
                if p1_won > p2_won:
                    winner_id = updated_duel["player1_id"]
                elif p2_won > p1_won:
                    winner_id = updated_duel["player2_id"]
                await duel_queries.finish_duel(conn, duel_id, winner_id)

        if _bot_instance:
            import asyncio
            asyncio.create_task(_broadcast_discord_duel_update(duel_id, discord_id, prob["title"], finished, winner_id))

        return _json({
            "verified": True,
            "game_number": cur_game,
            "solved_by": discord_id,
            "finished": finished,
            "winner_id": winner_id,
            "p1_games_won": p1_won,
            "p2_games_won": p2_won,
        })
    except Exception as e:
        print(f"[api/duels/verify] error: {e}")
        return _json({"error": str(e)}, status=500)


async def _broadcast_discord_duel_update(duel_id: int, solver_id: str, prob_title: str, finished: bool, winner_id: str | None):
    if not _bot_instance:
        return
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            duel = await duel_queries.get_duel(conn, duel_id)
        if not duel or not duel.get("channel_id"):
            return
        ch = _bot_instance.get_channel(int(duel["channel_id"]))
        if not ch:
            return

        import discord
        if finished:
            win_txt = f"🏆 **Match Winner**: <@{winner_id}>!" if winner_id else "🤝 **Match Draw!**"
            em = discord.Embed(
                title=f"⚡ {prob_title} Solved!",
                description=f"<@{solver_id}> solved the problem!\n\n{win_txt}",
                color=0x57F287
            )
        else:
            em = discord.Embed(
                title=f"⚡ {prob_title} Solved!",
                description=f"<@{solver_id}> solved the problem and took the round!",
                color=0x00D9FF
            )
        await ch.send(embed=em)
    except Exception as e:
        print(f"[api/broadcast] Discord broadcast failed: {e}")


async def stats(request: web.Request) -> web.Response:
    """GET /api/stats — hero-section community counters."""
    gid = _guild(request)
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """SELECT
                 (SELECT COUNT(*) FROM users)::int                            AS members,
                 (SELECT COUNT(*) FROM problems WHERE guild_id = $1)::int     AS problems,
                 (SELECT COUNT(*) FROM solves   WHERE guild_id = $1)::int     AS solves,
                 (SELECT COUNT(*) FROM duels
                   WHERE guild_id = $1 AND status = 'finished')::int          AS duels,
                 (SELECT COUNT(*) FROM handles WHERE verified)::int           AS verified_handles""",
            gid,
        )
    return _json(dict(row))


async def announcements(request: web.Request) -> web.Response:
    """GET /api/announcements — reads bot_config keys prefixed
    `announcement:` so admins can post from Discord and it lands on the
    website with no deploy."""
    async with get_pool().acquire() as conn:
        recs = await conn.fetch(
            """SELECT key, value, updated_by, updated_at FROM bot_config
               WHERE key LIKE 'announcement:%' ORDER BY updated_at DESC LIMIT 50"""
        )
    return _json({"announcements": [
        {"id": r["key"].split(":", 1)[1], "body": r["value"],
         "author": r["updated_by"], "posted_at": r["updated_at"]}
        for r in recs
    ]})


async def internal_membership(request: web.Request) -> web.Response:
    """POST /api/internal/membership  {"discord_ids": [...]}
    The website calls this after Discord OAuth to check who the bot has
    actually seen in the guild. Key-protected."""
    _require_key(request)
    body = await request.json()
    ids = [str(x) for x in body.get("discord_ids", [])][:200]
    async with get_pool().acquire() as conn:
        recs = await conn.fetch(
            "SELECT discord_id FROM users WHERE discord_id = ANY($1::text[])", ids
        )
    known = {r["discord_id"] for r in recs}
    return _json({"known": sorted(known),
                  "unknown": sorted(set(ids) - known)})



# ── Discord channel mirror (Phase 2) ────────────────────────────────────────

async def channel_messages(request: web.Request) -> web.Response:
    """GET /api/channels/{key}/messages?limit=50&before=<iso>&pinned=1&q=text

    Reads the Postgres mirror written by cogs/website_sync.py, never Discord.
    Keeps the website fast and keeps the bot inside Discord's rate limits.
    """
    key = request.match_info["key"]
    if key not in set(config.SYNCED_CHANNELS.values()):
        return _json({"error": "unknown channel key"}, status=404)

    limit = min(int(request.query.get("limit", 50)), 200)
    before = request.query.get("before")
    pinned_only = request.query.get("pinned") == "1"
    search = request.query.get("q")
    # Top-level only by default; editorial threads are fetched per-thread.
    include_threads = request.query.get("threads") == "1"

    sql = """
        SELECT message_id, channel_id, channel_key, author_id, author_name,
               author_avatar, author_is_bot, content, embeds, attachments,
               thread_id, thread_name, is_pinned, reply_to_id,
               created_at, edited_at
        FROM discord_messages
        WHERE channel_key = $1
          AND ($2::timestamptz IS NULL OR created_at < $2)
          AND ($3::boolean IS FALSE OR is_pinned)
          AND ($4::text IS NULL OR content ILIKE '%' || $4 || '%')
          AND ($5::boolean IS TRUE OR thread_id IS NULL)
        ORDER BY created_at DESC
        LIMIT $6
    """
    async with get_pool().acquire() as conn:
        recs = await conn.fetch(
            sql, key,
            dt.datetime.fromisoformat(before) if before else None,
            pinned_only, search, include_threads, limit,
        )

    messages = []
    for r in recs:
        m = dict(r)
        # asyncpg returns jsonb as text; decode so the client gets objects.
        for f in ("embeds", "attachments"):
            if isinstance(m.get(f), str):
                m[f] = json.loads(m[f])
        messages.append(m)

    return _json({
        "channel_key": key,
        "messages": messages,
        "next_before": messages[-1]["created_at"] if len(messages) == limit else None,
    })


async def channel_threads(request: web.Request) -> web.Response:
    """GET /api/channels/{key}/threads — thread index for a channel.

    daily-editorials uses one thread per day, so this is what drives the
    'Editorial Available' / 'Coming Soon' state next to each daily problem.
    """
    key = request.match_info["key"]
    limit = min(int(request.query.get("limit", 100)), 300)
    async with get_pool().acquire() as conn:
        recs = await conn.fetch(
            """SELECT thread_id, parent_id, channel_key, name, editorial_date,
                      message_count, has_pdf, is_archived, created_at
               FROM discord_threads
               WHERE channel_key = $1
               ORDER BY COALESCE(editorial_date, created_at::date) DESC
               LIMIT $2""",
            key, limit,
        )
    return _json({"channel_key": key, "threads": _rows(recs)})


async def thread_messages(request: web.Request) -> web.Response:
    """GET /api/threads/{thread_id}/messages — one editorial / article thread."""
    tid = request.match_info["thread_id"]
    async with get_pool().acquire() as conn:
        recs = await conn.fetch(
            """SELECT message_id, author_name, author_avatar, author_is_bot,
                      content, embeds, attachments, created_at, edited_at
               FROM discord_messages
               WHERE thread_id = $1
               ORDER BY created_at ASC""",
            tid,
        )
    out = []
    for r in recs:
        m = dict(r)
        for f in ("embeds", "attachments"):
            if isinstance(m.get(f), str):
                m[f] = json.loads(m[f])
        out.append(m)
    return _json({"thread_id": tid, "messages": out})


async def editorial_for_date(request: web.Request) -> web.Response:
    """GET /api/editorials/{date}  (YYYY-MM-DD)

    Returns one of three states so the website can follow the spec exactly:
    no thread -> hide the editorial section; thread but no PDF -> "coming
    soon"; thread with PDF -> "available" plus the attachment.
    """
    try:
        day = dt.date.fromisoformat(request.match_info["date"])
    except ValueError:
        return _json({"error": "date must be YYYY-MM-DD"}, status=400)

    async with get_pool().acquire() as conn:
        thread = await conn.fetchrow(
            """SELECT thread_id, name, has_pdf, message_count, created_at
               FROM discord_threads
               WHERE channel_key = 'daily_editorials' AND editorial_date = $1
               LIMIT 1""",
            day,
        )
        if thread is None:
            return _json({"date": day, "status": "none"})

        pdfs = await conn.fetch(
            """SELECT attachments FROM discord_messages
               WHERE thread_id = $1 AND attachments::text ILIKE '%"is_pdf": true%'
               ORDER BY created_at ASC""",
            thread["thread_id"],
        )

    files = []
    for row in pdfs:
        raw = row["attachments"]
        for a in (json.loads(raw) if isinstance(raw, str) else raw):
            if a.get("is_pdf"):
                files.append(a)

    return _json({
        "date": day,
        "status": "available" if files else "coming_soon",
        "thread": dict(thread),
        "files": files,
    })


async def guild_stats(request: web.Request) -> web.Response:
    """GET /api/guild — live member counts for the community page."""
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM guild_snapshot WHERE guild_id = $1", _guild(request)
        )
    if row is None:
        return _json({"error": "no snapshot yet — is the bot running?"}, status=404)
    return _json(dict(row))


async def channel_index(request: web.Request) -> web.Response:
    """GET /api/channels — which channels are mirrored and how fresh each is."""
    async with get_pool().acquire() as conn:
        recs = await conn.fetch(
            """SELECT channel_key, COUNT(*)::int AS message_count,
                      MAX(created_at) AS latest_at, MAX(synced_at) AS synced_at
               FROM discord_messages GROUP BY channel_key"""
        )
    have = {r["channel_key"]: dict(r) for r in recs}
    return _json({"channels": [
        have.get(key, {"channel_key": key, "message_count": 0,
                       "latest_at": None, "synced_at": None})
        for key in sorted(set(config.SYNCED_CHANNELS.values()))
    ]})


# ─────────────────────────────  contests (live API fetch)  ─────────

# In-memory cache: fetch from platforms at most once per hour.
_contests_cache: dict = {"data": [], "fetched_at": 0}
_CONTESTS_TTL = 3600  # 1 hour

async def upcoming_contests(request: web.Request) -> web.Response:
    """GET /api/contests — upcoming contests from CF, LC, CC, AtCoder.

    Reuses the same fetch functions from cogs/contests.py.
    Caches for 1 hour to avoid rate limits.
    """
    import time
    now = time.time()
    if now - _contests_cache["fetched_at"] < _CONTESTS_TTL and _contests_cache["data"]:
        return _json({"contests": _contests_cache["data"], "cached": True})

    try:
        from cogs.contests import fetch_all_contests
        contests = await fetch_all_contests()
        # Serialize for JSON
        result = []
        for c in contests:
            result.append({
                "platform": c["key"],
                "id": c["id"],
                "name": c["name"],
                "start_ts": c["start_ts"],
                "duration": c["duration"],
                "url": c["url"],
                "start_iso": dt.datetime.fromtimestamp(
                    c["start_ts"], tz=dt.timezone.utc
                ).isoformat(),
            })
        _contests_cache["data"] = result
        _contests_cache["fetched_at"] = now
        return _json({"contests": result, "cached": False})
    except Exception as e:
        print(f"[api/contests] fetch error: {e}", flush=True)
        # Return stale cache if available
        if _contests_cache["data"]:
            return _json({"contests": _contests_cache["data"], "cached": True, "stale": True})
        return _json({"contests": [], "error": str(e)}, status=502)


# ─────────────────────────────  wiring  ─────────────────────────────

_bot_instance = None

async def check_submissions(request: web.Request) -> web.Response:
    """POST /api/problems/check
    Checks today's daily problems solves for the given user, awarding points."""
    body = await request.json()
    discord_id = body.get("discord_id")
    if not discord_id:
        return _json({"error": "missing discord_id"}, status=400)

    global _bot_instance
    if not _bot_instance:
        return _json({"error": "bot not running"}, status=503)

    checker_cog = _bot_instance.get_cog("Checker")
    if not checker_cog:
        return _json({"error": "checker cog not loaded"}, status=503)

    pool = get_pool()
    today = queries.today_ist()
    gid = _guild(request)

    async with pool.acquire() as conn:
        week = await queries.get_active_week(conn, gid)
        if not week:
            return _json({"error": "no active week configured"}, status=400)
        probs = await queries.get_problems_for_day(conn, gid, today)

    if not probs:
        return _json({"message": "no problems assigned for today"}, status=200)

    # Sort problems identically to checker cog
    DIFF_ORDER = {"easy": 0, "medium": 1, "hard": 2, "expert": 3, "master": 4}
    probs = sorted(probs, key=lambda p: (DIFF_ORDER.get(p["difficulty"], 99), p["id"]))

    class MockMember:
        def __init__(self, id_str):
            self.id = int(id_str)
            self.display_name = f"User {id_str}"
            class Avatar:
                url = ""
            self.display_avatar = Avatar()

    member = MockMember(discord_id)

    try:
        results, total_earned = await checker_cog._check_member(
            member, probs, gid, delay=0.5
        )
        clean_results = [r.replace("`", "").replace("**", "") for r in results if r]
        return _json({
            "success": True,
            "results": clean_results,
            "earned": total_earned
        })
    except Exception as e:
        print(f"[api/check] check failed for {discord_id}: {e}")
        return _json({"error": str(e)}, status=500)


async def get_hardtests(request: web.Request) -> web.Response:
    """GET /api/internal/hardtests/{pid}"""
    _require_key(request)
    pid = request.match_info["pid"]
    try:
        import aiohttp
        import pickle
        import zlib
        import base64
        import urllib.parse

        where = f'"pid"=\'{pid}\''
        url = (
            f"https://datasets-server.huggingface.co/filter"
            f"?dataset=sigcp/hardtests_tests&config=default&split=train"
            f"&where={urllib.parse.quote(where)}&length=1"
        )

        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=15) as resp:
                if resp.status != 200:
                    return _json({"error": f"HF response code {resp.status}"}, status=resp.status)
                data = await resp.json()

        if "rows" not in data or len(data["rows"]) == 0:
            return _json({"error": "problem not found in HARDTESTS Tests"}, status=404)

        row = data["rows"][0]["row"]
        encoded_tcs = row.get("test_cases")
        if not encoded_tcs:
            return _json({"error": "test_cases column missing or empty"}, status=404)

        decoded_bytes = base64.b64decode(encoded_tcs.encode("utf-8"))
        decompressed = zlib.decompress(decoded_bytes)
        tcs = pickle.loads(decompressed)
        if isinstance(tcs, str):
            tcs = json.loads(tcs)

        formatted = []
        for tc in tcs:
            formatted.append({
                "input": str(tc.get("input", "")),
                "output": str(tc.get("output", ""))
            })

        return _json({"pid": pid, "tests": formatted})
    except Exception as e:
        print(f"[api/hardtests] failed for {pid}: {e}")
        return _json({"error": str(e)}, status=500)


def build_app() -> web.Application:
    app = web.Application(middlewares=[_cors])
    r = app.router
    r.add_get("/", health)
    r.add_get("/health", health)
    r.add_get("/api/stats", stats)
    r.add_get("/api/modes", modes)
    r.add_get("/api/problems", daily_problems)
    r.add_get("/api/problems/{id}/solvers", problem_solvers)
    r.add_get("/api/leaderboard/points", leaderboard_points)
    r.add_get("/api/leaderboard/rating", leaderboard_rating)
    r.add_get("/api/users/{discord_id}", profile)
    r.add_get("/api/duels", match_history)
    r.add_get("/api/duels/live", live_duels)
    r.add_post("/api/duels/create", create_duel_api)
    r.add_get("/api/duels/state/{id}", get_duel_state_api)
    r.add_post("/api/duels/verify", verify_duel_submission_api)
    r.add_get("/api/announcements", announcements)
    r.add_get("/api/channels", channel_index)
    r.add_get("/api/channels/{key}/messages", channel_messages)
    r.add_get("/api/channels/{key}/threads", channel_threads)
    r.add_get("/api/threads/{thread_id}/messages", thread_messages)
    r.add_get("/api/editorials/{date}", editorial_for_date)
    r.add_get("/api/guild", guild_stats)
    r.add_get("/api/contests", upcoming_contests)
    r.add_get("/api/internal/hardtests/{pid}", get_hardtests)
    r.add_post("/api/problems/check", check_submissions)
    r.add_post("/api/internal/membership", internal_membership)
    r.add_route("OPTIONS", "/{tail:.*}", lambda req: web.Response(status=204))
    return app


async def run_server(port: int, bot=None):
    """Drop-in replacement for keep_alive.run_server."""
    global _bot_instance
    _bot_instance = bot
    runner = web.AppRunner(build_app())
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    print(f"[api] Binary Beats API listening on :{port}")
