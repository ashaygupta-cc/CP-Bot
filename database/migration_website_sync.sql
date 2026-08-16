-- migration_website_sync.sql
--
-- Mirrors configured Discord channels into Postgres so the website reads from
-- the database instead of hitting the Discord API on every page view.
--
-- Discord stays the source of truth: every row here is written by the
-- website_sync cog from a real Discord event (or a backfill), and is deleted
-- when the message is deleted. Nothing on the website ever writes here.
--
-- Safe to run repeatedly. Adds tables only — no existing table is altered.

CREATE TABLE IF NOT EXISTS discord_messages (
    guild_id        TEXT        NOT NULL,
    channel_id      TEXT        NOT NULL,
    message_id      TEXT        PRIMARY KEY,

    -- Stable key from config (e.g. 'server_updates'), so the website can ask
    -- for a channel by name and never hardcodes a numeric id.
    channel_key     TEXT        NOT NULL,

    author_id       TEXT,
    author_name     TEXT,
    author_avatar   TEXT,
    author_is_bot   BOOLEAN     NOT NULL DEFAULT FALSE,

    content         TEXT        NOT NULL DEFAULT '',
    -- Full embed/attachment payloads, rendered client-side by the website.
    embeds          JSONB       NOT NULL DEFAULT '[]'::jsonb,
    attachments     JSONB       NOT NULL DEFAULT '[]'::jsonb,
    reactions       JSONB       NOT NULL DEFAULT '[]'::jsonb,

    -- Forum/thread support. thread_id is set for messages inside a thread;
    -- thread_parent_id points at the channel the thread lives in.
    thread_id       TEXT,
    thread_name     TEXT,
    thread_parent_id TEXT,

    is_pinned       BOOLEAN     NOT NULL DEFAULT FALSE,
    reply_to_id     TEXT,

    created_at      TIMESTAMPTZ NOT NULL,
    edited_at       TIMESTAMPTZ,
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- The website's main access pattern: newest-first within one channel.
CREATE INDEX IF NOT EXISTS idx_dmsg_channel_time
    ON discord_messages (channel_key, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_dmsg_guild_channel
    ON discord_messages (guild_id, channel_id);

-- Editorial lookup joins threads to daily problems by date.
CREATE INDEX IF NOT EXISTS idx_dmsg_thread
    ON discord_messages (thread_id)
    WHERE thread_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_dmsg_pinned
    ON discord_messages (channel_key, is_pinned)
    WHERE is_pinned = TRUE;

-- Full-text search across synced content (Community search, docs search).
CREATE INDEX IF NOT EXISTS idx_dmsg_content_fts
    ON discord_messages
    USING GIN (to_tsvector('english', content));


-- ── Thread registry ─────────────────────────────────────────────────────────
-- daily-editorials uses one thread per day. Tracking threads separately lets
-- the website list "which days have an editorial" without scanning messages.

CREATE TABLE IF NOT EXISTS discord_threads (
    thread_id       TEXT        PRIMARY KEY,
    guild_id        TEXT        NOT NULL,
    parent_id       TEXT        NOT NULL,
    channel_key     TEXT        NOT NULL,
    name            TEXT        NOT NULL,

    -- Parsed out of the thread name when it looks like a date, so editorials
    -- can be matched to a daily problem without a manual mapping.
    editorial_date  DATE,

    message_count   INTEGER     NOT NULL DEFAULT 0,
    has_pdf         BOOLEAN     NOT NULL DEFAULT FALSE,
    is_archived     BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL,
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dthread_key ON discord_threads (channel_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_dthread_date ON discord_threads (editorial_date)
    WHERE editorial_date IS NOT NULL;


-- ── Guild stats snapshot ────────────────────────────────────────────────────
-- Member counts change constantly and the website shows them on every page.
-- One row per guild, overwritten by the sync cog on a timer.

CREATE TABLE IF NOT EXISTS guild_snapshot (
    guild_id        TEXT        PRIMARY KEY,
    name            TEXT,
    icon_url        TEXT,
    member_count    INTEGER     NOT NULL DEFAULT 0,
    online_count    INTEGER     NOT NULL DEFAULT 0,
    boost_count     INTEGER     NOT NULL DEFAULT 0,
    boost_tier      INTEGER     NOT NULL DEFAULT 0,
    channel_count   INTEGER     NOT NULL DEFAULT 0,
    role_count      INTEGER     NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
