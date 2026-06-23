"""
database/queries.py — Every DB operation in one place.
All functions accept an asyncpg connection/pool and return plain Python objects.
"""

import asyncpg
from datetime import date
from config import DEFAULT_DIFFICULTY_POINTS


# ══════════════════════════════════════════════════════════════
#  USERS
# ══════════════════════════════════════════════════════════════

async def upsert_user(conn, discord_id: str, username: str):
    await conn.execute(
        """
        INSERT INTO users (discord_id, discord_username)
        VALUES ($1, $2)
        ON CONFLICT (discord_id) DO UPDATE SET discord_username = EXCLUDED.discord_username
        """,
        discord_id, username,
    )


async def get_user(conn, discord_id: str):
    return await conn.fetchrow("SELECT * FROM users WHERE discord_id = $1", discord_id)


# ══════════════════════════════════════════════════════════════
#  HANDLES
# ══════════════════════════════════════════════════════════════

async def set_handle(conn, discord_id: str, platform: str, handle: str, verified: bool = True):
    await conn.execute(
        """
        INSERT INTO handles (discord_id, platform, handle, verified)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (discord_id, platform) DO UPDATE
            SET handle = EXCLUDED.handle, verified = EXCLUDED.verified, linked_at = NOW()
        """,
        discord_id, platform, handle, verified,
    )


async def get_handle(conn, discord_id: str, platform: str) -> str | None:
    row = await conn.fetchrow(
        "SELECT handle FROM handles WHERE discord_id = $1 AND platform = $2",
        discord_id, platform,
    )
    return row["handle"] if row else None


async def get_user_handles(conn, discord_id: str) -> list:
    return await conn.fetch(
        "SELECT platform, handle, verified, linked_at FROM handles WHERE discord_id = $1",
        discord_id,
    )


async def get_all_handles_for_platform(conn, platform: str) -> list:
    """Returns all (discord_id, handle) pairs for a platform — used in bulk checks."""
    return await conn.fetch(
        "SELECT discord_id, handle FROM handles WHERE platform = $1",
        platform,
    )


async def delete_handle(conn, discord_id: str, platform: str):
    await conn.execute(
        "DELETE FROM handles WHERE discord_id = $1 AND platform = $2",
        discord_id, platform,
    )


# ══════════════════════════════════════════════════════════════
#  WEEKS
# ══════════════════════════════════════════════════════════════

async def create_week(
    conn, guild_id: str, label: str, start: date, end: date
) -> int:
    # Deactivate any current active week for this guild
    await conn.execute(
        "UPDATE weeks SET is_active = FALSE WHERE guild_id = $1 AND is_active = TRUE",
        guild_id,
    )
    row = await conn.fetchrow(
        """
        INSERT INTO weeks (guild_id, label, start_date, end_date, is_active)
        VALUES ($1, $2, $3, $4, TRUE)
        RETURNING id
        """,
        guild_id, label, start, end,
    )
    return row["id"]


async def get_active_week(conn, guild_id: str):
    return await conn.fetchrow(
        "SELECT * FROM weeks WHERE guild_id = $1 AND is_active = TRUE ORDER BY id DESC LIMIT 1",
        guild_id,
    )


async def get_week(conn, week_id: int):
    return await conn.fetchrow("SELECT * FROM weeks WHERE id = $1", week_id)


# ══════════════════════════════════════════════════════════════
#  PROBLEMS
# ══════════════════════════════════════════════════════════════

async def add_problem(
    conn,
    guild_id: str,
    week_id: int,
    platform: str,
    problem_id: str,
    title: str | None,
    difficulty: str,
    points: int,
    set_by: str,
    assigned_date: date | None = None,
) -> int:
    row = await conn.fetchrow(
        """
        INSERT INTO problems
            (guild_id, week_id, platform, problem_id, title, difficulty, points, set_by, assigned_date)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
        ON CONFLICT (guild_id, week_id, platform, problem_id) DO UPDATE
            SET difficulty = EXCLUDED.difficulty,
                points     = EXCLUDED.points,
                title      = COALESCE(EXCLUDED.title, problems.title)
        RETURNING id
        """,
        guild_id, week_id, platform, problem_id.upper() if platform == "cf" else problem_id,
        title, difficulty.lower(), points, set_by, assigned_date,
    )
    return row["id"]


