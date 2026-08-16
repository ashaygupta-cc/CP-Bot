"""
database/queries.py — Every DB operation in one place.
v2: Added day-window checks, months, manual adjustments, daily/weekly/monthly leaderboards.
"""

import asyncpg
from datetime import date, datetime, timezone, timedelta
from config import DEFAULT_DIFFICULTY_POINTS, IST_OFFSET_HOURS, IST_OFFSET_MINS

# ── IST helpers ───────────────────────────────────────────────────────────────

IST = timezone(timedelta(hours=IST_OFFSET_HOURS, minutes=IST_OFFSET_MINS))

def _day_window_utc(target_date: date) -> tuple[datetime, datetime]:
    """
    Returns (start_utc, end_utc) for midnight-to-midnight IST of target_date.
    e.g. 2026-06-26 IST  →  2026-06-25 18:30 UTC  →  2026-06-26 18:29:59 UTC
    """
    start_ist = datetime(target_date.year, target_date.month, target_date.day,
                         0, 0, 0, tzinfo=IST)
    end_ist   = datetime(target_date.year, target_date.month, target_date.day,
                         23, 59, 59, tzinfo=IST)
    return start_ist.astimezone(timezone.utc), end_ist.astimezone(timezone.utc)


def today_ist() -> date:
    return datetime.now(IST).date()


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

async def create_week(conn, guild_id: str, label: str, start: date, end: date) -> int:
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
#  MONTHS
# ══════════════════════════════════════════════════════════════

async def create_month(conn, guild_id: str, label: str, start: date, end: date) -> int:
    await conn.execute(
        "UPDATE months SET is_active = FALSE WHERE guild_id = $1 AND is_active = TRUE",
        guild_id,
    )
    row = await conn.fetchrow(
        """
        INSERT INTO months (guild_id, label, start_date, end_date, is_active)
        VALUES ($1, $2, $3, $4, TRUE)
        RETURNING id
        """,
        guild_id, label, start, end,
    )
    return row["id"]


async def get_active_month(conn, guild_id: str):
    return await conn.fetchrow(
        "SELECT * FROM months WHERE guild_id = $1 AND is_active = TRUE ORDER BY id DESC LIMIT 1",
        guild_id,
    )


# ══════════════════════════════════════════════════════════════
#  PROBLEMS  (now require assigned_date)
# ══════════════════════════════════════════════════════════════

async def add_problem(
    conn,
    guild_id:      str,
    week_id:       int,
    platform:      str,
    problem_id:    str,
    title:         str | None,
    difficulty:    str,
    points:        int,
    set_by:        str,
    assigned_date: date,        # required in v2
    month_id:      int | None = None,
) -> int:
    row = await conn.fetchrow(
        """
        INSERT INTO problems
            (guild_id, week_id, month_id, platform, problem_id, title,
             difficulty, points, set_by, assigned_date)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
        ON CONFLICT (guild_id, week_id, platform, problem_id) DO UPDATE
            SET difficulty    = EXCLUDED.difficulty,
                points        = EXCLUDED.points,
                title         = COALESCE(EXCLUDED.title, problems.title),
                assigned_date = EXCLUDED.assigned_date,
                month_id      = EXCLUDED.month_id
        RETURNING id
        """,
        guild_id, week_id, month_id, platform,
        problem_id.upper() if platform == "cf" else problem_id,
        title, difficulty.lower(), points, set_by, assigned_date,
    )
    return row["id"]


async def get_problems_for_week(conn, guild_id: str, week_id: int) -> list:
    return await conn.fetch(
        """
        SELECT * FROM problems
        WHERE guild_id = $1 AND week_id = $2
        ORDER BY assigned_date, platform, created_at
        """,
        guild_id, week_id,
    )


async def get_problems_for_day(conn, guild_id: str, target_date: date) -> list:
    """Return problems whose assigned_date == target_date.
    Excludes soft-deleted problems (week_id = NULL via !removeproblem keep_history).
    """
    return await conn.fetch(
        """
        SELECT * FROM problems
        WHERE guild_id = $1 AND assigned_date = $2
          AND week_id IS NOT NULL
        ORDER BY platform, created_at
        """,
        guild_id, target_date,
    )


async def get_problem_by_id(conn, problem_db_id: int):
    return await conn.fetchrow("SELECT * FROM problems WHERE id = $1", problem_db_id)


