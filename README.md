# CP-Bot — Binary Beats Competitive Programming Discord Bot

> **Version 13.0.1** · Python 3.14 · discord.py 2.3+ · PostgreSQL (Supabase + Neon)

A full-featured Discord bot powering the **Binary Beats** competitive programming community. Tracks daily problem solves across four platforms, runs 1v1 rated duel matches, maintains daily/weekly/monthly leaderboards, mirrors server channels to a REST API for the website, and handles member onboarding — all within a single production-ready async Python application.

> **Setting up for the first time?** The [Complete Setup Guide](SETUP_README.md) covers Discord role hierarchy, channel permissions, environment variables, and a step-by-step first-run checklist.

---

## Table of Contents

1. [Features Overview](#features-overview)
2. [Architecture](#architecture)
3. [Technology Stack](#technology-stack)
4. [Project Structure](#project-structure)
5. [Database Schema](#database-schema)
6. [REST API Reference](#rest-api-reference)
7. [Bot Commands](#bot-commands)
8. [Background Tasks & Automation](#background-tasks--automation)
9. [Duel System Deep Dive](#duel-system-deep-dive)
10. [Environment Variables](#environment-variables)
11. [Deployment (Render)](#deployment-render)
12. [Local Setup](#local-setup)
13. [Version History](#version-history)

---

## Features Overview

| Feature | Description |
|---|---|
| **Multi-platform solve tracking** | Codeforces, LeetCode, CodeChef, AtCoder |
| **Daily/Weekly/Monthly leaderboards** | Fully scoped, auto-reset, persistent |
| **1v1 Duel System** | CP · DSA · ICPC families × Blitz & Duel formats, real Elo |
| **Bot opponent** | AI-simulated opponent auto-matched on no human response |
| **Contest reminders** | Fetches upcoming contests from CF/LC/CC/AtCoder, posts 12h/1h warnings |
| **Inactivity tracker** | Monitors member solve history, posts to dedicated channel on 15/20/25/30 of each month |
| **LinkedIn verification** | Auto-grants Member role on join; manual reverify flow available |
| **Website REST API** | 40+ public + internal endpoints served from the same process |
| **Channel mirroring** | 14 channels synced to Postgres for the website frontend |
| **Manual point management** | Admin add/subtract/override points with full audit log |
| **AtCoder cookie auth** | Stored REVEL_SESSION session for authenticated AtCoder checks |
| **Webhook branding** | All messages posted through named webhooks (Z4s / Contest Reminder / Zodiac) |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         bot.py  (entry point)                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ discord.py Bot  (prefix: !)                              │   │
│  │  ├── cogs/registry.py      — handle registration         │   │
│  │  ├── cogs/admin.py         — server configuration        │   │
│  │  ├── cogs/problems.py      — problem management          │   │
│  │  ├── cogs/checker.py       — solve verification          │   │
│  │  ├── cogs/leaderboard.py   — leaderboard views           │   │
│  │  ├── cogs/submissions.py   — recent submission lookup    │   │
│  │  ├── cogs/reset.py         — data management             │   │
│  │  ├── cogs/points.py        — manual point adjustments    │   │
│  │  ├── cogs/verification.py  — member onboarding           │   │
│  │  ├── cogs/inactivity.py    — inactivity monitoring       │   │
│  │  ├── cogs/contests.py      — contest reminders           │   │
│  │  ├── cogs/duels.py         — 1v1 match system            │   │
│  │  └── cogs/website_sync.py  — channel → Postgres mirror   │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ api_server.py  (aiohttp, port 10000)                     │   │
│  │  Public REST API — 40+ endpoints, CORS, key-protected    │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
         │                          │
         ▼                          ▼
┌────────────────┐      ┌────────────────────────────┐
│  Supabase PG   │      │  Neon PostgreSQL (2 pools)  │
│  (main DB)     │      │  CF pool · LC pool          │
│  users,solves  │      │  problem metadata / stmts   │
│  handles,duels │      │                             │
└────────────────┘      └────────────────────────────┘
         │
         ▼
┌────────────────────────────────────────────────────┐
│  Platform Adapters  (platforms/)                   │
│  codeforces.py · leetcode.py · codechef.py         │
│  atcoder.py · duel_cf_pool.py · duel_lc_pool.py    │
└────────────────────────────────────────────────────┘
```

### Key Design Decisions

- **Single-process, async-first** — the Discord bot and the HTTP API server run inside the same Python process as co-operating `asyncio` tasks. No inter-process communication needed.
- **Bot is the source of truth** — the REST API is strictly read-only over tables the bot already writes. Nothing in `api_server.py` mutates rating, points, or duel state directly (except the web-arena duel endpoints, which call the same duel queries the Discord cogs use).
- **Three PostgreSQL pools** — one Supabase pool for all live bot data, one Neon pool for the 10k+ Codeforces problem statement cache, one Neon pool for 2.6k LeetCode problem metadata.
- **Webhook branding** — every public-facing message is posted via a named webhook (`Z4s`, `Contest Reminder`, `Zodiac`, `Your Helper`) rather than the raw bot identity, for a polished look.
- **Graceful rate-limit recovery** — startup delay, per-user `!check` quotas (3/day, 1h cooldown), sequential bulk checks with per-member delays, and CF bulk-fetch fallback (zero per-problem API calls on block).

---

## Technology Stack

| Layer | Technology |
|---|---|
| Language | Python 3.14 |
| Discord | discord.py ≥ 2.3.2 |
| HTTP server | aiohttp ≥ 3.9.0 |
| Database driver | asyncpg ≥ 0.29.0 |
| Environment | python-dotenv ≥ 1.0.0 |
| Primary DB | Supabase PostgreSQL |
| Problem DB | Neon PostgreSQL (CF + LC pools) |
| Hosting | Render (web service, port 10000) |
| Runtime | Python 3.14 |

---

## Project Structure

```
CP-Bot/
├── bot.py                  # Entry point, help commands, startup logic
├── config.py               # All env vars, constants, channel routing config
├── api_server.py           # aiohttp REST API server (40+ endpoints)
├── duel_bot_engine.py      # Bot opponent simulation logic
├── duel_ranks.py           # CF-style Elo tier definitions (Newbie → LGM)
├── keep_alive.py           # Legacy keepalive (superseded by api_server)
├── ping.py                 # Health ping utility
├── requirements.txt        # Python dependencies
├── render.yaml             # Render deployment manifest
│
├── cogs/                   # discord.py cogs (one feature = one file)
│   ├── admin.py            # !setweek, !setmonth, !setpoints, !updatestats, !addcontest
│   ├── checker.py          # !check, !checkall, auto-check tasks
│   ├── contests.py         # !contests, !contestcheck, reminder background task
│   ├── duels.py            # !duel, !blitz, profile, leaderboard, rank — full duel engine
│   ├── inactivity.py       # !inactivity, !inactivitycheck, !exempt* — scheduled reports
│   ├── leaderboard.py      # !leaderboard, !lbfull, !lbdaily, !lbweekly, !lbmonthly
│   ├── points.py           # !addpoints, !subpoints, !setmemberpoints, !pointlog
│   ├── problems.py         # !problems, !addproblem, !removeproblem, !setdifficulty, !rius
│   ├── registry.py         # !register, !unregister, !profile, !handles
│   ├── reset.py            # !resetdaily, !resetweek, !resetmonth, !resetalltime, ...
│   ├── submissions.py      # !submissions
│   ├── verification.py     # on_member_join, !reverify, !verifyall, !verificationstatus
│   └── website_sync.py     # !sync*, !syncstatus, hourly background mirror
│
├── database/
│   ├── connection.py       # asyncpg pool init (Supabase + CF Neon + LC Neon)
│   ├── queries.py          # All core bot queries (users, solves, problems, weeks, etc.)
│   ├── duel_queries.py     # Duel-specific queries (ratings, match state, pair history)
│   ├── schema.sql          # Full PostgreSQL schema (run once)
│   ├── migrate_v1_to_v2.sql
│   ├── migration_duels.sql
│   ├── migration_v2_2.sql
│   └── migration_website_sync.sql
│
└── platforms/
    ├── base.py             # Abstract base class for platform adapters
    ├── codeforces.py       # CF: submission check, bulk fetch, handle verify
    ├── leetcode.py         # LC: GraphQL submission check, handle verify
    ├── codechef.py         # CC: submission check via public API
    ├── atcoder.py          # AtCoder: submission check with session cookie auth
    ├── duel_cf_pool.py     # CF problem pool: pick problems by rating + pair history
    └── duel_lc_pool.py     # LC problem pool: pick Easy/Medium/Hard by pair history
```

---

## Database Schema

All tables live in one Supabase PostgreSQL database. The schema is in `database/schema.sql`.

### Core Tables

#### `users`
| Column | Type | Description |
|---|---|---|
| `discord_id` | TEXT PK | Discord user snowflake |
| `discord_username` | TEXT | Display name at registration time |
| `created_at` | TIMESTAMPTZ | Row creation time |

#### `handles`
| Column | Type | Description |
|---|---|---|
| `discord_id` | TEXT FK → users | Discord user |
| `platform` | TEXT | `cf`, `lc`, `cc`, `atcoder` |
| `handle` | TEXT | Platform username/slug |
| `verified` | BOOLEAN | Handle verification state |
| `linked_at` | TIMESTAMPTZ | When handle was linked |

PK: `(discord_id, platform)`

#### `weeks`
| Column | Type | Description |
|---|---|---|
| `id` | SERIAL PK | Auto-incrementing week ID |
| `guild_id` | TEXT | Discord server ID |
| `label` | TEXT | Display label e.g. `"Week 1"` |
| `start_date` | DATE | Inclusive start |
| `end_date` | DATE | Inclusive end |
| `is_active` | BOOLEAN | Only one active week per guild |

#### `months`
| Column | Type | Description |
|---|---|---|
| `id` | SERIAL PK | Auto-incrementing month ID |
| `guild_id` | TEXT | Discord server ID |
| `label` | TEXT | Display label e.g. `"June 2026"` |
| `start_date` | DATE | Inclusive start |
| `end_date` | DATE | Inclusive end |
| `is_active` | BOOLEAN | Only one active month per guild |

#### `problems`
| Column | Type | Description |
|---|---|---|
| `id` | SERIAL PK | Database problem ID |
| `guild_id` | TEXT | Discord server ID |
| `week_id` | INTEGER FK → weeks | Owning week |
| `month_id` | INTEGER FK → months | Owning month (nullable) |
| `platform` | TEXT | `cf`, `lc`, `cc`, `atcoder` |
| `problem_id` | TEXT | Platform-native ID (e.g. `1234A`, `two-sum`) |
| `title` | TEXT | Problem title (nullable, filled lazily) |
| `difficulty` | TEXT | `easy`, `medium`, `hard`, `expert`, `master` |
| `points` | INTEGER | Points awarded on solve |
| `set_by` | TEXT | Admin discord_id who added it |
| `assigned_date` | DATE | The specific day this problem is active |

Unique: `(guild_id, week_id, platform, problem_id)`

#### `solves`
| Column | Type | Description |
|---|---|---|
| `id` | SERIAL PK | |
| `discord_id` | TEXT FK → users | Solver |
| `problem_db_id` | INTEGER FK → problems | Solved problem |
| `guild_id` | TEXT | Discord server ID |
| `solved_at` | TIMESTAMPTZ | Verified solve timestamp |
| `points_awarded` | INTEGER | Points credited |

Unique: `(discord_id, problem_db_id)` — one solve per user per problem.

#### `difficulty_points`
| Column | Type | Description |
|---|---|---|
| `guild_id` | TEXT | Discord server ID |
| `difficulty` | TEXT | Difficulty label |
| `points` | INTEGER | Points value |

PK: `(guild_id, difficulty)`. Default values: `easy=5`, `medium=10`, `hard=20`, `expert=35`, `master=50`.

#### `point_adjustments`
| Column | Type | Description |
|---|---|---|
| `id` | SERIAL PK | |
| `guild_id` | TEXT | Discord server ID |
| `discord_id` | TEXT FK → users | Target member |
| `delta` | INTEGER | Positive = add, negative = subtract |
| `reason` | TEXT | Admin-supplied reason |
| `adjusted_by` | TEXT | Admin discord_id |
| `created_at` | TIMESTAMPTZ | |

#### `bot_config`
| Column | Type | Description |
|---|---|---|
| `key` | TEXT PK | Config key (e.g. `atcoder_session`) |
| `value` | TEXT | Stored value |
| `updated_by` | TEXT | Who last set it |
| `updated_at` | TIMESTAMPTZ | |

### Duel Tables (added via `migration_duels.sql`)

#### `duels`
Stores every match (active or finished).

| Key Column | Description |
|---|---|
| `id` | SERIAL PK |
| `guild_id` | Discord server ID |
| `mode` | `cp_duel`, `cp_blitz`, `dsa_duel`, `dsa_blitz`, `icpc_duel`, `icpc_blitz` |
| `player1_id` / `player2_id` | Discord IDs |
| `is_bot_match` | Whether opponent is the AI bot |
| `bot_rating` | Simulated bot rating for bot matches |
| `status` | `pending`, `active`, `finished` |
| `winner_id` | Discord ID of winner (NULL = draw) |
| `p1_games_won` / `p2_games_won` | Problem-level score |
| `total_games` | 2 or 3 |
| `duel_number` | Sequential match number per guild |
| `channel_id` | Private match channel Discord snowflake |
| `current_game` | Index of currently active problem |
| `started_at` / `ended_at` | Match timestamps |

#### `duel_problems`
One row per problem per match.

| Key Column | Description |
|---|---|
| `duel_id` | FK → duels |
| `game_number` | Problem position (1, 2, or 3) |
| `platform` | `cf` or `lc` |
| `problem_id` | Platform-native ID |
| `difficulty` | Rating number (CF) or Easy/Medium/Hard (LC) |
| `rating` | Numeric CF rating target |
| `url` | Direct problem URL |
| `deadline_at` | Per-problem expiry (blitz) or match expiry (duel) |
| `p1_solved_at` / `p2_solved_at` | Verified solve timestamps |
| `game_winner` | Discord ID of who won this problem |

#### `duel_ratings`
One row per `(discord_id, guild_id, mode)`.

| Key Column | Description |
|---|---|
| `discord_id` | Discord user |
| `guild_id` | Discord server |
| `mode` | Match mode (6 modes total) |
| `rating` | Current Elo/points rating (starts at 800) |
| `wins` / `losses` / `draws` | Career record |
| `streak` | Positive = win streak, negative = loss streak |
| `bot_matches` | Count of bot-opponent matches |

#### `pair_history`
Tracks which problems have been served to a pair of players to avoid repeats.

### Website Sync Tables (added via `migration_website_sync.sql`)

#### `discord_messages`
Full mirror of messages from 14 configured channels. Includes `content`, `embeds` (jsonb), `attachments` (jsonb), `thread_id`, `is_pinned`, `reply_to_id`.

#### `discord_threads`
Thread index: `thread_id`, `editorial_date`, `message_count`, `has_pdf`, `is_archived`.

#### `guild_snapshot`
Guild-level stats updated every 5 minutes: `member_count`, `online_count`, `boost_count`, `channel_count`, etc.

#### `community_threads`
User-generated community posts from the website forum: `title`, `author`, `content`, `tag`, `upvotes`, `comments_json`.

---

## REST API Reference

The API server starts on the port configured via `PORT` (default: `10000`) alongside the Discord bot. All `GET` endpoints are public. Endpoints under `/api/internal/` require the `X-BB-Key` header set to `BB_API_KEY`.

CORS is configured via `BB_ALLOWED_ORIGINS`. The `/api/bot/*` paths are legacy aliases for frontend compatibility.

### Health

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Health check — returns `{"status":"ok","database":"ok"}` |
| `GET` | `/health` | Same as `/` |

### Community Stats

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/stats` | Live counters: `discord_members`, `team_members`, `contests_held`, `linkedin_followers`, plus DB totals for `members`, `problems`, `solves`, `duels`, `verified_handles` |

**Query params:** `guild_id` (optional, overrides server-level default)

### Problems

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/problems` | Paginated problem catalog |
| `GET` | `/api/problems/{id}/solvers` | Who solved a problem |
| `GET` | `/api/problems/{key}/statement` | Full problem statement with examples |
| `POST` | `/api/problems/check` | Trigger solve check for a user |

#### `GET /api/problems`

| Query Param | Type | Description |
|---|---|---|
| `page` | int | Page number (default: 1) |
| `pageSize` / `limit` | int | Results per page (max 500, default 60) |
| `platform` | string | Filter: `codeforces`, `cf`, `leetcode`, `lc` |
| `search` | string | Search by title or problem ID |
| `difficulty` | string | Filter by difficulty level |

**Response:**
```json
{
  "problems": [
    {
      "id": 42,
      "platform": "cf",
      "problem_id": "1234A",
      "title": "Problem Title",
      "difficulty": "medium",
      "points": 10,
      "assigned_date": "2026-08-17",
      "week_label": "Week 3",
      "solve_count": 5,
      "key": "1234A",
      "contestId": 1234,
      "index": "A",
      "rating": 1200,
      "tags": ["Medium", "CF"],
      "judgeable": true
    }
  ],
  "total": 120,
  "page": 1,
  "pages": 2
}
```

#### `GET /api/problems/{key}/statement`

Fetches the full problem statement. Falls back through multiple sources:
1. CF/LC Neon DB cache
2. Live platform API / GraphQL
3. Codeforces HTML scraper
4. Hugging Face HARDTESTS dataset

| Query Param | Description |
|---|---|
| `platform` | Hint for platform detection (`codeforces`, `leetcode`) |

**Response:**
```json
{
  "problem": {
    "key": "1234A",
    "title": "Problem Title",
    "rating": 1200,
    "tags": ["Greedy", "Math"],
    "timeLimitMs": 2000,
    "memoryLimitMb": 256,
    "description": "...",
    "inputFormat": "...",
    "outputFormat": "...",
    "note": "...",
    "examples": [{"input": "3\n1 2 3", "output": "6"}],
    "platform": "codeforces",
    "starterCode": ""
  }
}
```

#### `POST /api/problems/check`

**Body:**
```json
{ "discord_id": "123456789", "guild_id": "..." }
```

**Response:**
```json
{ "success": true, "results": ["1. CF 1234A — Solved · +10 pts"], "earned": 10 }
```

### Leaderboards

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/leaderboard/points` | Points leaderboard (daily/week/month/all) |
| `GET` | `/api/leaderboard/rating` | Duel rating leaderboard per mode |
| `GET` | `/api/modes` | List all active duel modes |

#### `GET /api/leaderboard/points`

| Query Param | Values | Description |
|---|---|---|
| `scope` | `all`, `daily`, `week`, `month` | Leaderboard scope (default: `all`) |
| `limit` | int (max 500) | Max entries (default: 100) |
| `date` | `YYYY-MM-DD` | Only for `scope=daily` |
| `guild_id` | string | Discord server ID |

**Response:**
```json
{
  "scope": "week",
  "entries": [
    {
      "rank": 1,
      "discord_id": "123456789",
      "discord_username": "tourist",
      "points": 150,
      "solved": 12
    }
  ]
}
```

#### `GET /api/leaderboard/rating`

| Query Param | Values | Description |
|---|---|---|
| `mode` | `cp_duel`, `cp_blitz`, `dsa_duel`, `dsa_blitz`, `icpc_duel`, `icpc_blitz` | Mode (default: `cp_duel`) |
| `limit` | int (max 500) | Max entries (default: 100) |

**Response:**
```json
{
  "mode": "cp_duel",
  "entries": [
    {
      "rank": 1,
      "discord_id": "123456789",
      "discord_username": "tourist",
      "rating": 1850,
      "wins": 20,
      "losses": 3,
      "draws": 1,
      "streak": 5,
      "bot_matches": 2,
      "updated_at": "2026-08-17T10:00:00Z"
    }
  ]
}
```

### Users

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/users/{discord_id}` | Full user profile |

**Response:**
```json
{
  "user": { "discord_id": "...", "discord_username": "...", "created_at": "..." },
  "handles": [{ "platform": "cf", "handle": "tourist", "verified": true }],
  "ratings": [{ "mode": "cp_duel", "rating": 1200, "wins": 5, "losses": 2, "draws": 1 }],
  "points": 250,
  "solved": 18,
  "streak": 4,
  "recent_solves": [{ "platform": "cf", "problem_id": "1234A", "title": "...", "points_awarded": 10 }]
}
```

### Duels

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/duels` | Match history |
| `GET` | `/api/duels/live` | Currently active matches |
| `GET` | `/api/duels/state/{id}` | Full state of a single match |
| `POST` | `/api/duels/create` | Create a new match (web arena) |
| `POST` | `/api/duels/verify` | Submit a solve verification |
| `POST` | `/api/duels/forfeit` | Forfeit an active match |

#### `GET /api/duels`

| Query Param | Description |
|---|---|
| `discord_id` | Filter to matches involving this user |
| `mode` | Filter by mode (e.g. `cp_blitz`) |
| `limit` | Max results (max 100, default 25) |

#### `POST /api/duels/create`

**Body:**
```json
{
  "mode": "dsa_blitz",
  "player1_id": "123456789",
  "player2_id": "987654321",
  "is_bot_match": false,
  "total_games": 3
}
```

**Response:** Full duel object including `duel_id`, ratings, and problem list with URLs.

#### `POST /api/duels/verify`

**Body:**
```json
{ "duel_id": 42, "discord_id": "123456789" }
```

**Response:**
```json
{
  "verified": true,
  "game_number": 1,
  "solved_by": "123456789",
  "finished": false,
  "winner_id": null,
  "p1_games_won": 1,
  "p2_games_won": 0
}
```

#### `POST /api/duels/forfeit`

**Body:**
```json
{ "duel_id": 42, "discord_id": "123456789" }
```

**Response:** Rating changes for both players, winner ID.

### Contests

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/contests` | Upcoming contests from all 4 platforms (cached, 30-min refresh) |

**Response:**
```json
{
  "contests": [
    {
      "platform": "cf",
      "id": "2000",
      "name": "Codeforces Round 1000 (Div. 2)",
      "start_ts": 1756000000.0,
      "duration": 7200,
      "url": "https://codeforces.com/contest/2000",
      "start_iso": "2026-08-20T18:00:00+00:00"
    }
  ],
  "cached": true
}
```

### Community Forum

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/community/threads` | All community posts |
| `POST` | `/api/community/threads` | Create a new post |
| `POST` | `/api/community/threads/{id}/upvote` | Upvote a post |
| `POST` | `/api/community/threads/{id}/comments` | Add a comment |
| `DELETE` | `/api/community/threads/{id}` | Delete a post |
| `DELETE` | `/api/community/threads/{id}/comments/{cid}` | Delete a comment |

### Channel Mirror (Website Sync)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/channels` | Sync status for all mirrored channels |
| `GET` | `/api/channels/{key}/messages` | Paginated messages from a channel |
| `GET` | `/api/channels/{key}/threads` | Thread index for a channel |
| `GET` | `/api/threads/{thread_id}/messages` | All messages inside a thread |
| `GET` | `/api/editorials/{date}` | Editorial state for a given date (YYYY-MM-DD) |

#### `GET /api/channels/{key}/messages`

| Query Param | Description |
|---|---|
| `limit` | Max messages (max 200, default 50) |
| `before` | ISO timestamp cursor for pagination |
| `pinned` | `1` to return only pinned messages |
| `q` | Full-text search in content |
| `threads` | `1` to include thread messages |

**Valid channel keys:** `contest_reminder`, `server_updates`, `updates_official`, `competitions_info`, `ideas_feedback`, `self_promo`, `arena_guide`, `maths_lounge`, `cp_dsa_roadmap`, `daily_editorials`, `server_info`, `team_info`, `find_us_online`, `oa_questions`

#### `GET /api/editorials/{date}`

Returns one of three states:
- `{"status": "none"}` — no editorial thread exists
- `{"status": "coming_soon", "thread": {...}}` — thread exists but no PDF
- `{"status": "available", "thread": {...}, "files": [...]}` — PDF attached

### Discord OAuth

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/discord/login` | Redirect to Discord OAuth authorization |
| `GET` | `/api/discord/callback` | OAuth callback handler, sets `bb_user_session` cookie |
| `GET` | `/api/discord/me` | Current session info from cookie |
| `POST` | `/api/discord/logout` | Clear session cookie |

### External API Proxies

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/cf/user/{handles}` | Proxy: Codeforces user info (rating, rank) |
| `GET` | `/api/cf/status/{handle}` | Proxy: Codeforces recent submissions |
| `GET` | `/api/cf/user/{handle}/rating-history` | Proxy: Codeforces rating history |
| `GET` | `/api/leetcode/status` | LeetCode health check |
| `GET` | `/api/guild` | Live Discord guild snapshot (member count, boost tier, etc.) |
| `GET` | `/api/announcements` | Bot config entries with `announcement:` prefix |

### Internal (Key-Protected)

Require header `X-BB-Key: <BB_API_KEY>`.

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/internal/membership` | Check which Discord IDs the bot has seen in the guild |
| `GET` | `/api/internal/hardtests/{pid}` | Fetch hidden test cases from Hugging Face HARDTESTS dataset |

#### `POST /api/internal/membership`

**Body:** `{"discord_ids": ["123...", "456..."]}`  
**Response:** `{"known": [...], "unknown": [...]}`

---

## Bot Commands

The command prefix is `!` by default (configurable via `PREFIX` env var).

### Member Commands

Available to all server members via `!help`.

#### Registration — `cogs/registry.py`

| Command | Arguments | Description |
|---|---|---|
| `!register` | `<platform> <handle>` | Link a CP handle. Verifies the handle exists before storing. Platforms: `cf`, `lc`, `cc`, `atcoder` |
| `!unregister` | `<platform>` | Unlink a previously registered handle |
| `!profile` | `[@user]` | View linked handles, total points, solve count, today's points, manual adjustments |
| `!handles` | `[platform]` | List all registered handles (optional: filter by platform) |

#### Problems — `cogs/problems.py`

| Command | Arguments | Description |
|---|---|---|
| `!problems` | — | Show today's assigned problems split into DSA and CP sections, ordered easy → hard. Shows the active week label and day number |

#### Solve Checking — `cogs/checker.py`

| Command | Arguments | Description |
|---|---|---|
| `!check` | `[@user]` | Check today's solve status for yourself or another member. Rate-limited: 3 runs/day, 1-hour cooldown between runs |
| `!submissions` | `<platform> [count] [@user]` | Browse recent submissions with verdicts. Aliases: `!subs`, `!recent` |

#### Leaderboards — `cogs/leaderboard.py`

| Command | Arguments | Description |
|---|---|---|
| `!leaderboard` | — | Show Daily + Weekly + Monthly leaderboards in one view (top 3 each). Aliases: `!lb`, `!rank`, `!top`, `!standings` |
| `!points` | — | View the current difficulty → points mapping |
| `!currentweek` | — | Show the active week and month with date ranges. Alias: `!week` |

#### Duels — `cogs/duels.py`

| Command | Syntax | Description |
|---|---|---|
| `!duel` | `@user <cp\|dsa\|icpc> [2\|3]` | Challenge a player — always DUEL mode (ICPC-style, one total timer). Format defaults to 3-problem |
| `!blitz` | `@user <cp\|dsa\|icpc> [2\|3]` | Challenge a player — always BLITZ mode (speed race, per-problem timers). Format defaults to 3-problem |
| `!duel leaderboard` | `[cp\|dsa\|icpc]` | Top 10 players in DUEL mode (family optional, defaults to CP) |
| `!blitz leaderboard` | `[cp\|dsa\|icpc]` | Top 10 players in BLITZ mode |
| `!duel rank` | `[cp\|dsa\|icpc]` | Your current DUEL tier/rating (all families if none given) |
| `!blitz rank` | `[cp\|dsa\|icpc]` | Your current BLITZ tier/rating |
| `!duelprofile` | `[@user]` | Full duel ratings and W/L/D record across all DUEL modes |
| `!blitzprofile` | `[@user]` | Full blitz ratings and W/L/D record across all BLITZ modes |

**Notes:**
- If the challenged player does not respond within the configured timeout (default 15s), the challenger is **automatically matched against the bot**.
- Players cannot challenge themselves, bot accounts, or have more than one active match at a time.
- The family token (`cp`/`dsa`/`icpc`) and the format token (`2`/`3`) can appear in any order after the opponent mention.

#### Configuration (All Members)

| Command | Description |
|---|---|
| `!help` | Member command reference. Admins use `!adminhelp` for admin commands |

---

### Admin Commands

Require the `Administrator` Discord permission or the configured `ADMIN_ROLE` role. Available via `!adminhelp`.

#### Period Management — `cogs/admin.py`

| Command | Arguments | Description |
|---|---|---|
| `!setweek` | `"<label>" <YYYY-MM-DD> <YYYY-MM-DD>` | Create and activate a new week. Deactivates the previous week automatically |
| `!setmonth` | `"<label>" <YYYY-MM-DD> <YYYY-MM-DD>` | Create and activate a new month for the monthly leaderboard |
| `!currentweek` | — | Show the active week and month |
| `!setpoints` | `<difficulty> <points>` | Set points for a difficulty level. Custom difficulties allowed |
| `!updatestats` | `[team] [contests] [linkedin]` | Update live website stat counters (team members, contests held, LinkedIn followers) |
| `!addcontest` | `"<title>" <cf_url> [start_time]` | Register an upcoming contest on the website portal |

#### Problem Management — `cogs/problems.py`

| Command | Arguments | Description |
|---|---|---|
| `!addproblem` | `<platform> <id> <difficulty> <YYYY-MM-DD> [points]` | Add a problem to the active week for a specific date. Custom points override the difficulty default |
| `!removeproblem` | `<db_id> [keep_history=yes]` | Remove a problem. Solve history kept by default; pass `no` to also delete solve records |
| `!setdifficulty` | `<db_id> <difficulty>` | Change a problem's difficulty and recalculate its points |
| `!removeifunsolved` | `<db_id>` | Safe removal: deletes immediately if unsolved, asks for confirmation if already solved. Alias: `!rius` |

#### Solve Checking — `cogs/checker.py`

| Command | Description |
|---|---|
| `!checkall` | Bulk-check every registered member for today's problems. Members processed sequentially with delay to avoid rate limits |

#### Leaderboard Admin — `cogs/leaderboard.py`

| Command | Arguments | Description |
|---|---|---|
| `!lbfull` | `[daily\|weekly\|monthly]` | Full paginated leaderboard (all users, 10 per page) |
| `!lbdaily` | — | Shortcut for `!lbfull daily` |
| `!lbweekly` | — | Shortcut for `!lbfull weekly` |
| `!lbmonthly` | — | Shortcut for `!lbfull monthly` |

#### Manual Points — `cogs/points.py`

| Command | Arguments | Description |
|---|---|---|
| `!addpoints` | `@user <amount> [reason]` | Grant bonus points to a member |
| `!subpoints` | `@user <amount> [reason]` | Deduct points from a member |
| `!setmemberpoints` | `@user <target_total> [reason]` | Force-set a member's adjustment total to an exact value |
| `!pointlog` | `[@user]` | View recent manual point adjustments for a member (audit log) |

#### Inactivity — `cogs/inactivity.py`

| Command | Description |
|---|---|
| `!inactivity` | Run the full inactivity report now and post to `#inactivity-info` |
| `!inactivitycheck` | Alias for `!inactivity` — manually trigger immediately |
| `!exemptinactivity @user` | Exempt a member from inactivity warnings |
| `!unexemptinactivity @user` | Remove a member's inactivity exemption |

#### Verification — `cogs/verification.py`

| Command | Description |
|---|---|
| `!sendverification` | Post the verification embed with buttons in the current channel |
| `!reverify @user` | Reset a member back to pending verification state and DM instructions |
| `!verifyall` | DM all unverified members with verification instructions |
| `!verificationstatus` | Show counts of pending vs. verified members. Alias: `!vstatus` |

#### Contests — `cogs/contests.py`

| Command | Description |
|---|---|
| `!contests` | List upcoming contests (next 7 days) from all platforms |
| `!contestcheck` | Manually trigger the contest reminder check immediately |

#### Duels Admin — `cogs/duels.py`

| Command | Arguments | Description |
|---|---|---|
| `!duelsetrank` | `@user <mode> <rating>` | Set a user's duel rating for a specific mode. Mode format: `cp_blitz`, `cp_duel`, `dsa_blitz`, `dsa_duel`, `icpc_blitz`, `icpc_duel` |

#### Reset — `cogs/reset.py`

| Command | Arguments | Description |
|---|---|---|
| `!resetdaily` | — | Explains that the daily board resets automatically (non-destructive) |
| `!resetweek` | — | Delete solve records for the current week (requires `yes` confirmation) |
| `!resetmonth` | — | Delete monthly solve records, preserving the current week (requires `yes` confirmation) |
| `!resetalltime` | — | **Nuclear** — permanently wipes all solve records. Requires typing `CONFIRM WIPE <username>` |
| `!resetuser` | `@user [week\|all]` | Reset a single member's solves (current week or all-time) |
| `!resetproblem` | `<db_id>` | Un-mark all solves for a specific problem so members can re-earn points |
| `!resetweekfull` | — | Full reset: delete solves + problems + deactivate the week (requires `yes` confirmation) |
| `!saferemove` | `<db_id>` | Remove a problem safely: instant if unsolved, confirmation required if already solved |

#### Website Sync — `cogs/website_sync.py`

| Command | Arguments | Description |
|---|---|---|
| `!syncultimate` | — | Fetch and sync complete message history across all 14 configured channels |
| `!syncall` | — | Sync the last 10 messages across all configured channels |
| `!sync` | `<key>` | Sync the last 10 messages of a single channel by key name |
| `!syncchannel` | `<key\|all> [limit]` | Sync up to `limit` messages for a channel or all channels |
| `!syncstatus` | — | View row counts and last sync timestamp per channel |

#### Bot Configuration — `bot.py`

| Command | Arguments | Description |
|---|---|---|
| `!setcookie` | `<REVEL_SESSION value>` | Store the AtCoder session cookie for authenticated checks. The triggering message is auto-deleted immediately |
| `!adminhelp` | — | Show the full admin command reference (hidden from non-admins) |

---

## Background Tasks & Automation

| Task | Schedule | Description |
|---|---|---|
| **Nightly auto-check** | 23:58 IST daily | Bulk-checks all members for today's problems, 2 seconds between members. Posts summary to `CHECKALL_CHANNEL_ID` if configured |
| **6-hour auto-check** | Every 6 hours | Silent background solve award — awards points without posting any message |
| **Week/Month-end announcement** | On exact last day of active week/month | Posts congratulations leaderboard to `LEADERBOARD_ANNOUNCE_CHANNEL_ID` and pings `LEADERBOARD_PING_ROLE_ID` |
| **Inactivity report** | 09:00 IST on 15th/20th/25th/30th | Scans all members' last solve, categorizes 15–29/30–49/50+ days inactive, posts to `#inactivity-info` |
| **Contest reminders** | Every 30 minutes | Fetches contests from CF/LC/CC/AtCoder, posts 12h and 1h warnings to `CONTEST_REMINDER_CHANNEL` |
| **Duel auto-check** | Every 45 seconds | Scans all active duels for expired deadlines, auto-resolves blitz problems and finalizes duel matches |
| **Website channel sync** | Every 1 hour | Mirrors last 10 messages from all 14 configured channels to Postgres |
| **Guild snapshot** | Every 5 minutes | Updates `guild_snapshot` table with live member counts, boost tier, etc. |
| **Contest API cache refresh** | Every 30 minutes (background) | Refreshes the contest API cache asynchronously; API responds instantly from memory |

---

## Duel System Deep Dive

### Modes

| Family | Match Type | Platform | Rating System |
|---|---|---|---|
| `cp` | BLITZ | Codeforces | Elo (K=24) |
| `cp` | DUEL | Codeforces | Elo (K=32) |
| `dsa` | BLITZ | LeetCode | Fixed points (win +12, loss −6) |
| `dsa` | DUEL | LeetCode | Fixed points (win +22, loss −12) |
| `icpc` | BLITZ | Codeforces | Elo (K=40) |
| `icpc` | DUEL | Codeforces | Elo (K=40) |

### BLITZ Format

- Problems are played one at a time, shared by both players.
- Per-problem timers:
  - 3-problem: 15 min (P1) / 25 min (P2) / 35 min (P3)
  - 2-problem: 25 min (P1) / 25 min (P2)
- First verified solver takes the problem for both.
- Timer expires with no solve → draw on that problem → next problem.
- Most problems won takes the match.

### DUEL Format (ICPC-style)

- One total timer: 20 minutes × number of problems (2 → 40 min, 3 → 60 min).
- Players start on Problem 1 and unlock Problem N+1 only after their own verified solve of Problem N (delivered privately/ephemerally).
- If a player finishes all problems early, the match ends immediately.
- Scoring on time expiry: solve count wins; tie on count → lower total solve time wins; 0–0 or identical count + time → draw.

### Problem Selection

Target ratings are computed from the average of both players' ratings:

```
base = max(800, ceil(avg_rating / 100) * 100)

CF normal 3-problem:  [base−100, base+100, base+200]
CF normal 2-problem:  [base, base+100]
ICPC 3-problem:       [base, base+100, base+300]
ICPC 2-problem:       [base+100, base+200]
LC:                   [Easy, Medium, Hard]  (3-problem)
LC:                   [Medium, Medium]      (2-problem)
```

Pair history is tracked so the same problem is never served to the same pair twice.

### Rating Tiers

| Tier | Min Rating |
|---|---|
| Legendary Grandmaster | 3000 |
| International Grandmaster | 2650 |
| Grandmaster | 2450 |
| International Master | 2300 |
| Master | 2150 |
| Candidate Master | 1950 |
| Expert | 1600 |
| Specialist | 1400 |
| Pupil | 1200 |
| Newbie | 0 |

### Forfeit Penalties

- **Forfeiter:** −32 rating, counted as a loss
- **Winner (if human):** +16 rating, counted as a win
- **Bot matches:** Elo penalty still applies

### Match Channels

Each match creates a private text channel under a `Duels` category:
- Named `{family}-{kind}-{number}-{p1}-vs-{p2}`
- @everyone can see the channel exists but cannot read history or send messages
- Only the two players and the bot have full access
- Channel auto-deletes 10 seconds after the match finishes

---

## Environment Variables

Copy `.env.example` to `.env` and fill in the values.

| Variable | Required | Default | Description |
|---|---|---|---|
| `DISCORD_TOKEN` | ✅ | — | Discord bot token |
| `PREFIX` | — | `!` | Command prefix |
| `ADMIN_ROLE` | — | `Admin` | Role name that grants admin commands |
| `DATABASE_URL` | ✅ | — | Supabase PostgreSQL connection string |
| `DATABASE_URL_CF` | — | `DATABASE_URL` | Neon PostgreSQL pool for CF problem cache |
| `DATABASE_URL_LC` | — | — | Neon PostgreSQL pool for LC problem cache |
| `PORT` | — | `10000` | HTTP API server port |
| `RENDER_URL` | — | — | Public URL of the deployed service (for keepalive) |
| `GUILD_ID` | ✅ | — | Discord server ID (used by the REST API for guild-scoped queries) |
| `BB_API_KEY` | ✅ | — | Shared secret for `/api/internal/*` endpoints. Generate with `openssl rand -hex 32` |
| `BB_ALLOWED_ORIGINS` | — | `http://localhost:5173,http://localhost:4000` | Comma-separated CORS origins |
| `VERIFICATION_CHANNEL` | — | `verification` | Channel name for verification prompts |
| `WELCOME_CHANNEL` | — | `general` | Channel name for welcome messages |
| `LINKEDIN_URL` | — | — | LinkedIn page URL for verification |
| `VERIFICATION_ROLE` | — | `Verification` | Role assigned while pending verification |
| `MEMBER_ROLE` | — | `Member` | Role granted after verification |
| `INACTIVITY_CHANNEL` | — | `inactivity-info` | Channel name for inactivity reports |
| `INACTIVITY_CHANNEL_ID` | — | — | Channel ID (takes precedence over name) |
| `CHECKALL_CHANNEL_ID` | — | — | Channel ID for nightly auto-check summary |
| `LEADERBOARD_ANNOUNCE_CHANNEL_ID` | — | — | Channel ID for week/month-end leaderboard announcements |
| `LEADERBOARD_PING_ROLE_ID` | — | — | Role ID to ping in period announcements |
| `CONTEST_REMINDER_CHANNEL` | — | `contest-reminder` | Channel name for contest reminders |
| `CONTEST_REMINDER_ROLE` | — | `everyone` | Role to ping in reminders (`everyone`, a role name, or blank) |
| `DUEL_CP_DUEL_CHANNEL` | — | — | Channel ID or name for CP Duel match announcements |
| `DUEL_CP_BLITZ_CHANNEL` | — | — | Channel ID or name for CP Blitz match announcements |
| `DUEL_DSA_DUEL_CHANNEL` | — | — | Channel ID or name for DSA Duel match announcements |
| `DUEL_DSA_BLITZ_CHANNEL` | — | — | Channel ID or name for DSA Blitz match announcements |
| `DUEL_ICPC_DUEL_CHANNEL` | — | — | Channel ID or name for ICPC Duel match announcements |
| `DUEL_ICPC_BLITZ_CHANNEL` | — | — | Channel ID or name for ICPC Blitz match announcements |
| `BOT_STARTUP_DELAY` | — | `45` | Seconds to wait after starting the API before connecting to Discord |
| `BOT_RETRY_DELAY` | — | `60` | Base seconds to wait on rate-limit before restarting. Doubles on each retry, capped at 600 |
| `DISCORD_CLIENT_ID` | — | — | Discord OAuth2 application client ID |
| `DISCORD_CLIENT_SECRET` | — | — | Discord OAuth2 application client secret |
| `DISCORD_REDIRECT_URI` | — | — | OAuth2 redirect URI (must match Discord developer portal) |

---

## Deployment (Render)

The `render.yaml` manifest configures a single **web service** on Render.

```yaml
services:
  - type: web
    name: cp-discord-bot
    runtime: python
    pythonVersion: 3.14
    buildCommand: pip install -r requirements.txt
    startCommand: python -u bot.py
    healthCheckPath: /health
    healthCheckProtocol: http
```

**Steps:**

1. Push the repository to GitHub.
2. Create a new **Web Service** on [render.com](https://render.com) connected to the repo.
3. Render will use `render.yaml` automatically.
4. Add all required environment variables in the Render dashboard under **Environment**.
5. Set `sync: false` vars (secrets) manually — they are intentionally not committed.
6. Deploy. The health check at `/health` is used by Render to confirm the service is alive.

**Startup sequence:**
1. DB pool initialized.
2. API server starts immediately on `PORT` (satisfies Render's port-open check).
3. Bot waits `BOT_STARTUP_DELAY` seconds (default 45s) — prevents Discord 429 on cold start.
4. All 13 cogs loaded.
5. Discord gateway connection established.
6. On 429 or network error: exponential backoff with `os.execv` process restart (doubles delay, max 600s).

---

## Local Setup

> For a detailed step-by-step walkthrough covering Discord server configuration, role hierarchy, channel permissions, environment variables, and a first-run checklist, see the **[Complete Setup Guide →](SETUP_README.md)**.

### Prerequisites

- Python 3.11+ (3.14 recommended)
- A PostgreSQL database (Supabase free tier works)
- A Discord application with a bot token

### Installation

```bash
# 1. Clone the repository
git clone <repo-url>
cd CP-Bot

# 2. Create and activate a virtual environment
python -m venv venv
venv\Scripts\activate      # Windows
# source venv/bin/activate  # macOS/Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up environment variables
copy .env.example .env
# Edit .env with your values

# 5. Initialise the database
# Run database/schema.sql in your PostgreSQL client (Supabase SQL Editor, psql, etc.)
# Then run the migration files in order:
#   database/migrate_v1_to_v2.sql
#   database/migration_duels.sql
#   database/migration_v2_2.sql
#   database/migration_website_sync.sql

# 6. Start the bot
python bot.py
```

The bot will:
- Start the HTTP API on `http://localhost:10000`
- Wait `BOT_STARTUP_DELAY` seconds
- Connect to Discord
- Load all cogs
- Print `Logged in as <BotName> (ID: <ID>)`

### Invite the Bot

When creating the Discord application, enable the following **Intents** in the Developer Portal:
- `MESSAGE CONTENT INTENT`
- `SERVER MEMBERS INTENT`
- `PRESENCE INTENT` (optional, for online count in guild snapshot)

**Required OAuth2 scopes:** `bot`, `applications.commands`

**Minimum bot permissions:** `Send Messages`, `Embed Links`, `Manage Webhooks`, `Manage Channels`, `Manage Roles`, `Read Message History`, `Add Reactions`, `View Channel`

---

## Version History

### v13.0.1 (Current)
- Updated README with Render deployment startup sequence, environment guide, and aligned Git version history.

### v13.0.0
- Live Problem Statement API (`/api/problems/{key}/statement`) with direct Codeforces HTML scraper and LeetCode GraphQL `codeSnippets` integration.
- Clean prompt headers in statement output and format superscript tags (`10^9`, `2^31`).
- Scoped HuggingFace `sigcp/hardtests_problems` dataset fallback.
- Sanitized Neon database connections to load strictly via environment variables.

### v12.2.0
- OAuth Discord Gateway (`/api/discord/auth`, `/api/discord/callback`, `/api/user/session`).
- Dynamic OAuth `redirect_uri` host matching for live deployment on `binarybeats.in`.

### v12.1.0
- Real-Time Session Sync engine and automated leaderboard API endpoints.

### v12.0.0
- Bot HTTP API Gateway (`api_server.py`) and Neon PostgreSQL multi-pool database sync.

### v11.0.0
- Webhook creation, real-time duels, auto-matchmaking, and declining system.

### v10.0.0
- Discord Embeddings and UI Design System overhaul.

### v9.0.0
- Automated Monthly & Weekly Leaderboards auto-broadcast on the last day of each period.

### v8.0.0
- CP/DSA Duels & Blitz Arena System with Elo rating calculation, matchmaking pools, and private duel channels.

### v7.0.0
- Automated Contest Reminders and Neo TLE scheduler.

### v6.0.0
- User Verification, Inactivity Kick system, and AtCoder Engine integration.

### v5.0.0
- CodeChef Parser and Verification Suite.

### v4.0.0
- Multi-Platform Command Registry.

### v3.0.0
- Codeforces Engine and Problemset utilities.

### v2.0.0
- Database Schema V2 & Core Bot Integration.

### v1.0.0
- Initial CP Bot release: Codeforces + LeetCode solve tracking, daily/weekly leaderboards, and basic registration.