async def get_problems_for_week(conn, guild_id: str, week_id: int) -> list:
    return await conn.fetch(
        """
        SELECT * FROM problems
        WHERE guild_id = $1 AND week_id = $2
        ORDER BY platform, created_at
        """,
        guild_id, week_id,
    )


async def get_problem_by_id(conn, problem_db_id: int):
    return await conn.fetchrow("SELECT * FROM problems WHERE id = $1", problem_db_id)


async def remove_problem(conn, problem_db_id: int, guild_id: str):
    await conn.execute(
        "DELETE FROM problems WHERE id = $1 AND guild_id = $2",
        problem_db_id, guild_id,
    )


async def set_problem_difficulty(conn, problem_db_id: int, difficulty: str, points: int):
    await conn.execute(
        "UPDATE problems SET difficulty = $1, points = $2 WHERE id = $3",
        difficulty.lower(), points, problem_db_id,
    )


# ══════════════════════════════════════════════════════════════
#  SOLVES
# ══════════════════════════════════════════════════════════════

async def has_solved(conn, discord_id: str, problem_db_id: int) -> bool:
    row = await conn.fetchrow(
        "SELECT 1 FROM solves WHERE discord_id = $1 AND problem_db_id = $2",
        discord_id, problem_db_id,
    )
    return row is not None


async def record_solve(
    conn,
    discord_id: str,
    problem_db_id: int,
    guild_id: str,
    solved_at,
    points: int,
) -> bool:
    """Returns True if newly recorded, False if already existed."""
    try:
        await conn.execute(
            """
            INSERT INTO solves (discord_id, problem_db_id, guild_id, solved_at, points_awarded)
            VALUES ($1, $2, $3, $4, $5)
            """,
            discord_id, problem_db_id, guild_id, solved_at, points,
        )
        return True
    except asyncpg.UniqueViolationError:
        return False


async def get_user_solves(conn, discord_id: str, guild_id: str) -> list:
    return await conn.fetch(
        """
        SELECT s.*, p.platform, p.problem_id, p.title, p.difficulty
        FROM solves s
        JOIN problems p ON p.id = s.problem_db_id
        WHERE s.discord_id = $1 AND s.guild_id = $2
        ORDER BY s.solved_at DESC
        """,
        discord_id, guild_id,
    )


# ══════════════════════════════════════════════════════════════
#  LEADERBOARD
# ══════════════════════════════════════════════════════════════

async def get_weekly_leaderboard(conn, guild_id: str, week_id: int) -> list:
    """Returns rows of (discord_id, total_points) sorted descending."""
    return await conn.fetch(
        """
        SELECT s.discord_id, SUM(s.points_awarded) AS total
        FROM solves s
        JOIN problems p ON p.id = s.problem_db_id
        WHERE s.guild_id = $1 AND p.week_id = $2
        GROUP BY s.discord_id
        ORDER BY total DESC
        """,
        guild_id, week_id,
    )


async def get_alltime_leaderboard(conn, guild_id: str) -> list:
    return await conn.fetch(
        """
        SELECT discord_id, SUM(points_awarded) AS total
        FROM solves
        WHERE guild_id = $1
        GROUP BY discord_id
        ORDER BY total DESC
        """,
        guild_id,
    )


# ══════════════════════════════════════════════════════════════
#  DIFFICULTY POINTS CONFIG
# ══════════════════════════════════════════════════════════════

async def get_difficulty_points(conn, guild_id: str) -> dict[str, int]:
    rows = await conn.fetch(
        "SELECT difficulty, points FROM difficulty_points WHERE guild_id = $1",
        guild_id,
    )
    cfg = dict(DEFAULT_DIFFICULTY_POINTS)   # start with defaults
    cfg.update({r["difficulty"]: r["points"] for r in rows})
    return cfg