async def remove_problem_keep_solves(conn, problem_db_id: int, guild_id: str):
    """Remove a problem WITHOUT deleting its solves (solves orphan gracefully via ON DELETE CASCADE
    but we want to keep them for history — so we soft-remove by setting week_id = NULL)."""
    # Actually CASCADE will delete solves. To keep solve history we simply delete the problem row
    # but first detach it from the week so it doesn't cascade. We achieve this by setting week_id=NULL.
    await conn.execute(
        "UPDATE problems SET week_id = NULL WHERE id = $1 AND guild_id = $2",
        problem_db_id, guild_id,
    )


async def hard_remove_problem(conn, problem_db_id: int, guild_id: str):
    """Hard delete — also deletes solves via CASCADE."""
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
#  SOLVES  (day-window enforced)
# ══════════════════════════════════════════════════════════════

async def has_solved(conn, discord_id: str, problem_db_id: int) -> bool:
    row = await conn.fetchrow(
        "SELECT 1 FROM solves WHERE discord_id = $1 AND problem_db_id = $2",
        discord_id, problem_db_id,
    )
    return row is not None


async def get_solve_count_for_problem(conn, problem_db_id: int, guild_id: str) -> int:
    """
    Returns how many distinct members have solved this problem in this guild.
    Used by !saferemove to gate deletion.
    """
    row = await conn.fetchrow(
        "SELECT COUNT(*) AS cnt FROM solves WHERE problem_db_id = $1 AND guild_id = $2",
        problem_db_id, guild_id,
    )
    return row["cnt"] if row else 0


