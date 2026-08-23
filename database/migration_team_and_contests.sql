-- migration_team.sql — team roster, editable at runtime via !team.
--
-- The website previously hardcoded its team roster as a static TS array
-- (src/data/static.ts, TEAM), which meant adding a member required a code
-- change + redeploy on the site repo. This table + the !team command let an
-- admin add someone from Discord and have them show up on the site's Team
-- page on the next data refresh, no redeploy needed.
--
-- Run once: psql $DATABASE_URL -f database/migration_team.sql

CREATE TABLE IF NOT EXISTS team_members (
    id           SERIAL PRIMARY KEY,
    guild_id     TEXT NOT NULL,
    name         TEXT NOT NULL,
    role         TEXT NOT NULL,          -- free-text title shown on the card, e.g. "Dev Lead"
    linkedin_url TEXT NOT NULL,
    github_url   TEXT,                   -- optional
    sort_order   INTEGER NOT NULL DEFAULT 0,
    added_by     TEXT NOT NULL,          -- admin discord_id
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_team_members_guild ON team_members(guild_id, sort_order);

-- Admin-added contests (!newContest / !addcontest). These weren't actually
-- wired into /api/contests before — the old command appended to an
-- in-memory Python list that nothing ever read, so admin-added contests
-- silently never appeared on the site and vanished on every bot restart.
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

