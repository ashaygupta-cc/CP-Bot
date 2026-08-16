-- ============================================================
--  CP Discord Bot v2 — Supabase / PostgreSQL Schema
--  Run this ONCE in the Supabase SQL Editor.
--  If upgrading from v1, run only the ALTER/CREATE sections
--  marked with "-- v2 NEW".
-- ============================================================

-- Discord users
CREATE TABLE IF NOT EXISTS users (
    discord_id       TEXT PRIMARY KEY,
    discord_username TEXT NOT NULL,
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

-- One row per (user, platform) — stores the CP handle
CREATE TABLE IF NOT EXISTS handles (
    discord_id  TEXT NOT NULL REFERENCES users(discord_id) ON DELETE CASCADE,
    platform    TEXT NOT NULL,
    handle      TEXT NOT NULL,
    verified    BOOLEAN DEFAULT FALSE,
    linked_at   TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (discord_id, platform)
);

-- A "week" contest period set by admins
CREATE TABLE IF NOT EXISTS weeks (
    id          SERIAL PRIMARY KEY,
    guild_id    TEXT NOT NULL,
    label       TEXT NOT NULL,
    start_date  DATE NOT NULL,
    end_date    DATE NOT NULL,
    is_active   BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- v2 NEW: months table (mirrors weeks)
CREATE TABLE IF NOT EXISTS months (
    id          SERIAL PRIMARY KEY,
    guild_id    TEXT NOT NULL,
    label       TEXT NOT NULL,          -- e.g. "June 2026"
    start_date  DATE NOT NULL,
    end_date    DATE NOT NULL,
    is_active   BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Problems assigned to a week; each problem has a specific day it belongs to
CREATE TABLE IF NOT EXISTS problems (
    id              SERIAL PRIMARY KEY,
    guild_id        TEXT NOT NULL,
    week_id         INTEGER REFERENCES weeks(id) ON DELETE CASCADE,
    month_id        INTEGER REFERENCES months(id) ON DELETE SET NULL,  -- v2 NEW
    platform        TEXT NOT NULL,
    problem_id      TEXT NOT NULL,
    title           TEXT,
    difficulty      TEXT NOT NULL DEFAULT 'medium',
    points          INTEGER NOT NULL,
    set_by          TEXT NOT NULL,
    assigned_date   DATE NOT NULL,      -- v2: NOW REQUIRED — the exact day this problem is for
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (guild_id, week_id, platform, problem_id)
);

-- Recorded solves
CREATE TABLE IF NOT EXISTS solves (
    id              SERIAL PRIMARY KEY,
    discord_id      TEXT NOT NULL REFERENCES users(discord_id),
    problem_db_id   INTEGER NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
    guild_id        TEXT NOT NULL,
    solved_at       TIMESTAMPTZ,
    points_awarded  INTEGER NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (discord_id, problem_db_id)
);

-- Per-guild difficulty → points config
CREATE TABLE IF NOT EXISTS difficulty_points (
    guild_id    TEXT NOT NULL,
    difficulty  TEXT NOT NULL,
    points      INTEGER NOT NULL,
    PRIMARY KEY (guild_id, difficulty)
);

-- v2 NEW: manual point adjustments (add/subtract/set)
CREATE TABLE IF NOT EXISTS point_adjustments (
    id          SERIAL PRIMARY KEY,
    guild_id    TEXT NOT NULL,
    discord_id  TEXT NOT NULL REFERENCES users(discord_id),
    delta       INTEGER NOT NULL,       -- positive = add, negative = subtract
    reason      TEXT,
    adjusted_by TEXT NOT NULL,          -- admin discord_id
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_solves_guild    ON solves(guild_id);
CREATE INDEX IF NOT EXISTS idx_solves_user     ON solves(discord_id);
CREATE INDEX IF NOT EXISTS idx_problems_week   ON problems(week_id);
CREATE INDEX IF NOT EXISTS idx_problems_date   ON problems(assigned_date);
CREATE INDEX IF NOT EXISTS idx_weeks_guild     ON weeks(guild_id);
CREATE INDEX IF NOT EXISTS idx_months_guild    ON months(guild_id);
CREATE INDEX IF NOT EXISTS idx_handles_plat    ON handles(platform);
CREATE INDEX IF NOT EXISTS idx_adjustments_user ON point_adjustments(discord_id, guild_id);