async def record_solve(
    conn,
    discord_id:    str,
    problem_db_id: int,
    guild_id:      str,
    solved_at:     datetime,
    points:        int,
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
        SELECT s.*, p.platform, p.problem_id, p.title, p.difficulty, p.assigned_date
        FROM solves s
        JOIN problems p ON p.id = s.problem_db_id
        WHERE s.discord_id = $1 AND s.guild_id = $2
        ORDER BY s.solved_at DESC
        """,
        discord_id, guild_id,
    )


# ══════════════════════════════════════════════════════════════
#  LEADERBOARDS
# ══════════════════════════════════════════════════════════════

async def get_daily_leaderboard(conn, guild_id: str, target_date: date) -> list:
    """
    Points from problems whose assigned_date == target_date,
    solved within the IST day window of that date.
    """
    start_utc, end_utc = _day_window_utc(target_date)
    return await conn.fetch(
        """
        SELECT s.discord_id,
               SUM(s.points_awarded) AS total,
               COUNT(*) AS solved_count
        FROM solves s
        JOIN problems p ON p.id = s.problem_db_id
        WHERE s.guild_id = $1
          AND p.assigned_date = $2
          AND s.solved_at >= $3
          AND s.solved_at <= $4
        GROUP BY s.discord_id
        ORDER BY total DESC
        """,
        guild_id, target_date, start_utc, end_utc,
    )


async def get_weekly_leaderboard(conn, guild_id: str, week_id: int) -> list:
    """
    Points from all solves within the week's problems PLUS manual point_adjustments.
    Users with only adjustments (0 solves) are included via FULL OUTER JOIN so that
    adjusted points always appear even when solve records were cleared.
    """
    return await conn.fetch(
        """
        SELECT
            COALESCE(sv.discord_id, adj.discord_id) AS discord_id,
            COALESCE(sv.solve_pts, 0) + COALESCE(adj.delta, 0) AS total,
            COALESCE(sv.solved_count, 0) AS solved_count
        FROM (
            SELECT s.discord_id,
                   SUM(s.points_awarded) AS solve_pts,
                   COUNT(*) AS solved_count
            FROM solves s
            JOIN problems p ON p.id = s.problem_db_id
            WHERE s.guild_id = $1 AND p.week_id = $2
            GROUP BY s.discord_id
        ) sv
        FULL OUTER JOIN (
            SELECT discord_id, SUM(delta) AS delta
            FROM point_adjustments
            WHERE guild_id = $1
            GROUP BY discord_id
        ) adj ON adj.discord_id = sv.discord_id
        WHERE COALESCE(sv.solve_pts, 0) + COALESCE(adj.delta, 0) > 0
        ORDER BY total DESC
        """,
        guild_id, week_id,
    )


async def get_monthly_leaderboard(
    conn,
    guild_id:        str,
    month_start:     date,
    month_end:       date,
    exclude_week_id: int | None = None,   # kept for API compatibility, unused in display
) -> list:
    """
    Points from problems whose assigned_date falls within the month date range
    PLUS manual point_adjustments for the guild.

    DISPLAY RULE: Shows ALL solves in the month window (including weekly problems).
    Exclusion only applies to RESET operations, not reads.
    Users with only adjustments (0 solves) are included via FULL OUTER JOIN so that
    adjusted points always appear even when solve records were cleared.
    """
    return await conn.fetch(
        """
        SELECT
            COALESCE(sv.discord_id, adj.discord_id) AS discord_id,
            COALESCE(sv.solve_pts, 0) + COALESCE(adj.delta, 0) AS total,
            COALESCE(sv.solved_count, 0) AS solved_count
        FROM (
            SELECT s.discord_id,
                   SUM(s.points_awarded) AS solve_pts,
                   COUNT(*) AS solved_count
            FROM solves s
            JOIN problems p ON p.id = s.problem_db_id
            WHERE s.guild_id = $1
              AND p.assigned_date >= $2
              AND p.assigned_date <= $3
            GROUP BY s.discord_id
        ) sv
        FULL OUTER JOIN (
            SELECT discord_id, SUM(delta) AS delta
            FROM point_adjustments
            WHERE guild_id = $1
            GROUP BY discord_id
        ) adj ON adj.discord_id = sv.discord_id
        WHERE COALESCE(sv.solve_pts, 0) + COALESCE(adj.delta, 0) > 0
        ORDER BY total DESC
        """,
        guild_id, month_start, month_end,
    )


async def get_alltime_leaderboard(conn, guild_id: str) -> list:
    return await conn.fetch(
        """
        SELECT s.discord_id,
               SUM(s.points_awarded + COALESCE(adj.delta, 0)) AS total,
               SUM(s.points_awarded) AS solve_pts,
               COUNT(s.id) AS solved_count
        FROM solves s
        LEFT JOIN (
            SELECT discord_id, SUM(delta) AS delta
            FROM point_adjustments
            WHERE guild_id = $1
            GROUP BY discord_id
        ) adj ON adj.discord_id = s.discord_id
        WHERE s.guild_id = $1
        GROUP BY s.discord_id
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
    cfg = dict(DEFAULT_DIFFICULTY_POINTS)
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
#  MANUAL POINT ADJUSTMENTS
# ══════════════════════════════════════════════════════════════

async def add_point_adjustment(
    conn, guild_id: str, discord_id: str, delta: int, reason: str, adjusted_by: str
):
    await conn.execute(
        """
        INSERT INTO point_adjustments (guild_id, discord_id, delta, reason, adjusted_by)
        VALUES ($1, $2, $3, $4, $5)
        """,
        guild_id, discord_id, delta, reason, adjusted_by,
    )


async def get_user_adjustment_total(conn, guild_id: str, discord_id: str) -> int:
    row = await conn.fetchrow(
        "SELECT COALESCE(SUM(delta), 0) AS total FROM point_adjustments WHERE guild_id = $1 AND discord_id = $2",
        guild_id, discord_id,
    )
    return row["total"] if row else 0


async def get_user_adjustments(conn, guild_id: str, discord_id: str) -> list:
    return await conn.fetch(
        "SELECT * FROM point_adjustments WHERE guild_id = $1 AND discord_id = $2 ORDER BY created_at DESC LIMIT 10",
        guild_id, discord_id,
    )


# ══════════════════════════════════════════════════════════════
#  RESET OPERATIONS
# ══════════════════════════════════════════════════════════════

async def reset_daily_solves(conn, guild_id: str, target_date: date) -> int:
    """Delete solves for problems assigned on target_date, solved within that IST day."""
    start_utc, end_utc = _day_window_utc(target_date)
    result = await conn.execute(
        """
        DELETE FROM solves
        WHERE guild_id = $1
          AND problem_db_id IN (SELECT id FROM problems WHERE guild_id = $1 AND assigned_date = $2)
          AND solved_at >= $3
          AND solved_at <= $4
        """,
        guild_id, target_date, start_utc, end_utc,
    )
    return int(result.split()[-1])


async def reset_current_week_solves(conn, guild_id: str) -> int:
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
    return int(result.split()[-1])


async def reset_current_month_solves(conn, guild_id: str) -> int:
    """
    DEPRECATED — use reset_month_solves_only() which accepts explicit date
    bounds and an optional exclude_week_id for safe, isolated monthly resets.
    Kept here so any external callers don't break immediately.
    """
    month = await conn.fetchrow(
        "SELECT start_date, end_date FROM months WHERE guild_id = $1 AND is_active = TRUE ORDER BY id DESC LIMIT 1",
        guild_id,
    )
    if not month:
        return 0
    week = await conn.fetchrow(
        "SELECT id FROM weeks WHERE guild_id = $1 AND is_active = TRUE ORDER BY id DESC LIMIT 1",
        guild_id,
    )
    return await reset_month_solves_only(
        conn,
        guild_id,
        month["start_date"],
        month["end_date"],
        protected_start=week["start_date"] if week else None,
        protected_end=week["end_date"]   if week else None,
    )


async def reset_month_solves_only(
    conn,
    guild_id:        str,
    month_start:     date,
    month_end:       date,
    protected_start: date | None = None,
    protected_end:   date | None = None,
) -> int:
    """
    Deletes ONLY the monthly-scope solves — weekly and daily leaderboards
    are completely untouched.

    Strategy (no data loss):
      • Target  : problems with assigned_date IN [month_start, month_end]
      • Exclude : problems with assigned_date IN [protected_start, protected_end]
                  i.e. the active week's window — those solves are preserved
                  so the weekly and daily leaderboards keep their data intact.
      • Uses date ranges only (not month_id) for the same robustness reason
        documented on get_monthly_leaderboard.

    No handles, users, problems, weeks, or out-of-scope solves are touched.
    """
    if protected_start is not None and protected_end is not None:
        result = await conn.execute(
            """
            DELETE FROM solves
            WHERE guild_id = $1
              AND problem_db_id IN (
                  SELECT p.id FROM problems p
                  WHERE p.guild_id = $1
                    AND p.assigned_date >= $2
                    AND p.assigned_date <= $3
                    AND (
                        p.week_id IS NULL
                        OR p.assigned_date < $4
                        OR p.assigned_date > $5
                    )
              )
            """,
            guild_id, month_start, month_end, protected_start, protected_end,
        )
    else:
        # No active week — safe to clear all solves in the month date range
        result = await conn.execute(
            """
            DELETE FROM solves
            WHERE guild_id = $1
              AND problem_db_id IN (
                  SELECT p.id FROM problems p
                  WHERE p.guild_id = $1
                    AND p.assigned_date >= $2
                    AND p.assigned_date <= $3
              )
            """,
            guild_id, month_start, month_end,
        )
    return int(result.split()[-1])


async def reset_all_solves(conn, guild_id: str) -> int:
    result = await conn.execute(
        "DELETE FROM solves WHERE guild_id = $1", guild_id,
    )
    return int(result.split()[-1])


async def reset_user_week_solves(conn, discord_id: str, guild_id: str) -> int:
    result = await conn.execute(
        """
        DELETE FROM solves
        WHERE discord_id = $1 AND guild_id = $2
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
    result = await conn.execute(
        "DELETE FROM solves WHERE discord_id = $1 AND guild_id = $2",
        discord_id, guild_id,
    )
    return int(result.split()[-1])


async def unmark_problem_solves(conn, problem_db_id: int, guild_id: str) -> int:
    result = await conn.execute(
        "DELETE FROM solves WHERE problem_db_id = $1 AND guild_id = $2",
        problem_db_id, guild_id,
    )
    return int(result.split()[-1])


async def reset_week_and_problems(conn, guild_id: str) -> dict:
    week = await conn.fetchrow(
        "SELECT id FROM weeks WHERE guild_id = $1 AND is_active = TRUE ORDER BY id DESC LIMIT 1",
        guild_id,
    )
    if not week:
        return {"solves": 0, "problems": 0, "week_id": None}
    week_id = week["id"]
    r1 = await conn.execute(
        "DELETE FROM solves WHERE problem_db_id IN (SELECT id FROM problems WHERE guild_id = $1 AND week_id = $2)",
        guild_id, week_id,
    )
    r2 = await conn.execute(
        "DELETE FROM problems WHERE guild_id = $1 AND week_id = $2", guild_id, week_id,
    )
    await conn.execute("UPDATE weeks SET is_active = FALSE WHERE id = $1", week_id)
    return {"solves": int(r1.split()[-1]), "problems": int(r2.split()[-1]), "week_id": week_id}

# ══════════════════════════════════════════════════════════════
#  BOT CONFIG  (generic key/value store — used by !setcookie)
# ══════════════════════════════════════════════════════════════

async def get_config(conn, key: str) -> str | None:
    """Return the stored value for `key`, or None if not set."""
    row = await conn.fetchrow("SELECT value FROM bot_config WHERE key = $1", key)
    return row["value"] if row else None


async def set_config(conn, key: str, value: str, updated_by: str):
    """Insert or overwrite a config value. No data loss — old value is replaced,
    never deleted as a row (so `updated_at` always reflects the latest write)."""
    await conn.execute(
        """
        INSERT INTO bot_config (key, value, updated_by, updated_at)
        VALUES ($1, $2, $3, NOW())
        ON CONFLICT (key) DO UPDATE
            SET value = EXCLUDED.value,
                updated_by = EXCLUDED.updated_by,
                updated_at = NOW()
        """,
        key, value, updated_by,
    )


async def delete_config(conn, key: str):
    await conn.execute("DELETE FROM bot_config WHERE key = $1", key)