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
import re
import time
import discord
import aiohttp
from aiohttp import web

from database.connection import get_pool, get_cf_pool, get_lc_pool, ping_db
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
        "url": "https://codeforces.com/group/hfiI9LqNuy/contests",
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
    """GET /api/problems?limit=60&platform=codeforces|leetcode&search=...&difficulty=...
    Public view of daily problem feed and problem catalog."""
    limit = min(int(request.query.get("pageSize") or request.query.get("limit") or 60), 500)
    page = max(int(request.query.get("page", 1)), 1)
    offset = (page - 1) * limit

    raw_platform = (request.query.get("platform") or "").strip().lower()
    search = (request.query.get("search") or "").strip()
    difficulty = (request.query.get("difficulty") or "").strip()

    platforms = None
    if raw_platform in ("codeforces", "cf"):
        platforms = ["codeforces", "cf"]
    elif raw_platform in ("leetcode", "lc"):
        platforms = ["leetcode", "lc"]
    elif raw_platform:
        platforms = [raw_platform]

    sql = """
        SELECT p.id, p.platform, p.problem_id, p.title, p.difficulty,
               p.points, p.assigned_date, p.set_by,
               w.label AS week_label, m.label AS month_label,
               (SELECT COUNT(*) FROM solves s WHERE s.problem_db_id = p.id)
                 AS solve_count
        FROM problems p
        LEFT JOIN weeks  w ON w.id = p.week_id
        LEFT JOIN months m ON m.id = p.month_id
        WHERE ($1::text[] IS NULL OR LOWER(p.platform) = ANY($1::text[]) OR p.platform IS NULL OR p.platform = '')
          AND ($2::text IS NULL OR p.guild_id = $2 OR p.guild_id IS NULL OR p.guild_id = '')
          AND ($3::text IS NULL OR $3 = '' OR LOWER(p.title) LIKE '%' || LOWER($3) || '%' OR LOWER(p.problem_id) LIKE '%' || LOWER($3) || '%')
          AND ($4::text IS NULL OR $4 = '' OR LOWER(p.difficulty) = LOWER($4))
        ORDER BY p.assigned_date DESC NULLS LAST, p.id DESC
        LIMIT $5 OFFSET $6
    """

    count_sql = """
        SELECT COUNT(*) FROM problems p
        WHERE ($1::text[] IS NULL OR LOWER(p.platform) = ANY($1::text[]) OR p.platform IS NULL OR p.platform = '')
          AND ($2::text IS NULL OR p.guild_id = $2 OR p.guild_id IS NULL OR p.guild_id = '')
          AND ($3::text IS NULL OR $3 = '' OR LOWER(p.title) LIKE '%' || LOWER($3) || '%' OR LOWER(p.problem_id) LIKE '%' || LOWER($3) || '%')
          AND ($4::text IS NULL OR $4 = '' OR LOWER(p.difficulty) = LOWER($4))
    """

    async with get_pool().acquire() as conn:
        recs = await conn.fetch(sql, platforms, _guild(request), search, difficulty, limit, offset)
        total = await conn.fetchval(count_sql, platforms, _guild(request), search, difficulty) or len(recs)

    pages = max(1, (total + limit - 1) // limit)

    import re
    problem_list = []
    for r in recs:
        d = dict(r)
        pid = str(d.get("problem_id") or d.get("id") or "")
        d["key"] = pid

        m = re.match(r"^(\d+)([A-Za-z0-9]+)$", pid)
        if m:
            d["contestId"] = int(m.group(1))
            d["index"] = m.group(2)
        else:
            d["contestId"] = 0
            d["index"] = pid

        points = d.get("points") or 0
        diff = (d.get("difficulty") or "medium").lower()
        if points >= 500:
            d["rating"] = points
        elif diff == "easy":
            d["rating"] = 800
        elif diff == "medium":
            d["rating"] = 1200
        elif diff == "hard":
            d["rating"] = 1600
        elif diff in ("expert", "master"):
            d["rating"] = 2000
        else:
            d["rating"] = 1200

        plat_label = d.get("platform") or "CP"
        d["tags"] = [diff.capitalize(), plat_label.upper()]
        d["judgeable"] = True

        if not d.get("title") or d["title"].strip() == pid:
            d["title"] = f"Problem {pid}"

        problem_list.append(d)

    return _json({
        "problems": problem_list,
        "total": total,
        "page": page,
        "pages": pages
    })


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


async def team_index(request: web.Request) -> web.Response:
    """GET /api/team — roster for the site's Team page. Seeded members
    (founders/original leads) still live in the site's own static.ts as a
    fallback; anything added via !team shows up here and the site merges
    the two, own-DB entries taking priority by name."""
    gid = _guild(request)
    async with get_pool().acquire() as conn:
        rows = await queries.get_team_members(conn, gid)
    return _json({"members": _rows(rows)})


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
               COALESCE(NULLIF(u1.discord_username, ''), NULLIF(h1.handle, ''), d.player1_id) AS player1_name,
               COALESCE(NULLIF(u2.discord_username, ''), NULLIF(h2.handle, ''), d.player2_id) AS player2_name
        FROM duels d
        LEFT JOIN users u1 ON LOWER(u1.discord_id) = LOWER(d.player1_id)
        LEFT JOIN users u2 ON LOWER(u2.discord_id) = LOWER(d.player2_id)
        LEFT JOIN (
            SELECT DISTINCT ON (LOWER(discord_id)) discord_id, handle
            FROM handles ORDER BY LOWER(discord_id), linked_at DESC
        ) h1 ON LOWER(h1.discord_id) = LOWER(d.player1_id)
        LEFT JOIN (
            SELECT DISTINCT ON (LOWER(discord_id)) discord_id, handle
            FROM handles ORDER BY LOWER(discord_id), linked_at DESC
        ) h2 ON LOWER(h2.discord_id) = LOWER(d.player2_id)
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
               COALESCE(NULLIF(u1.discord_username, ''), NULLIF(h1.handle, ''), d.player1_id) AS player1_name,
               COALESCE(NULLIF(u2.discord_username, ''), NULLIF(h2.handle, ''), d.player2_id) AS player2_name
        FROM duels d
        LEFT JOIN users u1 ON LOWER(u1.discord_id) = LOWER(d.player1_id)
        LEFT JOIN users u2 ON LOWER(u2.discord_id) = LOWER(d.player2_id)
        LEFT JOIN (
            SELECT DISTINCT ON (LOWER(discord_id)) discord_id, handle
            FROM handles ORDER BY LOWER(discord_id), linked_at DESC
        ) h1 ON LOWER(h1.discord_id) = LOWER(d.player1_id)
        LEFT JOIN (
            SELECT DISTINCT ON (LOWER(discord_id)) discord_id, handle
            FROM handles ORDER BY LOWER(discord_id), linked_at DESC
        ) h2 ON LOWER(h2.discord_id) = LOWER(d.player2_id)
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
    redirect_uri = os.getenv("DISCORD_REDIRECT_URI", "https://www.binarybeats.in/api/discord/callback")

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
    redirect_uri = os.getenv("DISCORD_REDIRECT_URI", "https://www.binarybeats.in/api/discord/callback")
    origin_base = "https://www.binarybeats.in"

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
                    err_body = await resp.text()
                    print(f"[api/discord/callback] Token error status: {resp.status}, body: {err_body}")
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
            "avatarUrl": avatar_url,
            "isMember": True,
            "roles": ["Member"]
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


async def leetcode_status_api(request: web.Request) -> web.Response:
    return _json({"status": "ok", "platform": "leetcode"})


# ───────────────────────────── CODEFORCES API PROXY ─────────────────────────────

async def cf_user_api(request: web.Request) -> web.Response:
    handles = request.match_info.get("handles", "")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://codeforces.com/api/user.info?handles={handles}") as resp:
                if resp.status != 200:
                    return _json({"error": "user_not_found"}, status=404)
                data = await resp.json()
                if data.get("status") == "OK":
                    users = [{
                        "handle": u.get("handle"),
                        "rating": u.get("rating"),
                        "maxRating": u.get("maxRating"),
                        "rank": u.get("rank")
                    } for u in data.get("result", [])]
                    return _json({"users": users})
    except Exception as e:
        print(f"[cf_user_api] Exception: {e}")
    return _json({"error": "cf_api_error"}, status=500)


async def cf_status_api(request: web.Request) -> web.Response:
    handle = request.match_info.get("handle", "")
    count = request.query.get("count", "50")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://codeforces.com/api/user.status?handle={handle}&from=1&count={count}") as resp:
                if resp.status != 200:
                    return _json({"submissions": []})
                data = await resp.json()
                if data.get("status") == "OK":
                    submissions = [{
                        "id": s.get("id"),
                        "creationTimeSeconds": s.get("creationTimeSeconds"),
                        "verdict": s.get("verdict"),
                        "problem": {"contestId": s.get("problem", {}).get("contestId", 0), "index": s.get("problem", {}).get("index", "")}
                    } for s in data.get("result", [])]
                    return _json({"submissions": submissions})
    except Exception as e:
        print(f"[cf_status_api] Exception: {e}")
    return _json({"submissions": []})


async def cf_rating_history_api(request: web.Request) -> web.Response:
    handle = request.match_info.get("handle", "")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://codeforces.com/api/user.rating?handle={handle}") as resp:
                if resp.status != 200:
                    return _json({"history": []})
                data = await resp.json()
                if data.get("status") == "OK":
                    history = [{
                        "contestId": r.get("contestId"),
                        "contestName": r.get("contestName"),
                        "newRating": r.get("newRating"),
                        "oldRating": r.get("oldRating"),
                        "ratingUpdateTimeSeconds": r.get("ratingUpdateTimeSeconds")
                    } for r in data.get("result", [])]
                    return _json({"history": history})
    except Exception as e:
        print(f"[cf_rating_history_api] Exception: {e}")
    return _json({"history": []})


_CF_PROBLEMS_CACHE = {}
_CF_CACHE_TIME = 0


async def _fetch_cf_problem_meta(contest_id: int, idx: str) -> dict:
    global _CF_PROBLEMS_CACHE, _CF_CACHE_TIME
    now = time.time()
    if not _CF_PROBLEMS_CACHE or (now - _CF_CACHE_TIME > 3600):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://codeforces.com/api/problemset.problems") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("status") == "OK":
                            for p in data.get("result", {}).get("problems", []):
                                p_cid = p.get("contestId")
                                p_idx = p.get("index")
                                if p_cid and p_idx:
                                    _CF_PROBLEMS_CACHE[f"{p_cid}{p_idx}".upper()] = p
                            _CF_CACHE_TIME = now
        except Exception as e:
            print(f"[_fetch_cf_problem_meta] Error: {e}")
    return _CF_PROBLEMS_CACHE.get(f"{contest_id}{idx}".upper(), {})


async def _fetch_leetcode_meta(slug: str) -> dict:
    slug = slug.lower().replace("lc-", "")
    try:
        query = """
        query getQuestionDetail($titleSlug: String!) {
          question(titleSlug: $titleSlug) {
            questionId
            title
            content
            difficulty
            topicTags {
              name
            }
            exampleTestcaseList
          }
        }
        """
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://leetcode.com/graphql",
                json={"query": query, "variables": {"titleSlug": slug}},
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Content-Type": "application/json"}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    q = data.get("data", {}).get("question")
                    if q:
                        return q
    except Exception as e:
        print(f"[_fetch_leetcode_meta] Error: {e}")
    return {}


def _clean_html(html_str: str) -> str:
    if not html_str:
        return ""
    s = html_str
    # Strip LLM system prompt headers from Hugging Face / Neon DB raw rows
    s = re.sub(r"^[\s\n]*You are an? expert [^\n]+\.?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^[\s\n]*###\s*Question:\s*", "", s, flags=re.IGNORECASE)

    s = re.sub(r'<sup>([\s\S]*?)</sup>', r'^\1', s, flags=re.IGNORECASE)
    s = re.sub(r'<sub>([\s\S]*?)</sub>', r'_\1', s, flags=re.IGNORECASE)
    s = re.sub(r'<strong class="example">([\s\S]*?)</strong>', r'\n\n**\1**\n', s, flags=re.IGNORECASE)
    s = re.sub(r'<pre[^>]*>([\s\S]*?)</pre>', r'\n\1\n', s, flags=re.IGNORECASE)
    s = s.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    s = re.sub(r"</p>", "\n\n", s, flags=re.IGNORECASE)
    s = re.sub(r"</li>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<li>", "• ", s, flags=re.IGNORECASE)
    s = re.sub(r"<code>([\s\S]*?)</code>", r"`\1`", s, flags=re.IGNORECASE)
    s = re.sub(r"<strong>([\s\S]*?)</strong>", r"**\1**", s, flags=re.IGNORECASE)
    s = re.sub(r"<em>([\s\S]*?)</em>", r"*\1*", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"').replace("&nbsp;", " ").replace("&#39;", "'")
    return re.sub(r"\n{3,}", "\n\n", s).strip()


async def _fetch_hardtests_dataset(key: str, contest_id: int, idx: str) -> dict:
    queries = []
    if contest_id and idx:
        queries.append(f"pid='{contest_id}_{idx.upper()}'")
        queries.append(f"pid='{contest_id}{idx.upper()}'")
        queries.append(f"pid='codeforces_{contest_id}_{idx.upper()}'")
        queries.append(f"pid='codeforces_{contest_id}{idx.upper()}'")
        queries.append(f"url LIKE '%codeforces.com/problemset/problem/{contest_id}/{idx.upper()}%'")
        queries.append(f"url LIKE '%codeforces.com/problemset/problem/{contest_id}/{idx.lower()}%'")
    queries.append(f"pid='{key}'")

    async def fetch_one(q: str):
        params = {
            "dataset": "sigcp/hardtests_problems",
            "config": "default",
            "split": "train",
            "where": q,
            "length": "1"
        }
        try:
            timeout = aiohttp.ClientTimeout(total=2.5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get("https://datasets-server.huggingface.co/filter", params=params) as resp:
                    if resp.status == 200:
                        body = await resp.json()
                        if not body.get("error") and body.get("rows") and body["rows"][0].get("row"):
                            return body["rows"][0]["row"]
        except Exception:
            pass
        return None

    tasks = [fetch_one(q) for q in queries]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, dict) and r:
            return r
    return {}


_CF_PROBLEMS_CACHE = {}

async def _fetch_cf_problem_meta(contest_id: int, idx: str) -> dict:
    global _CF_PROBLEMS_CACHE
    cache_key = f"{contest_id}_{idx.upper()}"
    if cache_key in _CF_PROBLEMS_CACHE:
        return _CF_PROBLEMS_CACHE[cache_key]

    url = "https://codeforces.com/api/problemset.problems"
    try:
        timeout = aiohttp.ClientTimeout(total=4)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("status") == "OK":
                        problems = data.get("result", {}).get("problems", [])
                        for p in problems:
                            ck = f"{p.get('contestId')}_{str(p.get('index')).upper()}"
                            _CF_PROBLEMS_CACHE[ck] = p
                        if cache_key in _CF_PROBLEMS_CACHE:
                            return _CF_PROBLEMS_CACHE[cache_key]
    except Exception as e:
        print(f"[_fetch_cf_problem_meta err]: {e}")
    return {}


async def _fetch_leetcode_meta(key: str) -> dict:
    slug = key.lower().replace("lc-", "").strip()
    graphql_url = "https://leetcode.com/graphql"
    query = """
    query getQuestionDetail($titleSlug: String!) {
      question(titleSlug: $titleSlug) {
        questionId
        title
        titleSlug
        content
        difficulty
        topicTags {
          name
          slug
        }
        codeSnippets {
          lang
          langSlug
          code
        }
        exampleTestcaseList
      }
    }
    """
    payload = {
        "query": query,
        "variables": {"titleSlug": slug},
        "operationName": "getQuestionDetail"
    }
    try:
        timeout = aiohttp.ClientTimeout(total=5)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Content-Type": "application/json",
            "Referer": f"https://leetcode.com/problems/{slug}/"
        }
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(graphql_url, json=payload, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    q = data.get("data", {}).get("question")
                    if q:
                        return q
    except Exception as e:
        print(f"[_fetch_leetcode_meta err]: {e}")
    return {}


async def _fetch_cf_html(contest_id: int, idx: str) -> dict:
    url = f"https://codeforces.com/problemset/problem/{contest_id}/{idx}"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    }
    try:
        timeout = aiohttp.ClientTimeout(total=4)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    result = {}
                    m_title = re.search(r'<div class="title">\s*[A-Za-z0-9]+\.\s*(.*?)\s*</div>', html)
                    if m_title:
                        result["title"] = m_title.group(1).strip()

                    m_statement = re.search(r'<div class="header">[\s\S]*?</div>([\s\S]*?)<div class="input-specification">', html)
                    if m_statement:
                        result["description"] = _clean_html(m_statement.group(1))

                    m_in_fmt = re.search(r'<div class="input-specification">\s*<div class="section-title">Input</div>([\s\S]*?)</div>\s*<div class="output-specification">', html)
                    if m_in_fmt:
                        result["inputFormat"] = _clean_html(m_in_fmt.group(1))

                    m_out_fmt = re.search(r'<div class="output-specification">\s*<div class="section-title">Output</div>([\s\S]*?)</div>\s*<div class="sample-tests">', html)
                    if m_out_fmt:
                        result["outputFormat"] = _clean_html(m_out_fmt.group(1))

                    m_note = re.search(r'<div class="note">\s*<div class="section-title">Note</div>([\s\S]*?)</div>', html)
                    if m_note:
                        result["note"] = _clean_html(m_note.group(1))

                    inputs = re.findall(r'<div class="input">\s*<div class="title">Input</div>\s*<pre>([\s\S]*?)</pre>', html)
                    outputs = re.findall(r'<div class="output">\s*<div class="title">Output</div>\s*<pre>([\s\S]*?)</pre>', html)
                    
                    examples = []
                    for inp, outp in zip(inputs, outputs):
                        c_in = _clean_html(inp).strip()
                        c_out = _clean_html(outp).strip()
                        if c_in or c_out:
                            examples.append({"input": c_in, "output": c_out})
                    result["examples"] = examples
                    return result
    except Exception as e:
        print(f"[_fetch_cf_html error]: {e}")
    return {}


async def problem_statement_api(request: web.Request) -> web.Response:
    key = request.match_info.get("key", "").strip()
    platform = (request.query.get("platform") or "").strip().lower()

    if not key:
        return _json({"error": "missing key"}, status=400)

    try:
        m = re.match(r"^(\d+)([A-Za-z0-9]+)$", key)
        contest_id = int(m.group(1)) if m else 0
        idx = m.group(2) if m else key

        row = None
        try:
            async with get_pool().acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT * FROM problems 
                       WHERE LOWER(problem_id) = LOWER($1) OR id::text = $1 OR LOWER(title) = LOWER($1)
                       LIMIT 1""", key
                )
        except Exception as err:
            print(f"[problem_statement_api DB query error]: {err}")

        title = f"Problem {key}"
        difficulty = "medium"
        rating = 1200
        db_platform = platform or "codeforces"

        if row:
            title = row["title"] or f"Problem {key}"
            difficulty = row["difficulty"] or "medium"
            points = row.get("points") or 0
            db_platform = row.get("platform") or platform or "codeforces"
            if points >= 500:
                rating = points
            elif difficulty.lower() == "easy":
                rating = 800
            elif difficulty.lower() == "medium":
                rating = 1200
            elif difficulty.lower() == "hard":
                rating = 1600
            elif difficulty.lower() in ("expert", "master"):
                rating = 2000

        is_lc = "lc" in db_platform.lower() or "leetcode" in db_platform.lower() or key.startswith("LC-") or (not contest_id and "-" in key)
        plat_name = "leetcode" if is_lc else "codeforces"
        tags = [difficulty.capitalize(), plat_name.upper()]
        description = ""
        input_fmt = ""
        output_fmt = ""
        note_fmt = ""
        examples = []
        starter_code = ""

        if is_lc:
            # 1. Query LC Neon Database (2,641 problems)
            try:
                lc_pool = get_lc_pool()
                if lc_pool:
                    async with lc_pool.acquire() as conn:
                        slug_key = key.lower().replace("lc-", "").strip()
                        row_lc = await conn.fetchrow(
                            """SELECT * FROM problems 
                               WHERE LOWER(problem_key) = LOWER($1)
                                  OR LOWER(problem_key) = 'lc-' || LOWER($1)
                               LIMIT 1""", slug_key
                        )
                        if row_lc:
                            d_lc = dict(row_lc)
                            if d_lc.get("title"):
                                title = d_lc["title"]
                            if d_lc.get("rating"):
                                rating = int(d_lc["rating"])
                            if d_lc.get("tags"):
                                raw_tags = d_lc["tags"]
                                if isinstance(raw_tags, str):
                                    try:
                                        raw_tags = json.loads(raw_tags)
                                    except Exception:
                                        pass
                                if isinstance(raw_tags, list):
                                    tags = [str(t) for t in raw_tags]
                            if d_lc.get("description"):
                                description = _clean_html(d_lc["description"])
                            if d_lc.get("examples"):
                                raw_ex = d_lc["examples"]
                                if isinstance(raw_ex, str):
                                    try:
                                        raw_ex = json.loads(raw_ex)
                                    except Exception:
                                        pass
                                if isinstance(raw_ex, list):
                                    examples = raw_ex
            except Exception as err_lc_db:
                print(f"[LC Neon DB query error]: {err_lc_db}")

            # 2. Live GraphQL fetch for starterCode and extra details
            try:
                lc_meta = await _fetch_leetcode_meta(key)
                if lc_meta:
                    if not title or title == f"Problem {key}":
                        title = lc_meta.get("title") or title
                    diff_raw = lc_meta.get("difficulty") or "Medium"
                    if not rating or rating == 1200:
                        rating = 1200 if diff_raw == "Easy" else 1600 if diff_raw == "Medium" else 2100
                    if not tags or tags == [difficulty.capitalize(), "LEETCODE"]:
                        tags = [t.get("name") for t in lc_meta.get("topicTags", []) if t.get("name")] or tags
                    if not description:
                        description = _clean_html(lc_meta.get("content", ""))
                    
                    tc_list = lc_meta.get("exampleTestcaseList", [])
                    if not examples and tc_list:
                        for tc in tc_list[:3]:
                            examples.append({"input": str(tc).strip(), "output": "Output evaluated upon submission"})
                    
                    snippets = lc_meta.get("codeSnippets", [])
                    if isinstance(snippets, list):
                        for snip in snippets:
                            if isinstance(snip, dict) and snip.get("langSlug") in ("cpp", "c++"):
                                starter_code = snip.get("code", "")
                                break
            except Exception as e_lc:
                print(f"[_fetch_leetcode_meta error]: {e_lc}")
        else:
            # 1. Query CF Neon DB (10,025 problems with 71,003 testcases)
            try:
                cf_pool = get_cf_pool()
                if cf_pool:
                    async with cf_pool.acquire() as conn:
                        cf_key = f"{contest_id}-{idx.upper()}" if contest_id and idx else key
                        row_cf = await conn.fetchrow(
                            """SELECT * FROM problems 
                               WHERE LOWER(problem_key) = LOWER($1)
                                  OR LOWER(problem_key) = LOWER($2)
                                  OR (contest_id = $3 AND UPPER(problem_index) = UPPER($4))
                               LIMIT 1""", key, cf_key, contest_id, idx
                        )
                        if row_cf:
                            d_cf = dict(row_cf)
                            if d_cf.get("title"):
                                title = d_cf["title"]
                            if d_cf.get("rating"):
                                rating = int(d_cf["rating"])
                            if d_cf.get("tags"):
                                raw_tags = d_cf["tags"]
                                if isinstance(raw_tags, str):
                                    try:
                                        raw_tags = json.loads(raw_tags)
                                    except Exception:
                                        pass
                                if isinstance(raw_tags, list):
                                    tags = [str(t).capitalize() for t in raw_tags]
                            if d_cf.get("description"):
                                description = _clean_html(d_cf["description"])
                            if d_cf.get("input_format"):
                                input_fmt = _clean_html(d_cf["input_format"])
                            if d_cf.get("output_format"):
                                output_fmt = _clean_html(d_cf["output_format"])
                            if d_cf.get("note"):
                                note_fmt = _clean_html(d_cf["note"])
                            if d_cf.get("examples"):
                                raw_ex = d_cf["examples"]
                                if isinstance(raw_ex, str):
                                    try:
                                        raw_ex = json.loads(raw_ex)
                                    except Exception:
                                        pass
                                if isinstance(raw_ex, list):
                                    examples = raw_ex
            except Exception as err_cf_db:
                print(f"[CF Neon DB query error]: {err_cf_db}")

            # 2. Codeforces API metadata & HTML direct fetch fallbacks
            if contest_id and idx:
                try:
                    cf_meta = await _fetch_cf_problem_meta(contest_id, idx)
                    if cf_meta:
                        if not title or title == f"Problem {key}":
                            title = cf_meta.get("name") or title
                        if cf_meta.get("rating") and not rating:
                            rating = int(cf_meta["rating"])
                        if cf_meta.get("tags") and not tags:
                            tags = [t.capitalize() for t in cf_meta["tags"]]
                except Exception as e_cf:
                    print(f"[_fetch_cf_problem_meta error]: {e_cf}")

                if not description or not examples:
                    try:
                        cf_data = await _fetch_cf_html(contest_id, idx)
                        if cf_data:
                            if cf_data.get("title") and (not title or title == f"Problem {key}"):
                                title = cf_data["title"]
                            if cf_data.get("description") and not description:
                                description = cf_data["description"]
                            if cf_data.get("inputFormat") and not input_fmt:
                                input_fmt = cf_data["inputFormat"]
                            if cf_data.get("outputFormat") and not output_fmt:
                                output_fmt = cf_data["outputFormat"]
                            if cf_data.get("note") and not note_fmt:
                                note_fmt = cf_data["note"]
                            if cf_data.get("examples") and not examples:
                                examples = cf_data["examples"]
                    except Exception as e_html:
                        print(f"[_fetch_cf_html error]: {e_html}")

            # Try Hugging Face HARDTESTS dataset as last resort if description or examples still missing
            if not description or not examples:
                try:
                    hf_row = await _fetch_hardtests_dataset(key, contest_id, idx)
                    if hf_row:
                        if hf_row.get("name") or hf_row.get("title"):
                            title = hf_row.get("name") or hf_row.get("title")
                        
                        if hf_row.get("difficulty_ratings") and isinstance(hf_row.get("difficulty_ratings"), list):
                            for d_entry in hf_row.get("difficulty_ratings"):
                                if isinstance(d_entry, dict) and d_entry.get("score"):
                                    try:
                                        rating = int(d_entry["score"])
                                        break
                                    except Exception:
                                        pass
                        elif hf_row.get("rating"):
                            try:
                                rating = int(hf_row["rating"])
                            except Exception:
                                pass

                        if hf_row.get("tags") and isinstance(hf_row.get("tags"), list):
                            parsed_tags = [str(t).capitalize() for t in hf_row["tags"] if t]
                            if parsed_tags:
                                tags = parsed_tags

                        raw_desc = hf_row.get("description") or hf_row.get("content") or hf_row.get("problem_description") or ""
                        if raw_desc:
                            description = _clean_html(raw_desc)
                        input_fmt = _clean_html(hf_row.get("input_format") or hf_row.get("input_specification") or "")
                        output_fmt = _clean_html(hf_row.get("output_format") or hf_row.get("output_specification") or "")
                        note_fmt = _clean_html(hf_row.get("note") or "")
                        
                        tc_list = hf_row.get("public_test_cases") or hf_row.get("test_cases") or []
                        if isinstance(tc_list, list) and tc_list:
                            examples = []
                            for tc in tc_list[:5]:
                                if isinstance(tc, dict):
                                    examples.append({
                                        "input": str(tc.get("input") or "").strip(),
                                        "output": str(tc.get("output") or "").strip()
                                    })
                except Exception as e_hf:
                    print(f"[_fetch_hardtests_dataset error]: {e_hf}")

        external_url = (
            f"https://leetcode.com/problems/{key.lower().replace('lc-', '')}/"
            if is_lc
            else f"https://codeforces.com/problemset/problem/{contest_id}/{idx}"
            if contest_id
            else "https://codeforces.com/problemset"
        )

        if not description:
            description = (
                f"### {title}\n\n"
                f"You are viewing problem **{key}** on **{plat_name.capitalize()}**.\n\n"
                f"**Problem Identifier:** `{key}`  \n"
                f"**Difficulty Rating:** {rating} ({difficulty.capitalize()})  \n"
                f"**Tags:** {', '.join(tags)}\n\n"
                f"Click the link below to open the complete statement on the official judge:\n\n"
                f"👉 [{external_url}]({external_url})\n\n"
                f"Write your solution in C++, Python, or Java and submit to verify your logic!"
            )

        if not examples:
            examples = [{"input": "Sample Input Data", "output": "Sample Output Data"}]

        statement = {
            "key": key,
            "contestId": contest_id,
            "index": idx,
            "title": title,
            "rating": rating,
            "tags": tags,
            "timeLimitMs": 2000,
            "memoryLimitMb": 256,
            "description": description,
            "inputFormat": input_fmt or "Standard Input containing test case parameters.",
            "outputFormat": output_fmt or "Print the required answer to Standard Output.",
            "note": note_fmt or "Ensure your algorithm complies with default time limits.",
            "examples": examples,
            "interactive": False,
            "judgeable": True,
            "testCount": max(len(examples), 5),
            "platform": plat_name,
            "starterCode": starter_code
        }

        return _json({"problem": statement})
    except Exception as exc:
        print(f"[problem_statement_api FATAL]: {exc}")
        return _json({
            "problem": {
                "key": key,
                "contestId": 0,
                "index": key,
                "title": f"Problem {key}",
                "rating": 1200,
                "tags": ["CP"],
                "timeLimitMs": 2000,
                "memoryLimitMb": 256,
                "description": f"### Problem {key}\n\nProblem details loading...",
                "inputFormat": "Standard Input",
                "outputFormat": "Standard Output",
                "note": "",
                "examples": [{"input": "Sample Input", "output": "Sample Output"}],
                "interactive": False,
                "judgeable": True,
                "testCount": 5,
                "platform": "codeforces"
            }
        })


def build_app() -> web.Application:
    app = web.Application(middlewares=[_cors])
    r = app.router
    r.add_get("/", health)
    r.add_get("/health", health)
    r.add_get("/api/stats", stats)
    r.add_get("/api/modes", modes)
    r.add_get("/api/problems", daily_problems)
    r.add_get("/api/problems/{id}/solvers", problem_solvers)
    r.add_get("/api/problems/{key}/statement", problem_statement_api)
    r.add_get("/api/leaderboard/points", leaderboard_points)
    r.add_get("/api/leaderboard/rating", leaderboard_rating)
    r.add_get("/api/users/{discord_id}", profile)
    r.add_get("/api/team", team_index)
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
    r.add_get("/api/leetcode/status", leetcode_status_api)
    r.add_get("/api/cf/user/{handles}", cf_user_api)
    r.add_get("/api/cf/status/{handle}", cf_status_api)
    r.add_get("/api/cf/user/{handle}/rating-history", cf_rating_history_api)
    r.add_get("/api/announcements", announcements)
    r.add_get("/api/channels", channel_index)
    r.add_get("/api/channels/{key}/messages", channel_messages)
    r.add_get("/api/channels/{key}/threads", channel_threads)
    r.add_get("/api/threads/{thread_id}/messages", thread_messages)
    r.add_get("/api/editorials/{date}", editorial_for_date)
    r.add_get("/api/guild", guild_stats)
    r.add_get("/api/contests", upcoming_contests)

    # ── /api/bot/* route aliases for legacy frontend compatibility ──
    r.add_get("/api/bot/problems", daily_problems)
    r.add_get("/api/bot/problems/{key}/statement", problem_statement_api)
    r.add_get("/api/bot/contests", upcoming_contests)
    r.add_get("/api/bot/team", team_index)
    r.add_get("/api/bot/channels", channel_index)
    r.add_get("/api/bot/channels/{key}/messages", channel_messages)
    r.add_get("/api/bot/channels/{key}/threads", channel_threads)
    r.add_get("/api/bot/threads/{thread_id}/messages", thread_messages)
    r.add_get("/api/bot/editorials/{date}", editorial_for_date)
    r.add_post("/api/bot/duels/create", create_duel_api)
    r.add_get("/api/bot/duels/state/{id}", get_duel_state_api)
    r.add_post("/api/bot/duels/verify", verify_duel_submission_api)
    r.add_post("/api/bot/duels/forfeit", forfeit_duel_api)

    r.add_get("/api/internal/hardtests/{pid}", get_hardtests)
    r.add_post("/api/problems/check", check_submissions)
    r.add_post("/api/internal/membership", internal_membership)
    r.add_route("OPTIONS", "/{tail:.*}", lambda req: web.Response(status=204))
    return app


async def run_server(port: int, bot=None):
    """Drop-in replacement for keep_alive.run_server."""
    global _bot_instance
    _bot_instance = bot

    try:
        from database.connection import init_pool
        await init_pool()
        print("[api] DB pools (Supabase, CF Neon DB, LC Neon DB) initialized successfully.")
    except Exception as e_db:
        print(f"[api] DB pool initialization warning: {e_db}")

    runner = web.AppRunner(build_app())
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    print(f"[api] Binary Beats API listening on :{port}")
