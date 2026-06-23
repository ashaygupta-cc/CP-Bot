-- ============================================================
--  CP Discord Bot — Supabase / PostgreSQL Schema
--  Run this once in the Supabase SQL editor.
-- ============================================================

-- Discord users
CREATE TABLE IF NOT EXISTS users (
    discord_id      TEXT PRIMARY KEY,
    discord_username TEXT NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- One row per (user, platform) — stores the CP handle
CREATE TABLE IF NOT EXISTS handles (
    discord_id  TEXT NOT NULL REFERENCES users(discord_id) ON DELETE CASCADE,
    platform    TEXT NOT NULL,   -- 'cf' | 'lc' | 'cc' | 'atcoder'
    handle      TEXT NOT NULL,
    verified    BOOLEAN DEFAULT FALSE,
    linked_at   TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (discord_id, platform)
);

-- A "week" contest period set by admins
CREATE TABLE IF NOT EXISTS weeks (
    id          SERIAL PRIMARY KEY,
    guild_id    TEXT NOT NULL,
    label       TEXT NOT NULL,          -- e.g. "Week 1"
    start_date  DATE NOT NULL,
    end_date    DATE NOT NULL,
    is_active   BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Problems assigned to a week
CREATE TABLE IF NOT EXISTS problems (
    id              SERIAL PRIMARY KEY,
    guild_id        TEXT NOT NULL,
    week_id         INTEGER REFERENCES weeks(id) ON DELETE CASCADE,
    platform        TEXT NOT NULL,
    problem_id      TEXT NOT NULL,       -- e.g. '1234A', 'two-sum', 'CHEFEZ'
    title           TEXT,
    difficulty      TEXT NOT NULL DEFAULT 'medium',
    points          INTEGER NOT NULL,
    set_by          TEXT NOT NULL,       -- discord_id of admin
    assigned_date   DATE,               -- optional: specific day in the week
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (guild_id, week_id, platform, problem_id)
);

-- Recorded solves (one row per user per problem)
CREATE TABLE IF NOT EXISTS solves (
    id              SERIAL PRIMARY KEY,
    discord_id      TEXT NOT NULL REFERENCES users(discord_id),
    problem_db_id   INTEGER NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
    guild_id        TEXT NOT NULL,
    solved_at       TIMESTAMPTZ,        -- when the platform says they solved it
    points_awarded  INTEGER NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (discord_id, problem_db_id)
);

-- Per-guild difficulty → points config (overrides defaults)
CREATE TABLE IF NOT EXISTS difficulty_points (
    guild_id    TEXT NOT NULL,
    difficulty  TEXT NOT NULL,
    points      INTEGER NOT NULL,
    PRIMARY KEY (guild_id, difficulty)
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_solves_guild   ON solves(guild_id);
CREATE INDEX IF NOT EXISTS idx_solves_user    ON solves(discord_id);
CREATE INDEX IF NOT EXISTS idx_problems_week  ON problems(week_id);
CREATE INDEX IF NOT EXISTS idx_weeks_guild    ON weeks(guild_id);
CREATE INDEX IF NOT EXISTS idx_handles_plat   ON handles(platform);
