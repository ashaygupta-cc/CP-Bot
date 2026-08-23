"""
database/queries.py — Every DB operation in one place.
v2.2: monthly_solves snapshot table — weekly resets no longer affect monthly counts.
      Added day_number_display() helper to cap day display at week length.
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


def day_number_display(week_start: date, week_end: date, today: date) -> tuple[int, int]:
    """
    Returns (display_day, total_days) for the !problems header.

    Rules:
      - total_days  = (week_end - week_start).days + 1  (always correct)
      - display_day = clamped to [1, total_days] so you never see "Day 8 of 7"
                      even if today is past the week's end_date.

    Example:
      week: 2026-06-22 → 2026-06-28  (7 days)
      today: 2026-06-29  →  display_day = 7  (capped), total_days = 7
      → shows "Day 7 of 7 — Week complete" instead of "Day 8 of 7"
    """
    total_days  = (week_end - week_start).days + 1
    raw_day     = (today - week_start).days + 1          # 1-based, may exceed total
    display_day = max(1, min(raw_day, total_days))       # clamp to [1, total_days]
    return display_day, total_days


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
#  TEAM MEMBERS (website /team page — see database/migration_team.sql)
# ══════════════════════════════════════════════════════════════

async def add_team_member(
    conn, guild_id: str, name: str, role: str,
    linkedin_url: str, github_url: str | None, added_by: str,
) -> int:
    """Adds a team member. If a member with the same name (case-insensitive)
    already exists for this guild, updates their card in place instead of
    creating a duplicate — running !team again for someone just edits them."""
    existing = await conn.fetchrow(
        "SELECT id FROM team_members WHERE guild_id = $1 AND LOWER(name) = LOWER($2)",
        guild_id, name,
    )
    if existing:
        await conn.execute(
            """UPDATE team_members
               SET role = $1, linkedin_url = $2, github_url = $3, added_by = $4
               WHERE id = $5""",
            role, linkedin_url, github_url, added_by, existing["id"],
        )
        return existing["id"]

    next_order = await conn.fetchval(
        "SELECT COALESCE(MAX(sort_order), 0) + 1 FROM team_members WHERE guild_id = $1",
        guild_id,
    )
    row = await conn.fetchrow(
        """INSERT INTO team_members (guild_id, name, role, linkedin_url, github_url, sort_order, added_by)
           VALUES ($1, $2, $3, $4, $5, $6, $7)
           RETURNING id""",
        guild_id, name, role, linkedin_url, github_url, next_order, added_by,
    )
    return row["id"]


async def get_team_members(conn, guild_id: str) -> list:
    return await conn.fetch(
        """SELECT id, name, role, linkedin_url, github_url, sort_order, created_at
           FROM team_members WHERE guild_id = $1
           ORDER BY sort_order ASC, created_at ASC""",
        guild_id,
    )


async def remove_team_member(conn, guild_id: str, name: str) -> bool:
    result = await conn.execute(
        "DELETE FROM team_members WHERE guild_id = $1 AND LOWER(name) = LOWER($2)",
        guild_id, name,
    )
    return result.endswith(" 1")


# ══════════════════════════════════════════════════════════════
#  CUSTOM CONTESTS (!newContest / !addcontest)
# ══════════════════════════════════════════════════════════════

async def add_custom_contest(
    conn, guild_id: str, name: str, url: str,
    start_ts: float, added_by: str, duration: int = 7200,
) -> int:
    row = await conn.fetchrow(
        """INSERT INTO custom_contests (guild_id, name, url, start_ts, duration, added_by)
           VALUES ($1, $2, $3, $4, $5, $6)
           RETURNING id""",
        guild_id, name, url, start_ts, duration, added_by,
    )
    return row["id"]


async def get_upcoming_custom_contests(conn, guild_id: str, now_ts: float) -> list:
    """Only future contests — past ones age out of the feed on their own."""
    return await conn.fetch(
        """SELECT id, name, url, start_ts, duration
           FROM custom_contests
           WHERE guild_id = $1 AND start_ts > $2
           ORDER BY start_ts ASC""",
        guild_id, now_ts,
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
    """Soft-remove by setting week_id = NULL so solves aren't cascade-deleted."""
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
    month_id:      int | None = None,   # v2.2: pass the active month_id
) -> bool:
    """
    Returns True if newly recorded, False if already existed.

    v2.2: Also writes to monthly_solves so that monthly leaderboard
    data survives weekly resets. The two tables are written in the
    same logical operation — if either INSERT fails the solve is not
    double-counted (UNIQUE constraints on both tables).
    """
    try:
        await conn.execute(
            """
            INSERT INTO solves (discord_id, problem_db_id, guild_id, solved_at, points_awarded)
            VALUES ($1, $2, $3, $4, $5)
            """,
            discord_id, problem_db_id, guild_id, solved_at, points,
        )
    except asyncpg.UniqueViolationError:
        return False   # already in solves → monthly_solves already has it too

    # Mirror into monthly_solves (ignore conflict — idempotent)
    await conn.execute(
        """
        INSERT INTO monthly_solves
            (discord_id, problem_db_id, guild_id, month_id, solved_at, points_awarded)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (discord_id, problem_db_id) DO NOTHING
        """,
        discord_id, problem_db_id, guild_id, month_id, solved_at, points,
    )
    return True


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
    Reads from `solves` — daily is never affected by !resetmonth.
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
    Points from all solves within the week's problems PLUS manual point_adjustments
    that were created within that week's date range.

    Adjustment scoping: only adjustments with created_at inside
    [max(week_start 00:00 IST, week's created_at), week_end 23:59 IST] count
    for this week. The max() matters when an admin gives an adjustment
    BEFORE running !setweek — that adjustment's created_at is earlier than
    the week row's own created_at, so it's excluded from weekly (it still
    counts on monthly, since monthly scoping is unaffected by this).
    Adjustments from prior or future weeks are excluded so they never
    bleed across week boundaries.

    Reads from `solves` — the live table cleared on !resetweek.
    """
    week_row = await conn.fetchrow(
        "SELECT start_date, end_date, created_at FROM weeks WHERE id = $1", week_id
    )
    if not week_row:
        return []

    week_start_utc, _            = _day_window_utc(week_row["start_date"])
    _,              week_end_utc = _day_window_utc(week_row["end_date"])

    # Adjustments given before the week row was actually created (e.g. admin
    # gave points earlier the same day, then ran !setweek) must NOT count on
    # weekly — they only count on monthly. So the real lower bound for weekly
    # adjustments is whichever is LATER: the week's start-of-day, or the
    # moment the week was created in the DB.
    week_start_utc = max(week_start_utc, week_row["created_at"])

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
              AND created_at >= $3
              AND created_at <= $4
            GROUP BY discord_id
        ) adj ON adj.discord_id = sv.discord_id
        WHERE COALESCE(sv.solve_pts, 0) + COALESCE(adj.delta, 0) > 0
        ORDER BY total DESC
        """,
        guild_id, week_id, week_start_utc, week_end_utc,
    )


async def get_monthly_leaderboard(
    conn,
    guild_id:        str,
    month_start:     date,
    month_end:       date,
    exclude_week_id: int | None = None,   # kept for API compat, unused
) -> list:
    """
    v2.2 FIX: Reads from `monthly_solves` instead of `solves` so weekly
    resets never affect monthly solve counts.

    Adjustment scoping: only adjustments with created_at inside
    [month_start 00:00 IST, month_end 23:59 IST] count for this month.
    Adjustments from outside the month window (e.g. previous months)
    are excluded so they don't bleed across month boundaries.
    BUT they DO still accumulate correctly — e.g. an adjustment given
    in Week 1 of the month is included in the month total because
    Week 1 falls inside the month's date range.
    """
    month_start_utc, _             = _day_window_utc(month_start)
    _,               month_end_utc = _day_window_utc(month_end)

    return await conn.fetch(
        """
        SELECT
            COALESCE(sv.discord_id, adj.discord_id) AS discord_id,
            COALESCE(sv.solve_pts, 0) + COALESCE(adj.delta, 0) AS total,
            COALESCE(sv.solved_count, 0) AS solved_count
        FROM (
            SELECT ms.discord_id,
                   SUM(ms.points_awarded) AS solve_pts,
                   COUNT(*) AS solved_count
            FROM monthly_solves ms
            JOIN problems p ON p.id = ms.problem_db_id
            WHERE ms.guild_id = $1
              AND p.assigned_date >= $2
              AND p.assigned_date <= $3
            GROUP BY ms.discord_id
        ) sv
        FULL OUTER JOIN (
            -- Only adjustments given during this month's window
            SELECT discord_id, SUM(delta) AS delta
            FROM point_adjustments
            WHERE guild_id = $1
              AND created_at >= $4
              AND created_at <= $5
            GROUP BY discord_id
        ) adj ON adj.discord_id = sv.discord_id
        WHERE COALESCE(sv.solve_pts, 0) + COALESCE(adj.delta, 0) > 0
        ORDER BY total DESC
        """,
        guild_id, month_start, month_end, month_start_utc, month_end_utc,
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
    """Delete solves for problems assigned on target_date, solved within that IST day.
    Does NOT touch monthly_solves — daily reset never affects monthly counts."""
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
    """
    Clears weekly solve records ONLY (from `solves` table).
    monthly_solves is intentionally NOT touched here — that is what
    fixes the "monthly shows 0 solved after !resetweek" bug.
    """
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
    DEPRECATED — use reset_month_solves_only().
    Kept so external callers don't break.
    """
    month = await conn.fetchrow(
        "SELECT start_date, end_date FROM months WHERE guild_id = $1 AND is_active = TRUE ORDER BY id DESC LIMIT 1",
        guild_id,
    )
    if not month:
        return 0
    return await reset_month_solves_only(
        conn, guild_id, month["start_date"], month["end_date"],
    )


async def reset_month_solves_only(
    conn,
    guild_id:        str,
    month_start:     date,
    month_end:       date,
    protected_start: date | None = None,   # kept for API compat, no longer used
    protected_end:   date | None = None,   # kept for API compat, no longer used
) -> int:
    """
    v2.2: Clears monthly_solves for the given date range.
    The `solves` table (weekly/daily) is completely untouched.
    protected_start/protected_end params are kept for compatibility
    but are no longer needed — the two tables are now separate.
    """
    result = await conn.execute(
        """
        DELETE FROM monthly_solves
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
    """Nuclear wipe — clears both solves and monthly_solves."""
    await conn.execute("DELETE FROM monthly_solves WHERE guild_id = $1", guild_id)
    result = await conn.execute(
        "DELETE FROM solves WHERE guild_id = $1", guild_id,
    )
    return int(result.split()[-1])


async def reset_user_week_solves(conn, discord_id: str, guild_id: str) -> int:
    """Clears one user's weekly solves. monthly_solves preserved."""
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
    """Clears all solves for one user — both tables."""
    await conn.execute(
        "DELETE FROM monthly_solves WHERE discord_id = $1 AND guild_id = $2",
        discord_id, guild_id,
    )
    result = await conn.execute(
        "DELETE FROM solves WHERE discord_id = $1 AND guild_id = $2",
        discord_id, guild_id,
    )
    return int(result.split()[-1])


async def unmark_problem_solves(conn, problem_db_id: int, guild_id: str) -> int:
    """Remove all solves for one problem — both tables."""
    await conn.execute(
        "DELETE FROM monthly_solves WHERE problem_db_id = $1 AND guild_id = $2",
        problem_db_id, guild_id,
    )
    result = await conn.execute(
        "DELETE FROM solves WHERE problem_db_id = $1 AND guild_id = $2",
        problem_db_id, guild_id,
    )
    return int(result.split()[-1])


async def reset_week_and_problems(conn, guild_id: str) -> dict:
    """!resetweekfull — deletes solves + problems + deactivates week.
    monthly_solves rows are deleted via ON DELETE CASCADE on problems.id."""
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
    row = await conn.fetchrow("SELECT value FROM bot_config WHERE key = $1", key)
    return row["value"] if row else None


async def set_config(conn, key: str, value: str, updated_by: str):
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