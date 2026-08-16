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
import asyncio
import datetime as dt
import discord
import aiohttp
from aiohttp import web

from database.connection import get_pool, ping_db
from database import queries, duel_queries
import config

GUILD_ID = os.getenv("GUILD_ID", "")
BB_API_KEY = os.getenv("BB_API_KEY", "")
ALLOWED_ORIGINS = [o.strip() for o in os.getenv(
    "BB_ALLOWED_ORIGINS",
    "http://localhost:5173,https://binarybeats.in,https://www.binarybeats.in,https://binarybeats.vercel.app",
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


_CUSTOM_STATS = {
    "team_members": 11,
    "contests_held": 2,
    "linkedin_followers": 273,
}

_REGISTERED_CONTESTS = [
    {
        "id": "bb-contest-1",
        "title": "Binary Beats Grand Contest 1",
        "url": "https://codeforces.com/contests",
        "platform": "Codeforces",
        "start_time": "2026-08-20T18:00:00Z",
        "status": "UPCOMING",
    },
    {
        "id": "bb-contest-2",
        "title": "Binary Beats ICPC Practice Gym",
        "url": "https://codeforces.com/gyms",
        "platform": "Codeforces",
        "start_time": "2026-08-25T17:00:00Z",
        "status": "UPCOMING",
    }
]


async def stats(request: web.Request) -> web.Response:
    """GET /api/stats — Returns live Discord member count, team count, contests held, and LinkedIn followers."""
    discord_members = 250
    if _bot_instance and _bot_instance.guilds:
        discord_members = sum(g.member_count or 0 for g in _bot_instance.guilds)

    return _json({
        "discord_members": discord_members,
        "team_members": _CUSTOM_STATS["team_members"],
        "contests_held": _CUSTOM_STATS["contests_held"],
        "linkedin_followers": _CUSTOM_STATS["linkedin_followers"],
    })


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
        await duel_queries.cleanup_duplicate_user_ratings(conn)
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
            bot_rating = p1_rating["rating"] if is_bot else None

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
                prob_platform = prob.get("platform") or platform
                pid = await duel_queries.add_duel_problem(
                    conn, duel_id, i, prob_platform, prob["problem_id"],
                    prob["title"], prob.get("difficulty", "Medium"),
                    prob.get("rating"), prob.get("url", ""), deadlines[i-1]
                )
                problem_rows.append({
                    "id": pid, "game_number": i, "platform": prob_platform,
                    "problem_id": prob["problem_id"], "title": prob["title"],
                    "difficulty_label": prob.get("difficulty", "Medium"),
                    "rating": prob.get("rating"), "url": prob.get("url", "")
                })

        if _bot_instance:
            import asyncio
            asyncio.create_task(_broadcast_discord_duel_start(duel_id, p1_id, p2_id if not is_bot else None, mode, problem_rows))

        return _json({
            "duel_id": duel_id,
            "mode": mode,
            "status": "active",
            "duel_number": duel_num,
            "player1_id": p1_id,
            "player2_id": p2_id,
            "is_bot_match": is_bot,
            "p1_rating": p1_rating["rating"],
            "p2_rating": p2_rating["rating"],
            "ratings": {
                p1_id: p1_rating["rating"],
                (p2_id or "bot"): p2_rating["rating"],
            },
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
            gid = _guild(request)
            p1_id = duel["player1_id"]
            p2_id = duel["player2_id"]
            mode = duel["mode"]
            p1_row = await duel_queries.get_or_create_rating(conn, p1_id, gid, mode)
            p2_row = await duel_queries.get_or_create_rating(conn, p2_id, gid, mode) if p2_id else {"rating": duel.get("bot_rating") or p1_row["rating"]}
            cur_prob = await duel_queries.get_current_problem(conn, duel_id, duel["current_game"])
            probs = await conn.fetch("SELECT * FROM duel_problems WHERE duel_id = $1 ORDER BY game_number", duel_id)
        return _json({
            "duel": dict(duel),
            "ratings": {
                p1_id: p1_row["rating"],
                (p2_id or "bot"): p2_row["rating"],
            },
            "p1_rating": p1_row["rating"],
            "p2_rating": p2_row["rating"],
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

                # Calculate & apply Elo / rating deltas + W/L/D stats
                mode = updated_duel["mode"]
                p1_id = updated_duel["player1_id"]
                p2_id = updated_duel["player2_id"]
                is_bot = updated_duel.get("is_bot_match", False)

                if winner_id == p1_id:
                    r1, r2 = "win", "loss"
                    d1, d2 = 25, -15
                elif winner_id == p2_id:
                    r1, r2 = "loss", "win"
                    d1, d2 = -15, 25
                else:
                    r1, r2 = "draw", "draw"
                    d1, d2 = 0, 0

                await duel_queries.apply_rating_delta(conn, p1_id, gid, mode, d1, r1, is_bot)
                if p2_id:
                    await duel_queries.apply_rating_delta(conn, p2_id, gid, mode, d2, r2, is_bot)

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


_duel_discord_rooms: dict[int, dict] = {}

_webhook_cache_api: dict[int, discord.Webhook] = {}

async def _get_webhook_api(channel) -> discord.Webhook | None:
    if channel.id in _webhook_cache_api:
        return _webhook_cache_api[channel.id]
    try:
        webhooks = await channel.webhooks()
        for wh in webhooks:
            if wh.name == "Z4s":
                _webhook_cache_api[channel.id] = wh
                return wh
        wh = await channel.create_webhook(name="Z4s")
        _webhook_cache_api[channel.id] = wh
        return wh
    except Exception as e:
        print(f"[api/webhook] Failed to get/create webhook: {e}", flush=True)
        return None

async def _send_branded_api(channel, content=None, embed=None, **kwargs):
    wh = await _get_webhook_api(channel)
    if wh:
        try:
            return await wh.send(
                content=content, embed=embed,
                username="Z4s",
                avatar_url="https://raw.githubusercontent.com/ashaygupta-cc/ashaygupta-cc/main/Zodiac_Z408.png",
                wait=True, **kwargs)
        except Exception as e:
            print(f"[api/webhook] Send failed, fallback: {e}", flush=True)
    return await channel.send(content=content, embed=embed, **kwargs)


async def _broadcast_discord_duel_start(duel_id: int, p1_id: str, p2_id: str | None, mode: str, problems: list[dict]):
    if not _bot_instance:
        return
    try:
        import discord
        mode_title = mode.replace("_", " ").upper()
        prob_lines = []
        for p in problems:
            g_num = p.get("game_number", 1)
            title = p.get("title", "Problem")
            url = p.get("url", "")
            diff = p.get("difficulty_label", "Medium")
            prob_lines.append(f"• **Game {g_num}**: [{title}]({url}) `[{diff}]`")

        mode_channel = mode.replace("_", "-")
        channel_names = [mode_channel, mode, "duels-blitz", "cp-dsa", "general"]
        public_channel = None
        guild = None

        for g in _bot_instance.guilds:
            for ch in g.text_channels:
                if ch.name.lower() in channel_names:
                    public_channel = ch
                    guild = g
                    break
            if public_channel:
                break

        if not public_channel:
            for g in _bot_instance.guilds:
                if g.text_channels:
                    public_channel = g.text_channels[0]
                    guild = g
                    break

        if not guild or not public_channel:
            return

        def find_member(user_str: str | None):
            if not user_str:
                return None
            for m in guild.members:
                if str(m.id) == user_str or m.name.lower() == user_str.lower() or (m.global_name and m.global_name.lower() == user_str.lower()):
                    return m
            return None

        m1 = find_member(p1_id)
        m2 = find_member(p2_id) if p2_id else None

        cat_name = "Duels"
        category = discord.utils.get(guild.categories, name=cat_name)
        if not category:
            try:
                category = await guild.create_category(cat_name)
            except Exception:
                category = None

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=True,
                read_message_history=False,
                send_messages=False,
                add_reactions=False,
                manage_channels=False,
                manage_messages=False,
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                read_messages=True,
                send_messages=True,
                embed_links=True,
                attach_files=True,
                manage_messages=True,
                manage_channels=True,
            ),
        }
        if m1:
            overwrites[m1] = discord.PermissionOverwrite(
                view_channel=True, read_messages=True, send_messages=True,
                read_message_history=True, add_reactions=True,
                manage_channels=False, manage_messages=False
            )
        if m2:
            overwrites[m2] = discord.PermissionOverwrite(
                view_channel=True, read_messages=True, send_messages=True,
                read_message_history=True, add_reactions=True,
                manage_channels=False, manage_messages=False
            )

        private_channel = None
        try:
            family = "dsa" if "dsa" in mode else ("icpc" if "icpc" in mode else "cp")
            kind = "duel" if mode.endswith("_duel") else "blitz"
            p1_tag = m1.name if m1 else p1_id
            p2_tag = m2.name if m2 else (p2_id or "Z4s")
            chan_name = f"{family}-{kind}-{duel_id}-{p1_tag}-vs-{p2_tag}".lower().replace(" ", "-")[:100]
            private_channel = await guild.create_text_channel(
                name=chan_name,
                category=category,
                overwrites=overwrites,
                reason=f"Binary Beats Match #{duel_id}"
            )
        except Exception as e:
            print(f"[api/broadcast] Could not create private match channel: {e}")

        pub_em = discord.Embed(title="__Match room is live.__", color=0x57F287)
        if private_channel:
            pub_em.description = f"→ {private_channel.mention}"
        else:
            pub_em.description = f"→ Web Arena"
        pub_em.set_footer(text="Tap the channel to jump straight in.", icon_url="https://raw.githubusercontent.com/ashaygupta-cc/ashaygupta-cc/main/Binary%20Beats.webp")

        pub_msg = await _send_branded_api(public_channel, embed=pub_em)

        _duel_discord_rooms[duel_id] = {
            "public_channel_id": public_channel.id,
            "broadcast_msg_id": pub_msg.id if pub_msg else None,
            "private_channel_id": private_channel.id if private_channel else None,
        }

        async with get_pool().acquire() as conn:
            chan_to_save = private_channel.id if private_channel else public_channel.id
            await conn.execute("UPDATE duels SET channel_id = $1 WHERE id = $2", str(chan_to_save), duel_id)

        if private_channel:
            p1_title = problems[0].get("title", "Problem 1") if problems else "Problem 1"
            p1_url = problems[0].get("url", "") if problems else ""
            p1_diff = problems[0].get("difficulty_label", "Medium") if problems else "Medium"

            p1_tag = m1.display_name if m1 else p1_id
            p2_tag = m2.display_name if m2 else (p2_id or "Z4s")

            async with get_pool().acquire() as conn:
                p1_r = await duel_queries.get_or_create_rating(conn, p1_id, str(guild.id), mode)
                p2_r = await duel_queries.get_or_create_rating(conn, p2_id, str(guild.id), mode) if p2_id else {"rating": 800}

            import duel_ranks
            p1_rank = duel_ranks.get_rank(p1_r["rating"])
            p2_rank = duel_ranks.get_rank(p2_r["rating"])

            lines = [
                p1_tag,
                f"{p1_rank['name']} · {p1_r['rating']}",
                "",
                "VS",
                "",
                f"{p2_rank['name']} · {p2_r['rating']}",
                p2_tag,
            ]
            w = max(len(l) for l in lines) + 4
            vs_box = "```\n" + "\n".join(l.center(w) for l in lines) + "\n```"

            tot_probs = len(problems) if problems else 3
            mode_label = "LC Blitz" if mode == "dsa_blitz" else ("LC Duel" if mode == "dsa_duel" else ("CF Blitz" if mode == "cp_blitz" else "CF Duel"))
            mode_blurb = "LeetCode speed race" if "dsa" in mode else "Codeforces speed race"

            room_em = discord.Embed(
                title=f"__{mode_label} — Match #{duel_id}__",
                description=(
                    f"**{mode_blurb}** · **{tot_probs}** problems, one at a time\n\n"
                    f"{vs_box}\n\n"
                    f"First verified solve takes each problem.\n"
                    f"Timer expires → draw → next problem."
                ),
                color=0x00D9FF
            )
            room_em.set_author(name="Binary Beats", icon_url="https://raw.githubusercontent.com/ashaygupta-cc/ashaygupta-cc/main/Binary%20Beats.webp")
            room_em.set_image(url="https://raw.githubusercontent.com/ashaygupta-cc/ashaygupta-cc/main/Binary%20Beats%20Banner.jpeg")
            room_em.add_field(name=f"__Problem 1/{tot_probs}__", value=f"[{p1_title}]({p1_url})", inline=True)
            room_em.add_field(name="__Difficulty__", value=f"`{p1_diff}`", inline=True)
            room_em.add_field(name="__Time Limit__", value="`25 min`", inline=True)
            room_em.set_footer(text="First verified solve takes this problem · Forfeit costs 16 rating", icon_url="https://raw.githubusercontent.com/ashaygupta-cc/ashaygupta-cc/main/Binary%20Beats.webp")

            CD_ART = {
                7: "███████\n     ██\n    ██\n   ██\n  ██",
                6: " █████\n██\n██████\n██  ██\n █████",
                5: "██████\n██\n█████\n    ██\n█████",
                4: "██  ██\n██  ██\n██████\n    ██\n    ██",
                3: "█████\n   ██\n ████\n   ██\n█████",
                2: " █████\n    ██\n ████\n██\n██████",
                1: "  ██\n ███\n  ██\n  ██\n██████",
                0: " ████  ████  ██\n██    ██  ██ ██\n██ ██ ██  ██ ██\n██  █ ██  ██   ",
            }

            def format_cd(sec: int) -> str:
                art = CD_ART.get(sec, CD_ART[0])
                art_lines = art.split("\n")
                gw = max(len(l) for l in art_lines)
                padded = [l.ljust(gw) for l in art_lines]
                pad_w = max(gw + 6, 18)
                centered = "\n".join(l.center(pad_w) for l in padded)
                return f"```ansi\n\x1b[1;36m{centered}\x1b[0m\n```"

            cd_msg = await private_channel.send(format_cd(7))
            await private_channel.send(embed=room_em)
            
            p1_mention = m1.mention if m1 else f"`{p1_id}`"
            p2_mention = m2.mention if m2 else f"`{p2_id or 'Z4s'}`"
            await _send_branded_api(private_channel, content=f"{p1_mention} {p2_mention} — the arena is live. Good luck.")

            async def animate_cd():
                try:
                    for i in range(6, 0, -1):
                        await asyncio.sleep(1)
                        await cd_msg.edit(content=format_cd(i))
                    await asyncio.sleep(1)
                    await cd_msg.edit(content=format_cd(0))
                    await asyncio.sleep(2)
                    await cd_msg.delete()
                except Exception:
                    pass

            asyncio.create_task(animate_cd())

    except Exception as e:
        print(f"[api/broadcast] Match start broadcast error: {e}")


async def _broadcast_discord_duel_update(duel_id: int, solver_id: str, prob_title: str, finished: bool, winner_id: str | None):
    if not _bot_instance:
        return
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            duel = await duel_queries.get_duel(conn, duel_id)
        if not duel:
            return

        import discord
        import asyncio
        import duel_ranks
        from datetime import datetime, timedelta, timezone
        IST = timezone(timedelta(hours=5, minutes=30))

        p1 = duel["player1_id"]
        p2 = duel["player2_id"] or "Z4s"
        p1_won = duel["p1_games_won"]
        p2_won = duel["p2_games_won"]
        mode = duel["mode"]
        tot_games = duel.get("total_games", 3)

        info = _duel_discord_rooms.get(duel_id, {})
        pub_chan_id = info.get("public_channel_id")
        pub_msg_id = info.get("broadcast_msg_id")
        priv_chan_id = info.get("private_channel_id")

        public_channel = _bot_instance.get_channel(pub_chan_id) if pub_chan_id else None
        private_channel = _bot_instance.get_channel(priv_chan_id) if priv_chan_id else None

        score_str = f"{p1_won}-{p2_won}"
        winner_name = p1 if winner_id == p1 else (p2 if winner_id == p2 else ("Draw" if finished else None))

        gid = str(public_channel.guild.id) if public_channel else ""
        async with pool.acquire() as conn:
            p1_r = await duel_queries.get_or_create_rating(conn, p1, gid, mode)
            p2_r = await duel_queries.get_or_create_rating(conn, duel["player2_id"], gid, mode) if duel["player2_id"] else {"rating": duel.get("bot_rating") or 800}

        p1_rank = duel_ranks.get_rank(p1_r["rating"])

        if public_channel and pub_msg_id:
            try:
                pub_msg = await public_channel.fetch_message(pub_msg_id)
                if finished:
                    is_forfeit = prob_title == "Match Forfeited"
                    s_title = "__Match Result (Forfeit)__" if is_forfeit else "__Match Result__"
                    s_color = 0xED4245 if is_forfeit else 0xFEE75C

                    result_line = f"**{winner_name}** wins {score_str}" if winner_name != "Draw" else f"Draw — {score_str}"
                    if is_forfeit:
                        result_line += f"\n*{solver_id} forfeited*"

                    updated_em = discord.Embed(
                        title=s_title,
                        description=f"Mode: `{mode}` · Match #{duel.get('duel_number', duel_id)} · {tot_games}-Problem\n\n{result_line}",
                        color=s_color
                    )

                    p1_delta = -16 if (is_forfeit and solver_id == p1) else 16
                    p1_arrow = "📈" if p1_delta >= 0 else "📉"
                    updated_em.add_field(
                        name=f"{p1_arrow} {p1}",
                        value=f"`{p1_r['rating']}` → `{p1_r['rating'] + p1_delta}` **({p1_delta:+d})**",
                        inline=True
                    )
                    if duel["player2_id"]:
                        p2_delta = 16 if (is_forfeit and solver_id == p1) else -16
                        p2_arrow = "📈" if p2_delta >= 0 else "📉"
                        updated_em.add_field(
                            name=f"{p2_arrow} {p2}",
                            value=f"`{p2_r['rating']}` → `{p2_r['rating'] + p2_delta}` **({p2_delta:+d})**",
                            inline=True
                        )

                    updated_em.add_field(name="__Details__", value=f"{p1}: **{p1_rank['name']}**", inline=False)
                    updated_em.set_author(name="Binary Beats", icon_url="https://raw.githubusercontent.com/ashaygupta-cc/ashaygupta-cc/main/Binary%20Beats.webp")
                    updated_em.set_image(url="https://raw.githubusercontent.com/ashaygupta-cc/ashaygupta-cc/main/Binary%20Beats%20Banner.jpeg")
                    updated_em.set_footer(text=f"Completed at {datetime.now(IST).strftime('%H:%M:%S IST')}", icon_url="https://raw.githubusercontent.com/ashaygupta-cc/ashaygupta-cc/main/Binary%20Beats.webp")
                    await pub_msg.edit(embed=updated_em)
                else:
                    updated_em = pub_msg.embeds[0]
                    updated_em.description = (
                        f"LIVE UPDATE — Match #{duel_id}\n\n"
                        f"`{solver_id}` solved **{prob_title}**!\n\n"
                        f"Scoreboard: `{p1}` ({p1_won}) — ({p2_won}) `{p2}`"
                    )
                    await pub_msg.edit(embed=updated_em)
            except Exception as e:
                print(f"[api/broadcast] Could not edit public broadcast embed: {e}")

        if private_channel:
            if finished:
                win_txt = f"**{winner_name}** wins {score_str}" if winner_name != "Draw" else f"Draw — {score_str}"
                final_em = discord.Embed(
                    title="__Match Complete__",
                    description=f"{win_txt}",
                    color=0x57F287 if winner_name == p1 else 0xED4245
                )
                p1_delta = -16 if (prob_title == "Match Forfeited" and solver_id == p1) else 16
                final_em.add_field(
                    name=f"__{p1}__",
                    value=f"`{p1_r['rating']}` → `{p1_r['rating'] + p1_delta}` ({p1_delta:+d})",
                    inline=True
                )
                if duel["player2_id"]:
                    p2_delta = 16 if (prob_title == "Match Forfeited" and solver_id == p1) else -16
                    final_em.add_field(
                        name=f"__{p2}__",
                        value=f"`{p2_r['rating']}` → `{p2_r['rating'] + p2_delta}` ({p2_delta:+d})",
                        inline=True
                    )
                final_em.set_footer(text="Posting stats to the public channel… room closes in 10s", icon_url="https://raw.githubusercontent.com/ashaygupta-cc/ashaygupta-cc/main/Binary%20Beats.webp")
                await _send_branded_api(private_channel, embed=final_em)
                await asyncio.sleep(10)
                try:
                    await private_channel.delete(reason="Duel finished — auto-destruct")
                except Exception as e:
                    print(f"[api/broadcast] Could not delete private channel: {e}")
            else:
                solv_em = discord.Embed(
                    title=f"__Problem Solved: {prob_title}__",
                    description=f"`{solver_id}` solved the problem and won the round!\n\nScoreboard: `{p1}` ({p1_won}) — ({p2_won}) `{p2}`",
                    color=0x00D9FF
                )
                await _send_branded_api(private_channel, embed=solv_em)

    except Exception as e:
        print(f"[api/broadcast] Discord broadcast update failed: {e}")


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


# ─────────────────────────────  contests (instant cached API)  ─────────

_contests_cache: dict = {"data": [], "fetched_at": 0, "is_fetching": False}

def _get_initial_contest_fallbacks() -> list[dict]:
    import time
    now = time.time()
    return [
        {
            "platform": "cf",
            "id": "cf-div2-upcoming",
            "name": "Codeforces Round (Div. 2)",
            "start_ts": now + 86400 * 2,
            "duration": 7200,
            "url": "https://codeforces.com/contests",
            "start_iso": dt.datetime.fromtimestamp(now + 86400 * 2, tz=dt.timezone.utc).isoformat(),
        },
        {
            "platform": "lc",
            "id": "lc-weekly-upcoming",
            "name": "LeetCode Weekly Contest",
            "start_ts": now + 86400 * 5,
            "duration": 5400,
            "url": "https://leetcode.com/contest/",
            "start_iso": dt.datetime.fromtimestamp(now + 86400 * 5, tz=dt.timezone.utc).isoformat(),
        },
        {
            "platform": "atcoder",
            "id": "abc-upcoming",
            "name": "AtCoder Beginner Contest",
            "start_ts": now + 86400 * 6,
            "duration": 6000,
            "url": "https://atcoder.jp/contests/",
            "start_iso": dt.datetime.fromtimestamp(now + 86400 * 6, tz=dt.timezone.utc).isoformat(),
        },
        {
            "platform": "cc",
            "id": "cc-starters-upcoming",
            "name": "CodeChef Starters",
            "start_ts": now + 86400 * 3,
            "duration": 7200,
            "url": "https://www.codechef.com/contests",
            "start_iso": dt.datetime.fromtimestamp(now + 86400 * 3, tz=dt.timezone.utc).isoformat(),
        },
    ]


async def _bg_refresh_contests():
    if _contests_cache["is_fetching"]:
        return
    _contests_cache["is_fetching"] = True
    try:
        import time
        from cogs.contests import fetch_all_contests
        contests = await fetch_all_contests()
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
        if result:
            _contests_cache["data"] = result
            _contests_cache["fetched_at"] = time.time()
            print(f"[api/contests] Background fetch success → {len(result)} upcoming contests cached.", flush=True)
    except Exception as e:
        print(f"[api/contests] Background fetch error: {e}", flush=True)
    finally:
        _contests_cache["is_fetching"] = False


async def upcoming_contests(request: web.Request) -> web.Response:
    """GET /api/contests — Instant 0ms response from memory cache.
    Triggers asynchronous background refresh if cache is older than 30 mins.
    """
    import time
    import asyncio
    now = time.time()

    if not _contests_cache["data"] or (now - _contests_cache["fetched_at"] > 1800):
        asyncio.create_task(_bg_refresh_contests())

    data = _contests_cache["data"] if _contests_cache["data"] else _get_initial_contest_fallbacks()
    return _json({"contests": data, "cached": True})


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


async def forfeit_duel_api(request: web.Request) -> web.Response:
    """POST /api/duels/forfeit — Handles user force quit / forfeit with intelligent Elo rating penalty."""
    try:
        body = await request.json()
        duel_id = int(body.get("duel_id", 0))
        forfeiter_id = str(body.get("discord_id", ""))

        if not duel_id or not forfeiter_id:
            return _json({"error": "duel_id and discord_id are required"}, status=400)

        gid = _guild(request)
        pool = get_pool()
        async with pool.acquire() as conn:
            duel = await duel_queries.get_duel(conn, duel_id)
            if not duel or duel["status"] != "active":
                return _json({"error": "duel not active or not found"}, status=404)

            p1_id = duel["player1_id"]
            p2_id = duel["player2_id"]
            is_bot = duel.get("is_bot_match", False)
            mode = duel["mode"]

            p1_row = await duel_queries.get_or_create_rating(conn, p1_id, gid, mode)
            p1_old = p1_row["rating"]

            if p2_id:
                p2_row = await duel_queries.get_or_create_rating(conn, p2_id, gid, mode)
                p2_old = p2_row["rating"]
            else:
                p2_old = duel.get("bot_rating") or 1200

            winner_id = p2_id if forfeiter_id == p1_id else p1_id
            await duel_queries.finish_duel(conn, duel_id, winner_id)

            if mode.startswith("dsa"):
                d1 = -12 if forfeiter_id == p1_id else 22
                d2 = 22 if forfeiter_id == p1_id else -12
                r1 = "loss" if forfeiter_id == p1_id else "win"
                r2 = "win" if forfeiter_id == p1_id else "loss"
            else:
                k = 40 if "icpc" in mode else (32 if mode.endswith("_duel") else 24)
                s1 = 0.0 if forfeiter_id == p1_id else 1.0
                s2 = 1.0 if forfeiter_id == p1_id else 0.0
                expected1 = 1.0 / (1.0 + 10 ** ((p2_old - p1_old) / 400.0))
                expected2 = 1.0 / (1.0 + 10 ** ((p1_old - p2_old) / 400.0))
                d1 = round(k * (s1 - expected1))
                d2 = round(k * (s2 - expected2))
                r1 = "loss" if forfeiter_id == p1_id else "win"
                r2 = "win" if forfeiter_id == p1_id else "loss"

            await duel_queries.apply_rating_delta(conn, p1_id, gid, mode, d1, r1, is_bot)
            if p1_id:
                p1_new_row = await duel_queries.get_or_create_rating(conn, p1_id, gid, mode)
                p1_new = p1_new_row["rating"]
            else:
                p1_new = p1_old + d1

            if p2_id:
                p2_new_row = await duel_queries.get_or_create_rating(conn, p2_id, gid, mode)
                p2_new = p2_new_row["rating"]
            else:
                p2_new = p2_old

        if _bot_instance:
            import asyncio
            asyncio.create_task(_broadcast_discord_duel_update(duel_id, forfeiter_id, "Match Forfeited", True, winner_id))

        return _json({
            "status": "forfeited",
            "winner_id": winner_id,
            "forfeited_by": forfeiter_id,
            "p1_old_rating": p1_old,
            "p1_new_rating": p1_new,
            "p1_rating_change": d1,
            "p2_old_rating": p2_old,
            "p2_new_rating": p2_new,
            "p2_rating_change": d2 if p2_id else 0,
        })
    except Exception as e:
        print(f"[api/duels/forfeit] error: {e}")
        return _json({"error": str(e)}, status=500)


# ───────────────────────────── COMMUNITY API ─────────────────────────────

async def get_community_threads_api(request: web.Request) -> web.Response:
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, title, author, avatar, avatar_url, post_image_url, content, tag, upvotes, comments_count,
                       to_char(created_at, 'YYYY-MM-DD HH24:MI') as date, comments_json
                FROM community_threads
                ORDER BY created_at DESC LIMIT 50
            """)
            result = []
            for r in rows:
                c_data = r["comments_json"]
                comments = json.loads(c_data) if isinstance(c_data, str) else (c_data or [])
                result.append({
                    "id": r["id"],
                    "title": r["title"],
                    "author": r["author"],
                    "avatar": r["avatar"],
                    "avatarUrl": r["avatar_url"],
                    "postImageUrl": r["post_image_url"],
                    "content": r["content"],
                    "tag": r["tag"],
                    "upvotes": r["upvotes"],
                    "commentsCount": r["comments_count"],
                    "date": r["date"] or "Just now",
                    "comments": comments
                })
            return _json(result)
    except Exception as e:
        print(f"[api/community/threads] error: {e}")
        return _json([], status=200)


async def create_community_thread_api(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        thread_id = data.get("id") or f"t_{int(dt.datetime.now().timestamp()*1000)}"
        title = data.get("title", "").strip()
        author = data.get("author", "anonymous").strip()
        avatar = data.get("avatar", "BB").strip()
        avatar_url = data.get("avatarUrl")
        post_image_url = data.get("postImageUrl")
        content = data.get("content", "").strip()
        tag = data.get("tag", "Solutions").strip()

        if not title or not content:
            return _json({"error": "Title and content required"}, status=400)

        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO community_threads (id, title, author, avatar, avatar_url, post_image_url, content, tag, upvotes, comments_count, comments_json)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 1, 0, '[]'::jsonb)
                ON CONFLICT (id) DO UPDATE SET title = EXCLUDED.title, content = EXCLUDED.content, post_image_url = EXCLUDED.post_image_url
            """, thread_id, title, author, avatar, avatar_url, post_image_url, content, tag)
        return _json({"status": "created", "id": thread_id})
    except Exception as e:
        print(f"[api/community/create] error: {e}")
        return _json({"error": str(e)}, status=500)


async def upvote_community_thread_api(request: web.Request) -> web.Response:
    thread_id = request.match_info.get("id", "")
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute("UPDATE community_threads SET upvotes = upvotes + 1 WHERE id = $1", thread_id)
        return _json({"status": "upvoted"})
    except Exception as e:
        return _json({"error": str(e)}, status=500)


async def comment_community_thread_api(request: web.Request) -> web.Response:
    thread_id = request.match_info.get("id", "")
    try:
        data = await request.json()
        comment = {
            "id": data.get("id") or f"c_{int(dt.datetime.now().timestamp()*1000)}",
            "author": data.get("author", "anonymous"),
            "avatar": data.get("avatar", "BB"),
            "avatarUrl": data.get("avatarUrl"),
            "content": data.get("content", "").strip(),
            "date": "Just now"
        }

        pool = get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT comments_json FROM community_threads WHERE id = $1", thread_id)
            if not row:
                return _json({"error": "Thread not found"}, status=404)

            c_data = row["comments_json"]
            existing_comments = json.loads(c_data) if isinstance(c_data, str) else (c_data or [])
            existing_comments.append(comment)

            await conn.execute("""
                UPDATE community_threads 
                SET comments_json = $2::jsonb, comments_count = array_length(ARRAY(SELECT jsonb_array_elements($2::jsonb)), 1)
                WHERE id = $1
            """, thread_id, json.dumps(existing_comments))

        return _json({"status": "commented", "comment": comment})
    except Exception as e:
        return _json({"error": str(e)}, status=500)


async def delete_community_thread_api(request: web.Request) -> web.Response:
    thread_id = request.match_info.get("id", "")
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM community_threads WHERE id = $1", thread_id)
        return _json({"status": "deleted"})
    except Exception as e:
        return _json({"error": str(e)}, status=500)


async def delete_community_comment_api(request: web.Request) -> web.Response:
    thread_id = request.match_info.get("id", "")
    comment_id = request.match_info.get("cid", "")
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT comments_json FROM community_threads WHERE id = $1", thread_id)
            if row:
                c_data = row["comments_json"]
                existing_comments = json.loads(c_data) if isinstance(c_data, str) else (c_data or [])
                updated_comments = [c for c in existing_comments if c.get("id") != comment_id]
                await conn.execute("""
                    UPDATE community_threads 
                    SET comments_json = $2::jsonb, comments_count = $3
                    WHERE id = $1
                """, thread_id, json.dumps(updated_comments), len(updated_comments))
        return _json({"status": "comment_deleted"})
    except Exception as e:
        return _json({"error": str(e)}, status=500)


# ───────────────────────────── DISCORD OAUTH ─────────────────────────────

async def discord_login_api(request: web.Request) -> web.Response:
    client_id = os.getenv("DISCORD_CLIENT_ID", "1519084550226051102")
    host = request.headers.get("X-Forwarded-Host") or request.headers.get("Host") or "www.binarybeats.in"
    scheme = request.headers.get("X-Forwarded-Proto", "https")
    if "localhost" in host:
        scheme = "http"

    redirect_uri = f"{scheme}://{host}/api/discord/callback"
    from urllib.parse import quote
    auth_url = (
        f"https://discord.com/oauth2/authorize?client_id={client_id}"
        f"&redirect_uri={quote(redirect_uri, safe='')}&response_type=code&scope=identify"
    )
    raise web.HTTPFound(location=auth_url)


async def discord_callback_api(request: web.Request) -> web.Response:
    code = request.query.get("code")
    client_id = os.getenv("DISCORD_CLIENT_ID", "1519084550226051102")
    client_secret = os.getenv("DISCORD_CLIENT_SECRET", "HB8O8piuilNlr94nCq02cmu1Hg7vuTJx")
    
    host = request.headers.get("X-Forwarded-Host") or request.headers.get("Host") or "www.binarybeats.in"
    scheme = request.headers.get("X-Forwarded-Proto", "https")
    if "localhost" in host:
        scheme = "http"

    redirect_uri = f"{scheme}://{host}/api/discord/callback"
    origin_base = f"{scheme}://{host}"

    if not code:
        raise web.HTTPFound(location=f"{origin_base}/?auth=error")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post("https://discord.com/api/v10/oauth2/token", data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri
            }) as resp:
                if resp.status != 200:
                    print(f"[api/discord/callback] Token error status: {resp.status}")
                    raise web.HTTPFound(location=f"{origin_base}/?auth=token_error")
                token_data = await resp.json()
                access_token = token_data.get("access_token")

            async with session.get("https://discord.com/api/v10/users/@me", headers={
                "Authorization": f"Bearer {access_token}"
            }) as me_resp:
                if me_resp.status != 200:
                    raise web.HTTPFound(location="https://www.binarybeats.in/?auth=user_error")
                user = await me_resp.json()

        avatar_url = (
            f"https://cdn.discordapp.com/avatars/{user.get('id')}/{user.get('avatar')}.png"
            if user.get("avatar")
            else "https://raw.githubusercontent.com/ashaygupta-cc/ashaygupta-cc/main/Zodiac_Z408.png"
        )
        user_session = {
            "id": user.get("id"),
            "username": user.get("username"),
            "globalName": user.get("global_name") or user.get("username"),
            "avatarUrl": avatar_url
        }

        response = web.HTTPFound(location=f"{origin_base}/")
        response.set_cookie("bb_user_session", json.dumps(user_session), max_age=86400*30, httponly=False)
        return response
    except web.HTTPFound:
        raise
    except Exception as e:
        print(f"[api/discord/callback] Exception: {e}")
        raise web.HTTPFound(location=f"{origin_base}/?auth=exception")


async def discord_me_api(request: web.Request) -> web.Response:
    cookie = request.cookies.get("bb_user_session")
    if cookie:
        try:
            data = json.loads(cookie)
            return _json({"authenticated": True, "user": data})
        except Exception:
            pass
    return _json({"authenticated": False})


async def discord_logout_api(request: web.Request) -> web.Response:
    response = _json({"status": "logged_out"})
    response.del_cookie("bb_user_session")
    return response


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
    r.add_post("/api/duels/forfeit", forfeit_duel_api)
    r.add_get("/api/community/threads", get_community_threads_api)
    r.add_post("/api/community/threads", create_community_thread_api)
    r.add_post("/api/community/threads/{id}/upvote", upvote_community_thread_api)
    r.add_post("/api/community/threads/{id}/comments", comment_community_thread_api)
    r.add_delete("/api/community/threads/{id}", delete_community_thread_api)
    r.add_delete("/api/community/threads/{id}/comments/{cid}", delete_community_comment_api)
    r.add_get("/api/discord/login", discord_login_api)
    r.add_get("/api/discord/callback", discord_callback_api)
    r.add_get("/api/discord/me", discord_me_api)
    r.add_post("/api/discord/logout", discord_logout_api)
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
