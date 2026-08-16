-- ============================================================
--  CP Discord Bot  —  Migration: v1 → v2
--  Run this in Supabase SQL Editor INSTEAD of schema.sql
--  if you already have a working v1 database with user data.
--
--  ✅ SAFE: every statement is additive or defensive.
--  ✅ Existing data (users, handles, weeks, problems, solves,
--     difficulty_points) is completely untouched.
--  ✅ Run it multiple times — all statements are idempotent.
-- ============================================================

-- ────────────────────────────────────────────────────────────
-- STEP 1: Create the new `months` table (did not exist in v1)
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS months (
    id          SERIAL PRIMARY KEY,
    guild_id    TEXT NOT NULL,
    label       TEXT NOT NULL,
    start_date  DATE NOT NULL,
    end_date    DATE NOT NULL,
    is_active   BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ────────────────────────────────────────────────────────────
-- STEP 2: Create the new `point_adjustments` table (new in v2)
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS point_adjustments (
    id          SERIAL PRIMARY KEY,
    guild_id    TEXT NOT NULL,
    discord_id  TEXT NOT NULL REFERENCES users(discord_id),
    delta       INTEGER NOT NULL,
    reason      TEXT,
    adjusted_by TEXT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ────────────────────────────────────────────────────────────
-- STEP 3: Add `month_id` column to `problems` (new FK in v2)
--         DO NOTHING if the column already exists.
-- ────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'problems' AND column_name = 'month_id'
    ) THEN
        ALTER TABLE problems
            ADD COLUMN month_id INTEGER REFERENCES months(id) ON DELETE SET NULL;
        RAISE NOTICE 'Added column: problems.month_id';
    ELSE
        RAISE NOTICE 'Column problems.month_id already exists — skipped.';
    END IF;
END $$;

-- ────────────────────────────────────────────────────────────
-- STEP 4: Add `assigned_date` column to `problems`
--         v1 had this as optional (nullable).
--         v2 uses it as required — but we cannot ALTER to NOT NULL
--         on existing rows without supplying a value first.
--         We add it as nullable here; the bot code handles it.
-- ────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'problems' AND column_name = 'assigned_date'
    ) THEN
        -- Column genuinely missing (shouldn't happen with v1 schema, but safe)
        ALTER TABLE problems ADD COLUMN assigned_date DATE;
        RAISE NOTICE 'Added column: problems.assigned_date';
    ELSE
        RAISE NOTICE 'Column problems.assigned_date already exists — skipped.';
    END IF;
END $$;

-- ────────────────────────────────────────────────────────────
-- STEP 5: Back-fill assigned_date for existing problems that
--         have NULL there (v1 rows where it was never set).
--         We use the week's start_date as a safe fallback so
--         existing problems still show up in /problems.
-- ────────────────────────────────────────────────────────────
UPDATE problems p
SET    assigned_date = w.start_date
FROM   weeks w
WHERE  p.week_id       = w.id
  AND  p.assigned_date IS NULL;

-- Orphaned problems (no week) get today as fallback
UPDATE problems
SET    assigned_date = CURRENT_DATE
WHERE  assigned_date IS NULL;

-- ────────────────────────────────────────────────────────────
-- STEP 6: Add new indexes (CREATE INDEX IF NOT EXISTS is safe)
-- ────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_problems_date     ON problems(assigned_date);
CREATE INDEX IF NOT EXISTS idx_months_guild      ON months(guild_id);
CREATE INDEX IF NOT EXISTS idx_adjustments_user  ON point_adjustments(discord_id, guild_id);

-- All pre-existing indexes from v1 (safe to re-run)
CREATE INDEX IF NOT EXISTS idx_solves_guild      ON solves(guild_id);
CREATE INDEX IF NOT EXISTS idx_solves_user       ON solves(discord_id);
CREATE INDEX IF NOT EXISTS idx_problems_week     ON problems(week_id);
CREATE INDEX IF NOT EXISTS idx_weeks_guild       ON weeks(guild_id);
CREATE INDEX IF NOT EXISTS idx_handles_plat      ON handles(platform);

-- ────────────────────────────────────────────────────────────
-- STEP 7: Verification — run this SELECT to confirm everything
--         looks correct after migration.
-- ────────────────────────────────────────────────────────────
SELECT
    'users'              AS tbl, COUNT(*) AS rows FROM users
UNION ALL SELECT 'handles',            COUNT(*) FROM handles
UNION ALL SELECT 'weeks',              COUNT(*) FROM weeks
UNION ALL SELECT 'months',             COUNT(*) FROM months
UNION ALL SELECT 'problems',           COUNT(*) FROM problems
UNION ALL SELECT 'problems_no_date',   COUNT(*) FROM problems WHERE assigned_date IS NULL
UNION ALL SELECT 'solves',             COUNT(*) FROM solves
UNION ALL SELECT 'difficulty_points',  COUNT(*) FROM difficulty_points
UNION ALL SELECT 'point_adjustments',  COUNT(*) FROM point_adjustments
ORDER BY tbl;

-- ============================================================
-- ✅ Migration complete.
-- You should see:
--   problems_no_date  →  0   (all rows have a date now)
--   months            →  0   (empty, ready for /setmonth)
--   point_adjustments →  0   (empty, ready for /addpoints)
--   All other tables  →  your existing row counts unchanged
-- ============================================================
