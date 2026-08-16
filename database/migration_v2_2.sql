-- ============================================================
--  CP Bot v2.2 Migration — Monthly Solve Snapshot
--  Run ONCE in Supabase SQL Editor.
--
--  WHY THIS EXISTS:
--    The `solves` table is the single source of truth for both
--    weekly AND monthly leaderboards. When !resetweek deletes
--    rows from `solves`, it also wipes the monthly solve counts
--    because the monthly leaderboard queries the same rows.
--
--  FIX:
--    Add a `monthly_solves` snapshot table. Whenever a solve is
--    recorded (record_solve), a matching row is also written here
--    with the month_id. This table is NEVER touched by !resetweek
--    or !resetdaily — only !resetmonth clears it.
--
--    The monthly leaderboard now reads from `monthly_solves`
--    instead of `solves`, so weekly resets have zero effect on it.
--
--  ZERO DATA LOSS:
--    - No existing table is altered, renamed, or dropped.
--    - Existing solves are backfilled into monthly_solves below.
--    - All existing points/adjustments are preserved.
-- ============================================================

-- 1. Create the snapshot table
CREATE TABLE IF NOT EXISTS monthly_solves (
    id              SERIAL PRIMARY KEY,
    discord_id      TEXT NOT NULL REFERENCES users(discord_id),
    problem_db_id   INTEGER NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
    guild_id        TEXT NOT NULL,
    month_id        INTEGER REFERENCES months(id) ON DELETE CASCADE,
    solved_at       TIMESTAMPTZ,
    points_awarded  INTEGER NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (discord_id, problem_db_id)   -- one row per user per problem, same as solves
);

CREATE INDEX IF NOT EXISTS idx_monthly_solves_guild   ON monthly_solves(guild_id);
CREATE INDEX IF NOT EXISTS idx_monthly_solves_user    ON monthly_solves(discord_id);
CREATE INDEX IF NOT EXISTS idx_monthly_solves_month   ON monthly_solves(month_id);
CREATE INDEX IF NOT EXISTS idx_monthly_solves_problem ON monthly_solves(problem_db_id);

-- 2. Backfill: copy existing solves into monthly_solves.
--    We join problems → months to find the correct month_id for each solve.
--    Solves where the problem has no month_id are still inserted (month_id = NULL)
--    so no historical data is lost.
INSERT INTO monthly_solves (
    discord_id, problem_db_id, guild_id, month_id, solved_at, points_awarded, created_at
)
SELECT
    s.discord_id,
    s.problem_db_id,
    s.guild_id,
    p.month_id,          -- may be NULL for older problems — that's fine
    s.solved_at,
    s.points_awarded,
    s.created_at
FROM solves s
JOIN problems p ON p.id = s.problem_db_id
ON CONFLICT (discord_id, problem_db_id) DO NOTHING;  -- safe to re-run

-- Done. Verify with:
-- SELECT COUNT(*) FROM monthly_solves;
-- SELECT COUNT(*) FROM solves;
-- (counts should be equal or close — any difference means solves with
--  no matching problem row, which are orphans that can be ignored.)
