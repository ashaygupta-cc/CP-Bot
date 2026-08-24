-- migration_team_contests_botlog.sql — team roster, custom contests, bot stats logging.

CREATE TABLE IF NOT EXISTS team_members (
    id           SERIAL PRIMARY KEY,
    guild_id     TEXT NOT NULL,
    name         TEXT NOT NULL,          -- free-text title shown on the card, e.g. "Dev Lead"
    role         TEXT NOT NULL,
    linkedin_url TEXT NOT NULL,
    github_url   TEXT,                   -- optional
    sort_order   INTEGER NOT NULL DEFAULT 0,
    added_by     TEXT NOT NULL,          -- admin discord_id
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_team_members_guild ON team_members(guild_id, sort_order);

CREATE TABLE IF NOT EXISTS custom_contests (
    id          SERIAL PRIMARY KEY,
    guild_id    TEXT NOT NULL,
    name        TEXT NOT NULL,
    url         TEXT NOT NULL,
    start_ts    DOUBLE PRECISION NOT NULL,
    duration    INTEGER NOT NULL DEFAULT 7200,
    added_by    TEXT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_custom_contests_guild ON custom_contests(guild_id, start_ts);

CREATE TABLE IF NOT EXISTS bot_match_log (
    id              SERIAL PRIMARY KEY,
    duel_id         INTEGER,
    bot_rating      INTEGER NOT NULL,
    problem_rating  INTEGER NOT NULL,
    platform        TEXT NOT NULL,        -- 'cf' | 'lc'
    solved          BOOLEAN NOT NULL,
    solve_seconds   DOUBLE PRECISION,     -- NULL when unsolved
    time_limit_seconds INTEGER NOT NULL,
    logged_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bot_match_log_time ON bot_match_log(logged_at);