async def set_difficulty_points(conn, guild_id: str, difficulty: str, points: int):
    await conn.execute(
        """
        INSERT INTO difficulty_points (guild_id, difficulty, points)
        VALUES ($1, $2, $3)
        ON CONFLICT (guild_id, difficulty) DO UPDATE SET points = EXCLUDED.points
        """,
        guild_id, difficulty.lower(), points,
    )


# ══════════════════════════════════════════════════════════════
#  RESET OPERATIONS
# ══════════════════════════════════════════════════════════════

async def reset_current_week_solves(conn, guild_id: str) -> int:
    """Delete all solves that belong to the currently active week for this guild.
    Returns the number of rows deleted."""
    result = await conn.execute(
        """
        DELETE FROM solves
        WHERE guild_id = $1
          AND problem_db_id IN (
              SELECT p.id FROM problems p
              JOIN weeks w ON w.id = p.week_id
              WHERE p.guild_id = $1 AND w.is_active = TRUE
          )
        """,
        guild_id,
    )
    # asyncpg returns 'DELETE N'
    return int(result.split()[-1])


async def reset_all_solves(conn, guild_id: str) -> int:
    """Delete ALL solves for this guild (nuclear option).
    Returns the number of rows deleted."""
    result = await conn.execute(
        "DELETE FROM solves WHERE guild_id = $1",
        guild_id,
    )
    return int(result.split()[-1])


async def reset_user_week_solves(conn, discord_id: str, guild_id: str) -> int:
    """Delete this week's solves for a specific user in a guild."""
    result = await conn.execute(
        """
        DELETE FROM solves
        WHERE discord_id = $1
          AND guild_id   = $2
          AND problem_db_id IN (
              SELECT p.id FROM problems p
              JOIN weeks w ON w.id = p.week_id
              WHERE p.guild_id = $2 AND w.is_active = TRUE
          )
        """,
        discord_id, guild_id,
    )
    return int(result.split()[-1])


async def reset_user_all_solves(conn, discord_id: str, guild_id: str) -> int:
    """Delete ALL solves for a specific user in a guild."""
    result = await conn.execute(
        "DELETE FROM solves WHERE discord_id = $1 AND guild_id = $2",
        discord_id, guild_id,
    )
    return int(result.split()[-1])


async def unmark_problem_solves(conn, problem_db_id: int, guild_id: str) -> int:
    """Remove all solves for a specific problem (so members can re-earn points if re-added)."""
    result = await conn.execute(
        "DELETE FROM solves WHERE problem_db_id = $1 AND guild_id = $2",
        problem_db_id, guild_id,
    )
    return int(result.split()[-1])


async def reset_week_and_problems(conn, guild_id: str) -> dict:
    """
    Nuclear-lite: deletes solves AND problems for the current active week,
    then deactivates the week itself.
    Returns a dict with counts.
    """
    # Step 1: get active week
    week = await conn.fetchrow(
        "SELECT id FROM weeks WHERE guild_id = $1 AND is_active = TRUE ORDER BY id DESC LIMIT 1",
        guild_id,
    )
    if not week:
        return {"solves": 0, "problems": 0, "week_id": None}

    week_id = week["id"]

    # Step 2: delete solves for problems in this week
    r1 = await conn.execute(
        """
        DELETE FROM solves
        WHERE problem_db_id IN (
            SELECT id FROM problems WHERE guild_id = $1 AND week_id = $2
        )
        """,
        guild_id, week_id,
    )
    solves_deleted = int(r1.split()[-1])

    # Step 3: delete the problems
    r2 = await conn.execute(
        "DELETE FROM problems WHERE guild_id = $1 AND week_id = $2",
        guild_id, week_id,
    )
    problems_deleted = int(r2.split()[-1])

    # Step 4: deactivate the week
    await conn.execute(
        "UPDATE weeks SET is_active = FALSE WHERE id = $1",
        week_id,
    )

    return {"solves": solves_deleted, "problems": problems_deleted, "week_id": week_id}
