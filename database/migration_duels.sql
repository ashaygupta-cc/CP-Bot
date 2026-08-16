-- ============================================================
--  CP Bot — Duel System Migration (v2)
--  Run ONCE in the Supabase SQL Editor.
--
--  Everything here is CREATE TABLE IF NOT EXISTS / CREATE INDEX
--  IF NOT EXISTS — 100% additive, safe to re-run, zero impact on
--  any existing table (users, handles, weeks, problems, solves, …).
--
--  Changes in v2:
--  • duel_ratings default rating changed from 1200 to 800 (Newbie)
--  • All other schema unchanged
-- ============================================================

-- Per-guild duel settings (K-factors, ICPC range, LC points, bot toggle, etc.)
-- Same key/value pattern as bot_config, but scoped per guild.
CREATE TABLE IF NOT EXISTS duel_config (
    guild_id    TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    updated_by  TEXT,
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (guild_id, key)
);

-- One row per (user, guild, mode). mode ∈
--   cp_blitz, cp_duel, dsa_blitz, dsa_duel, icpc_blitz, icpc_duel
CREATE TABLE IF NOT EXISTS duel_ratings (
    discord_id   TEXT NOT NULL REFERENCES users(discord_id),
    guild_id     TEXT NOT NULL,
    mode         TEXT NOT NULL,
    rating       INTEGER NOT NULL DEFAULT 800,   -- Codeforces Newbie baseline
    wins         INTEGER NOT NULL DEFAULT 0,
    losses       INTEGER NOT NULL DEFAULT 0,
    draws        INTEGER NOT NULL DEFAULT 0,
    streak       INTEGER NOT NULL DEFAULT 0,      -- positive = win streak, negative = loss streak
    bot_matches  INTEGER NOT NULL DEFAULT 0,       -- practice matches vs bot (informational)
    updated_at   TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (discord_id, guild_id, mode)
);

-- A single duel/blitz match (vs a human OR vs the bot opponent).
CREATE TABLE IF NOT EXISTS duels (
    id              SERIAL PRIMARY KEY,
    guild_id        TEXT NOT NULL,
    mode            TEXT NOT NULL,
    player1_id      TEXT NOT NULL,               -- challenger
    player2_id      TEXT,                        -- NULL when is_bot_match = TRUE
    is_bot_match    BOOLEAN NOT NULL DEFAULT FALSE,
    bot_rating      INTEGER,                     -- the bot's target rating for this match
    status          TEXT NOT NULL DEFAULT 'pending',
                    -- pending | active | finished | declined | cancelled | timeout
    channel_id      TEXT,
    current_game    INTEGER NOT NULL DEFAULT 1,
    total_games     INTEGER NOT NULL DEFAULT 1,
    winner_id       TEXT,                        -- discord_id, 'BOT', or NULL (draw)
    p1_games_won    INTEGER NOT NULL DEFAULT 0,
    p2_games_won    INTEGER NOT NULL DEFAULT 0,
    duel_number     INTEGER,                     -- display slot, e.g. "CP Duel #3"
    started_at      TIMESTAMPTZ,
    ended_at        TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Each problem within a duel (multiple rows for Bo3, etc.)
CREATE TABLE IF NOT EXISTS duel_problems (
    id              SERIAL PRIMARY KEY,
    duel_id         INTEGER NOT NULL REFERENCES duels(id) ON DELETE CASCADE,
    game_number     INTEGER NOT NULL DEFAULT 1,
    platform        TEXT NOT NULL,                -- cf | lc
    problem_id      TEXT NOT NULL,                -- e.g. "1234A" or "two-sum"
    title           TEXT,
    difficulty_label TEXT,                        -- easy/medium/hard (lc) or rating (cf)
    rating          INTEGER,
    url             TEXT,
    topic           TEXT,                         -- e.g. "DP, Graphs" for CF/ICPC picks
    deadline_at     TIMESTAMPTZ,                  -- when this game's timer expires
    p1_solved_at    TIMESTAMPTZ,
    p2_solved_at    TIMESTAMPTZ,                  -- for bot matches: bot's simulated solve time
    game_winner     TEXT,                         -- discord_id, 'BOT', 'DRAW', or NULL (in progress)
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Weighted repeat-avoidance: which problems a pair has already faced.
-- pair_key = sorted "user1_user2" joined by "_", or "BOT_<user_id>" for bot matches.
CREATE TABLE IF NOT EXISTS duel_problem_history (
    pair_key    TEXT NOT NULL,
    platform    TEXT NOT NULL,
    problem_id  TEXT NOT NULL,
    used_at     TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (pair_key, platform, problem_id)
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_duel_ratings_guild_mode ON duel_ratings(guild_id, mode);
CREATE INDEX IF NOT EXISTS idx_duels_guild            ON duels(guild_id);
CREATE INDEX IF NOT EXISTS idx_duels_status           ON duels(status);
CREATE INDEX IF NOT EXISTS idx_duels_players          ON duels(player1_id, player2_id);
CREATE INDEX IF NOT EXISTS idx_duel_problems_duel      ON duel_problems(duel_id);
CREATE INDEX IF NOT EXISTS idx_duel_problem_history_pk ON duel_problem_history(pair_key, platform);

-- Update existing installs: if duel_ratings table exists, ensure default is 800 for new rows
-- (old rows keep their existing rating)
ALTER TABLE duel_ratings ALTER COLUMN rating SET DEFAULT 800;

-- Verify:
-- SELECT COUNT(*) FROM duels;
-- SELECT COUNT(*) FROM duel_ratings;
