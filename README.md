# CP-Bot — Binary Beats Competitive Programming Discord Bot

> **v14.0.2** · Python 3.14 · discord.py 2.3+ · PostgreSQL (Supabase + Neon)

A full-featured Discord bot powering the **Binary Beats** competitive programming community. Tracks daily problem solves across four platforms, runs 1v1 rated duel matches, maintains daily/weekly/monthly leaderboards, provides a personalised AI CP coaching system, mirrors server channels to a REST API for the website, and handles member onboarding — all within a single async Python application.

> **Setting up for the first time?** See the [Complete Setup Guide](SETUP_README.md) for Discord role hierarchy, channel permissions, environment variables, and a first-run checklist.

---

## Table of Contents

1. [Features Overview](#features-overview)
2. [Architecture](#architecture)
3. [Technology Stack](#technology-stack)
4. [Project Structure](#project-structure)
5. [Database Schema](#database-schema)
6. [REST API Reference](#rest-api-reference)
7. [Bot Commands](#bot-commands)
8. [Background Tasks](#background-tasks)
9. [Duel System](#duel-system)
10. [AI Coach System](#ai-coach-system)
11. [Environment Variables](#environment-variables)
12. [Deployment (Render)](#deployment-render)
13. [Local Setup](#local-setup)
14. [Version History](#version-history)

---

## Features Overview

| Feature | Description |
|---|---|
| Multi-platform solve tracking | Codeforces, LeetCode, CodeChef, AtCoder |
| Daily/Weekly/Monthly leaderboards | Scoped, auto-reset, persistent |
| 1v1 Duel System | CP, DSA, ICPC families in Blitz and Duel formats, real Elo |
| Bot opponent | Sigmoid-calibrated AI auto-matched when no human responds |
| AI CP Coach | Gemini-powered !coach, !hint, !explain, !review commands |
| Contest reminders | Fetches from CF/LC/CC/AtCoder, posts 12h and 1h warnings |
| Inactivity tracker | Monitors solve history, posts tiered reports on 15/20/25/30 of each month |
| Member onboarding | Auto-grants Member role on join, Zodiac webhook welcome, DM guide |
| LinkedIn verification | Persistent button flow for reverify/verifyall flows |
| Website REST API | 50+ endpoints served from the same process |
| Channel mirroring | 15 channels synced to Postgres, hourly background sync |
| Manual point management | Admin add/subtract/override with full audit log |
| Database maintenance | Configurable retention prune commands for daily problems and forum |
| Team roster management | !team command updates website team page live |
| Custom contests | Admin-added contests persisted to DB and served via API |

---

## Architecture

```
bot.py  (entry point)
  |
  +-- aiohttp API server  (port 10000, starts immediately)
  |
  +-- discord.py Bot  (prefix: !)
       |
       +-- cogs/registry.py        handle registration
       +-- cogs/admin.py           server configuration + maintenance
       +-- cogs/problems.py        problem management
       +-- cogs/checker.py         solve verification + nightly tasks
       +-- cogs/leaderboard.py     leaderboard views
       +-- cogs/submissions.py     recent submission lookup
       +-- cogs/reset.py           data management
       +-- cogs/points.py          manual point adjustments
       +-- cogs/verification.py    member onboarding (auto + manual)
       +-- cogs/inactivity.py      inactivity monitoring
       +-- cogs/contests.py        contest reminders
       +-- cogs/duels.py           1v1 match system
       +-- cogs/website_sync.py    channel -> Postgres mirror
       +-- cogs/ai_agent.py        Gemini AI CP Coach

  Databases
       +-- Supabase PostgreSQL     all live bot data
       +-- Neon PostgreSQL (CF)    10k+ CF problem statement cache
       +-- Neon PostgreSQL (LC)    LeetCode problem metadata
```

### Key Design Decisions

**Single-process, async-first.** The Discord bot and the HTTP API server run inside the same Python process as co-operating asyncio tasks. The API starts immediately on startup, letting Render's health check pass while the bot applies its startup delay.

**Bot is the source of truth.** The REST API is read-only over tables the bot already writes. The web arena duel endpoints (`/api/duels/create`, `/api/duels/verify`, `/api/duels/forfeit`) call the same duel queries the Discord cogs use.

**Three PostgreSQL pools.** One Supabase pool for all live data, one Neon pool for the CF problem statement cache (6-hour TTL, fetched once per window), one Neon pool for LC problem metadata.

**Webhook branding.** Public-facing messages go through named webhooks: `Z4s` (duels), `Contest Reminder` (contests), `Zodiac` (welcome), `Your Helper` (help commands).

**CF rate-limit safety.** One bulk `user.status` fetch per `!check` regardless of how many CF problems are assigned. If CF returns a 403/503 or Cloudflare page, `CFBlockedError` is raised and CF is skipped for that run entirely. No per-problem fallback calls.

**Exponential restart backoff.** On Discord 429/1015 or network errors, the process restarts via `os.execv` with a delay that doubles on each retry, capped at 600 seconds.

---

## Technology Stack

| Layer | Technology |
|---|---|
| Language | Python 3.14 |
| Discord | discord.py >= 2.3.2 |
| HTTP server | aiohttp >= 3.9.0 |
| Database driver | asyncpg >= 0.29.0 |
| Environment | python-dotenv >= 1.0.0 |
| AI / LLM | Google Gemini 2.5-flash (free tier, falls back to 1.5-flash) |
| Primary DB | Supabase PostgreSQL |
| Problem DB | Neon PostgreSQL (CF + LC pools) |
| Hosting | Render (web service, port 10000) |

---

## Project Structure

```
CP-Bot/
+-- bot.py                   Entry point, help commands, startup logic
+-- config.py                All env vars, constants, channel routing
+-- api_server.py            aiohttp REST API (50+ endpoints)
+-- duel_bot_engine.py       Bot opponent simulation (sigmoid timing model)
+-- duel_ranks.py            CF-style Elo tier definitions
+-- keep_alive.py            Legacy health server (superseded by api_server)
+-- ping.py                  Health ping utility
+-- requirements.txt
+-- render.yaml              Render deployment manifest
|
+-- cogs/
|    +-- admin.py            Period config, contests, team, maintenance
|    +-- ai_agent.py         Gemini AI CP Coach (!coach !hint !explain !review)
|    +-- checker.py          !check, !checkall, nightly tasks
|    +-- contests.py         Contest reminders, 12h/1h warnings
|    +-- duels.py            1v1 duel system, ratings, channels
|    +-- inactivity.py       Inactivity reports on 15/20/25/30
|    +-- leaderboard.py      Daily/Weekly/Monthly boards
|    +-- points.py           Manual point adjustments
|    +-- problems.py         Problem management
|    +-- registry.py         Handle registration, profile
|    +-- reset.py            Data reset commands
|    +-- submissions.py      Recent submissions view
|    +-- verification.py     Member onboarding, reverify flow
|    +-- website_sync.py     Channel mirror to Postgres
|
+-- database/
|    +-- connection.py       asyncpg pool init (3 pools)
|    +-- queries.py          Core bot queries
|    +-- duel_queries.py     Duel-specific queries
|    +-- schema.sql          Base PostgreSQL schema
|    +-- migrate_v1_to_v2.sql
|    +-- migration_duels.sql
|    +-- migration_v2_2.sql
|    +-- migration_website_sync.sql
|
+-- platforms/
     +-- base.py             Abstract adapter base class
     +-- codeforces.py       CF: bulk fetch, verify, CFBlockedError
     +-- leetcode.py         LC: GraphQL check, handle verify
     +-- codechef.py         CC: submission check
     +-- atcoder.py          AtCoder: session-cookie authenticated check
     +-- duel_cf_pool.py     CF problem pool: rating-targeted picker, 6h cache
     +-- duel_lc_pool.py     LC problem pool: Easy/Medium/Hard sequencer
```

---

## Database Schema

### Core Tables (schema.sql)

#### `users`
| Column | Type | Notes |
|---|---|---|
| `discord_id` | TEXT PK | Discord snowflake |
| `discord_username` | TEXT | Display name at registration |
| `created_at` | TIMESTAMPTZ | |

#### `handles`
| Column | Type | Notes |
|---|---|---|
| `discord_id` | TEXT FK | |
| `platform` | TEXT | `cf`, `lc`, `cc`, `atcoder` |
| `handle` | TEXT | Platform username |
| `verified` | BOOLEAN | |
| `linked_at` | TIMESTAMPTZ | |

PK: `(discord_id, platform)`

#### `weeks`
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `guild_id` | TEXT | |
| `label` | TEXT | e.g. `"Week 1"` |
| `start_date` | DATE | |
| `end_date` | DATE | |
| `is_active` | BOOLEAN | One active per guild |

#### `months`
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `guild_id` | TEXT | |
| `label` | TEXT | e.g. `"June 2026"` |
| `start_date` | DATE | |
| `end_date` | DATE | |
| `is_active` | BOOLEAN | One active per guild |

#### `problems`
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `guild_id` | TEXT | |
| `week_id` | INTEGER FK | |
| `month_id` | INTEGER FK | nullable |
| `platform` | TEXT | |
| `problem_id` | TEXT | Platform-native ID |
| `title` | TEXT | Filled lazily |
| `difficulty` | TEXT | `easy`/`medium`/`hard`/`expert`/`master` |
| `points` | INTEGER | |
| `set_by` | TEXT | Admin discord_id |
| `assigned_date` | DATE | Required, exact day this problem is active |

Unique: `(guild_id, week_id, platform, problem_id)`

#### `solves`
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `discord_id` | TEXT FK | |
| `problem_db_id` | INTEGER FK | Cascades on delete |
| `guild_id` | TEXT | |
| `solved_at` | TIMESTAMPTZ | |
| `points_awarded` | INTEGER | |

Unique: `(discord_id, problem_db_id)` — one solve per user per problem.

#### `difficulty_points`
PK: `(guild_id, difficulty)`. Defaults: easy=5, medium=10, hard=20, expert=35, master=50.

#### `point_adjustments`
Manual bonus/penalty log. `delta` is positive for adds, negative for subtracts.

#### `bot_config`
Generic key/value store. Used for AtCoder `REVEL_SESSION` cookie and `announcement:*` keys served by the API.

### Duel Tables (migration_duels.sql)

#### `duels`
One row per match. Key columns: `mode`, `player1_id`, `player2_id`, `is_bot_match`, `bot_rating`, `status` (`pending`/`active`/`finished`/`cancelled`), `winner_id`, `p1_games_won`, `p2_games_won`, `total_games`, `duel_number`, `channel_id`, `current_game`, `started_at`, `ended_at`.

#### `duel_problems`
One row per problem per match. Key columns: `duel_id`, `game_number`, `platform`, `problem_id`, `difficulty`, `rating`, `url`, `deadline_at`, `p1_solved_at`, `p2_solved_at`, `game_winner`.

#### `duel_ratings`
One row per `(discord_id, guild_id, mode)`. Starts at 800. Columns: `rating`, `wins`, `losses`, `draws`, `streak`, `bot_matches`.

#### `pair_history`
Tracks which problems have been served to a pair to avoid repeats.

### Website Sync Tables (migration_website_sync.sql)

#### `discord_messages`
Full mirror of messages from 15 configured channels. Includes `content`, `embeds` (jsonb), `attachments` (jsonb), `thread_id`, `is_pinned`, `reply_to_id`, `synced_at`.

#### `discord_threads`
Thread index: `thread_id`, `editorial_date`, `message_count`, `has_pdf`, `is_archived`.

#### `guild_snapshot`
Live guild stats updated every 5 minutes: `member_count`, `online_count`, `boost_count`, `channel_count`, `role_count`.

### Auto-created at Startup (connection.py)

#### `community_threads`
Website forum posts. Columns: `id`, `title`, `author`, `avatar`, `avatar_url`, `post_image_url`, `content`, `tag`, `upvotes`, `downvotes`, `comments_count`, `created_at`, `comments_json` (jsonb).

#### `community_comments`
Forum comments, FK to `community_threads` with CASCADE delete.

#### `custom_contests`
Admin-added contests via `!newcontest`. Columns: `id`, `guild_id`, `name`, `url`, `start_ts`, `duration`, `added_by`, `created_at`.

#### `team_members`
Website team page roster via `!team`. Columns: `id`, `guild_id`, `name`, `role`, `linkedin_url`, `github_url`, `added_by`, `created_at`.

---

## REST API Reference

The API server starts on `PORT` (default 10000) alongside the Discord bot. All `GET` routes are public unless noted. Routes under `/api/internal/` require the `X-BB-Key: <BB_API_KEY>` header.

CORS is controlled by `BB_ALLOWED_ORIGINS`. Methods supported: `GET`, `POST`, `OPTIONS`.

### Health

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Health check, returns `{"status":"ok","database":"ok"}` |
| `GET` | `/health` | Same as `/` |

### Community Stats

`GET /api/stats`

Returns live counters. Reads from DB for `members`, `problems`, `solves`, `duels`, `verified_handles`. Custom counters for `team_members`, `contests_held`, `linkedin_followers` are set via `!updatestats`.

```json
{
  "discord_members": 250,
  "team_members": 11,
  "contests_held": 2,
  "linkedin_followers": 450,
  "members": 80,
  "problems": 120,
  "solves": 540,
  "duels": 38,
  "verified_handles": 65
}
```

### Problems

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/problems` | Paginated problem catalog |
| `GET` | `/api/problems/{id}/solvers` | Who solved a problem |
| `GET` | `/api/problems/{key}/statement` | Full problem statement |
| `POST` | `/api/problems/check` | Trigger solve check for a user |

#### `GET /api/problems`

| Param | Type | Description |
|---|---|---|
| `page` | int | Page number (default 1) |
| `pageSize` / `limit` | int | Results per page (max 500, default 60) |
| `platform` | string | `codeforces`, `cf`, `leetcode`, `lc` |
| `search` | string | Search by title or problem ID |
| `difficulty` | string | Filter by difficulty |

#### `GET /api/problems/{key}/statement`

Falls back through: Neon DB cache -> live CF/LC API -> CF HTML scraper -> Hugging Face HARDTESTS dataset.

| Param | Description |
|---|---|
| `platform` | Hint: `codeforces` or `leetcode` |

Returns full statement with `description`, `inputFormat`, `outputFormat`, `note`, `examples`, `tags`, `timeLimitMs`, `memoryLimitMb`, `starterCode`.

#### `POST /api/problems/check`

Body: `{"discord_id": "123...", "guild_id": "..."}`

Calls the Checker cog's `_check_member` internally. Returns `{"success": true, "results": [...], "earned": 10}`.

### Leaderboards

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/leaderboard/points` | Points leaderboard |
| `GET` | `/api/leaderboard/rating` | Duel rating leaderboard |
| `GET` | `/api/modes` | Active duel modes from DB |

#### `GET /api/leaderboard/points`

| Param | Values | Description |
|---|---|---|
| `scope` | `all`, `daily`, `week`, `month` | Default: `all` |
| `limit` | int | Max 500, default 100 |
| `date` | `YYYY-MM-DD` | Only for `scope=daily` |

#### `GET /api/leaderboard/rating`

| Param | Values | Description |
|---|---|---|
| `mode` | `cp_duel`, `cp_blitz`, `dsa_duel`, `dsa_blitz`, `icpc_duel`, `icpc_blitz` | Default: `cp_duel` |
| `limit` | int | Max 500, default 100 |

Returns entries with `rank`, `discord_id`, `discord_username`, `rating`, `wins`, `losses`, `draws`, `streak`, `bot_matches`, `updated_at`.

### Users

`GET /api/users/{discord_id}`

Returns full user profile: `user`, `handles`, `ratings`, `points`, `solved`, `streak`, `recent_solves`.

### Team

`GET /api/team`

Returns team member roster added via `!team`. The website merges this with its own static fallback, with DB entries taking priority by name.

### Duels

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/duels` | Match history |
| `GET` | `/api/duels/live` | Currently active matches |
| `GET` | `/api/duels/state/{id}` | Full state of one match |
| `POST` | `/api/duels/create` | Create a match (web arena) |
| `POST` | `/api/duels/verify` | Submit a solve verification |
| `POST` | `/api/duels/forfeit` | Forfeit an active match |

#### `POST /api/duels/create`

```json
{
  "mode": "dsa_blitz",
  "player1_id": "123456789",
  "player2_id": "987654321",
  "is_bot_match": false,
  "total_games": 3
}
```

Returns full duel object with `duel_id`, `ratings`, and `problems` list with URLs. Also broadcasts to Discord and creates a private match channel.

#### `POST /api/duels/verify`

```json
{"duel_id": 42, "discord_id": "123456789"}
```

Returns `{"verified": bool, "game_number": int, "solved_by": "...", "finished": bool, "winner_id": "...", "p1_games_won": int, "p2_games_won": int}`.

#### `POST /api/duels/forfeit`

```json
{"duel_id": 42, "discord_id": "123456789"}
```

Applies real Elo delta (same K-factor formula as Discord bot). Returns rating changes for both players.

### Contests

`GET /api/contests`

Returns upcoming contests from all platforms, cached in memory and refreshed every 30 minutes in the background. Responds instantly from cache. Also includes admin-added custom contests.

```json
{
  "contests": [
    {
      "platform": "cf",
      "id": "2000",
      "name": "Codeforces Round (Div. 2)",
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
| `GET` | `/api/community/threads` | All forum posts (last 50) |
| `POST` | `/api/community/threads` | Create a post |
| `POST` | `/api/community/threads/{id}/upvote` | Upvote a post |
| `POST` | `/api/community/threads/{id}/comments` | Add a comment |
| `DELETE` | `/api/community/threads/{id}` | Delete a post |
| `DELETE` | `/api/community/threads/{id}/comments/{cid}` | Delete a comment |

### Channel Mirror

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/channels` | Sync status for all mirrored channels |
| `GET` | `/api/channels/{key}/messages` | Paginated messages from a channel |
| `GET` | `/api/channels/{key}/threads` | Thread index for a channel |
| `GET` | `/api/threads/{thread_id}/messages` | All messages inside a thread |
| `GET` | `/api/editorials/{date}` | Editorial state for a date (YYYY-MM-DD) |

#### `GET /api/channels/{key}/messages`

| Param | Description |
|---|---|
| `limit` | Max 200, default 50 |
| `before` | ISO timestamp cursor for pagination |
| `pinned` | `1` to return only pinned messages |
| `q` | Full-text search in content |
| `threads` | `1` to include thread messages |

Valid channel keys: `contest_reminder`, `server_updates`, `updates_official`, `competitions_info`, `ideas_feedback`, `self_promo`, `arena_guide`, `maths_lounge`, `cp_dsa_roadmap`, `daily_editorials`, `server_info`, `team_info`, `find_us_online`, `oa_questions`, `daily_problems`

#### `GET /api/editorials/{date}`

Returns one of three states: `{"status":"none"}`, `{"status":"coming_soon","thread":{...}}`, or `{"status":"available","thread":{...},"files":[...]}`.

### Guild

`GET /api/guild`

Returns live guild snapshot from the `guild_snapshot` table (member count, boost tier, etc.).

### Announcements

`GET /api/announcements`

Returns all `bot_config` rows with keys prefixed `announcement:`. Admins post from Discord, it lands on the website with no deploy.

### Discord OAuth

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/discord/login` | Redirect to Discord OAuth |
| `GET` | `/api/discord/callback` | OAuth callback, sets `bb_user_session` cookie |
| `GET` | `/api/discord/me` | Current session from cookie |
| `POST` | `/api/discord/logout` | Clear session cookie |

### External Proxies

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/cf/user/{handles}` | Proxy: CF user info (rating, rank) |
| `GET` | `/api/cf/status/{handle}` | Proxy: CF recent submissions |
| `GET` | `/api/cf/user/{handle}/rating-history` | Proxy: CF rating history |
| `GET` | `/api/leetcode/status` | LeetCode health check |

### Internal (Key-Protected)

Require header `X-BB-Key: <BB_API_KEY>`.

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/internal/membership` | Check which Discord IDs the bot has seen |
| `GET` | `/api/internal/hardtests/{pid}` | Fetch hidden test cases from HF HARDTESTS dataset |

---

## Bot Commands

Command prefix is `!`. Members use `!help`. Admins use `!adminhelp`.

### Member Commands

#### Registration (`cogs/registry.py`)

| Command | Arguments | Description |
|---|---|---|
| `!register` | `<platform> <handle>` | Link a CP handle. Verifies it exists first. Platforms: `cf`, `lc`, `cc`, `atcoder` |
| `!unregister` | `<platform>` | Unlink a handle |
| `!profile` | `[@user]` | View linked handles, total points, solve count, today's points, manual adjustments |
| `!handles` | `[platform]` | List all registered handles, optionally filtered by platform |

#### Problems (`cogs/problems.py`)

| Command | Description |
|---|---|
| `!problems` | Today's assigned problems split into DSA and CP sections, sorted easy to hard. Shows active week label and day number |

#### Solve Checking (`cogs/checker.py`)

| Command | Arguments | Description |
|---|---|---|
| `!check` | `[@user]` | Check today's solve status. Rate-limited: 3 runs/day, 1-hour cooldown |
| `!submissions` | `<platform> [count] [@user]` | Recent submissions with verdicts. Aliases: `!subs`, `!recent` |

#### Leaderboards (`cogs/leaderboard.py`)

| Command | Description |
|---|---|
| `!leaderboard` | Daily + Weekly + Monthly in one view, top 3 each. Aliases: `!lb`, `!rank`, `!top`, `!standings` |
| `!points` | Current difficulty -> points mapping |
| `!currentweek` | Active week and month with date ranges. Alias: `!week` |

#### Duels (`cogs/duels.py`)

| Command | Syntax | Description |
|---|---|---|
| `!duel` | `@user <cp\|dsa\|icpc> [2\|3]` | Challenge in DUEL mode. Defaults to 3-problem. If opponent doesn't respond, auto-matched vs bot |
| `!blitz` | `@user <cp\|dsa\|icpc> [2\|3]` | Challenge in BLITZ mode. Defaults to 3-problem |
| `!duelprofile` | `[@user]` | Ratings and W/L/D across all DUEL modes |
| `!blitzprofile` | `[@user]` | Ratings and W/L/D across all BLITZ modes |
| `!duel leaderboard` | `[cp\|dsa\|icpc]` | Top players in DUEL mode |
| `!blitz leaderboard` | `[cp\|dsa\|icpc]` | Top players in BLITZ mode |
| `!duel rank` | `[cp\|dsa\|icpc]` | Your DUEL tier/rating (all families if none given) |
| `!blitz rank` | `[cp\|dsa\|icpc]` | Your BLITZ tier/rating |

The family token (`cp`/`dsa`/`icpc`) and the keyword (`leaderboard`/`rank`) can appear in any order after the command, e.g. `!duel leaderboard cp` or `!duel cp leaderboard`.

#### AI CP Coach (`cogs/ai_agent.py`)

| Command | Arguments | Description |
|---|---|---|
| `!coach` | `[@user]` | Personalised training roadmap based on your handle stats and duel ratings |
| `!hint` | `<problem_id>` | Progressive hints without spoiling the solution. Works for CF and LC problems |
| `!explain` | `<topic/algorithm>` | CP technique explanation with C++17 template and common problem patterns |
| `!review` | `<code>` | Time/space complexity analysis, TLE/MLE risk detection, and bug analysis |

Requires `GEMINI_API_KEY` in environment. Uses `gemini-2.5-flash`, falls back to `gemini-1.5-flash`.

---

### Admin Commands

Require the `Administrator` Discord permission or the configured `ADMIN_ROLE` role. Run `!adminhelp` to see full syntax in Discord.

#### Period Management (`cogs/admin.py`)

| Command | Arguments | Description |
|---|---|---|
| `!setweek` | `"<label>" <YYYY-MM-DD> <YYYY-MM-DD>` | Create and activate a new week, auto-deactivates previous |
| `!setmonth` | `"<label>" <YYYY-MM-DD> <YYYY-MM-DD>` | Create and activate a new month |
| `!currentweek` | | Show active week and month. Alias: `!week` |
| `!setpoints` | `<difficulty> <points>` | Set points for a difficulty level. Custom difficulties allowed |
| `!updatestats` | `<team> <contests> <linkedin>` | Update website homepage live counters |

#### Contest and Team Management (`cogs/admin.py`)

| Command | Arguments | Description |
|---|---|---|
| `!newcontest` | `"<name>" <link> [date]` | Register a contest, persisted to DB and live on the website. Aliases: `!addcontest`, `!newContest`. Date defaults to 7 days from now |
| `!team` | `"<name>" "<role>" <linkedin> [github]` | Add or update a team member card on the website Team page. Re-running for the same name updates in place |

#### Problem Management (`cogs/problems.py`)

| Command | Arguments | Description |
|---|---|---|
| `!addproblem` | `<platform> <id> <difficulty> <YYYY-MM-DD> [points]` | Add a problem to the active week for a specific date |
| `!removeproblem` | `<db_id> [keep_history]` | Remove a problem. Solve history kept by default; pass `no` to also delete solves |
| `!setdifficulty` | `<db_id> <difficulty>` | Change difficulty and recalculate points |
| `!removeifunsolved` | `<db_id>` | Instant delete if unsolved; asks to confirm if solved. Alias: `!rius` |

#### Solve Checking (`cogs/checker.py`)

| Command | Description |
|---|---|
| `!checkall` | Bulk-check every registered member for today's problems. Requires Administrator permission |

#### Leaderboard Admin (`cogs/leaderboard.py`)

| Command | Arguments | Description |
|---|---|---|
| `!lbfull` | `[daily\|weekly\|monthly]` | Full paginated leaderboard, all users, 10 per page |
| `!lbdaily` | | Shortcut for `!lbfull daily` |
| `!lbweekly` | | Shortcut for `!lbfull weekly` |
| `!lbmonthly` | | Shortcut for `!lbfull monthly` |

#### Manual Points (`cogs/points.py`)

| Command | Arguments | Description |
|---|---|---|
| `!addpoints` | `@user <amount> [reason]` | Grant bonus points |
| `!subpoints` | `@user <amount> [reason]` | Deduct points |
| `!setmemberpoints` | `@user <target_total> [reason]` | Force-set adjustment total to exact value |
| `!pointlog` | `[@user]` | Recent manual adjustments audit log |

#### Inactivity (`cogs/inactivity.py`)

| Command | Description |
|---|---|
| `!inactivity` | Run inactivity report now and post to `#inactivity-info` |
| `!inactivitycheck` | Alias for `!inactivity` |
| `!exemptinactivity @user` | Exempt a member from inactivity reports |
| `!unexemptinactivity @user` | Remove exemption |

#### Verification (`cogs/verification.py`)

| Command | Description |
|---|---|
| `!sendverification` | Post the LinkedIn verification embed with buttons in the current channel |
| `!reverify @user` | Remove Member role, re-add Verification role, post prompt in `#verification` |
| `!verifyall` | DM all unverified members and post verification prompt |
| `!verificationstatus` | Show pending vs verified counts. Alias: `!vstatus` |

#### Contests (`cogs/contests.py`)

| Command | Description |
|---|---|
| `!contests` | List upcoming contests (next 7 days) from all platforms |
| `!contestcheck` | Manually trigger the contest reminder check now |

#### Duels Admin (`cogs/duels.py` + `cogs/admin.py`)

| Command | Arguments | Description |
|---|---|---|
| `!duelsetrank` | `@user <mode> <rating>` | Set a user's duel rating. Mode format: `cp_blitz`, `cp_duel`, `dsa_blitz`, `dsa_duel`, `icpc_blitz`, `icpc_duel` |
| `!endduel` | `[duel_id]` | Without ID: lists active duels. With ID: force-cancels the match, no rating change. Alias: `!forceendduel` |

#### Reset (`cogs/reset.py`)

| Command | Arguments | Description |
|---|---|---|
| `!resetdaily` | | Explains daily resets automatically (non-destructive) |
| `!resetweek` | | Delete solves for the current week (requires `yes` confirmation) |
| `!resetmonth` | | Delete monthly solves only, weekly/daily untouched (requires `yes` confirmation) |
| `!resetalltime` | | Nuclear: wipe all solves. Requires typing `CONFIRM WIPE <username>` |
| `!resetuser` | `@user [week\|all]` | Reset a single member's solves |
| `!resetproblem` | `<db_id>` | Un-mark all solves for one problem so members can re-earn points |
| `!resetweekfull` | | Delete solves + problems + deactivate the week (requires `yes` confirmation) |
| `!saferemove` | `<db_id>` | Instant remove if unsolved; shows solvers and asks `confirm` if already solved |

#### Website Sync (`cogs/website_sync.py`)

| Command | Arguments | Description |
|---|---|---|
| `!syncultimate` | | Sync complete message history for all 15 channels |
| `!syncall` | | Sync last 10 messages across all channels |
| `!sync` | `<key>` | Sync last 10 messages of one channel |
| `!syncchannel` | `<key\|all> [limit]` | Sync up to `limit` messages for a channel or all channels |
| `!syncprune` | | Prune `daily_problems` and `daily_editorials` messages older than 30 days |
| `!syncstatus` | | Row counts and last sync timestamp per channel |

#### Bot Configuration (`bot.py`)

| Command | Arguments | Description |
|---|---|---|
| `!setcookie` | `<REVEL_SESSION value>` | Store AtCoder session cookie. Message auto-deleted after storing |
| `!adminhelp` | | Full admin command reference (hidden from non-admins) |

#### Database Maintenance (`cogs/admin.py`)

| Command | Description |
|---|---|
| `!prunedaily` | Delete `daily_problems` and `daily_editorials` messages and threads older than 30 days. User solve history is 100% safe. Aliases: `!pruneold`, `!prune30d` |
| `!prune_community` | Delete community forum threads and comments older than 15 days. Aliases: `!prune_community_posts`, `!prune_forum` |

---

## Background Tasks

| Task | Schedule | Description |
|---|---|---|
| Nightly auto-check | 23:58 IST daily | Bulk-checks all members for today's problems, 2 seconds between members. Posts summary to `CHECKALL_CHANNEL_ID` if configured |
| 6-hour silent check | Every 6 hours | Awards points silently with no message posted |
| Week/month-end announcement | On the exact last day of active week/month | Posts congratulations leaderboard to `LEADERBOARD_ANNOUNCE_CHANNEL_ID`, pings `LEADERBOARD_PING_ROLE_ID` |
| Inactivity report | 09:00 IST on 15th, 20th, 25th, 30th of each month | Tiered report (15-29 / 30-49 / 50+ days) posted to `#inactivity-info`. No DMs, no kicks |
| Contest reminders | Every 30 minutes | Posts 12h and 1h warnings to `#contest-reminder`. Fetches from CF/LC/CC/AtCoder + custom_contests |
| Duel auto-check | Every 45 seconds | Scans active duels for expired deadlines, auto-resolves blitz problems and finalises duel matches |
| Channel mirror sync | Every hour | Fetches last 10 messages from all 15 configured channels |
| Guild snapshot | Every 5 minutes | Updates `guild_snapshot` with live member counts, boost tier, etc. |
| Contest cache refresh | Every 30 minutes (background) | Refreshes the `/api/contests` cache asynchronously |

---

## Duel System

### Modes

| Family | Format | Platform | Rating |
|---|---|---|---|
| `cp` | BLITZ | Codeforces | Elo, K=24 |
| `cp` | DUEL | Codeforces | Elo, K=32 |
| `dsa` | BLITZ | LeetCode | Fixed (+12 win / -6 loss) |
| `dsa` | DUEL | LeetCode | Fixed (+22 win / -12 loss) |
| `icpc` | BLITZ | Codeforces | Elo, K=40 |
| `icpc` | DUEL | Codeforces | Elo, K=40 |

### BLITZ Format

Problems played one at a time, shared by both players. Per-problem timers scale with difficulty position:

```
3-problem format:  15 min (P1 easy) / 25 min (P2 medium) / 35 min (P3 hard)
2-problem format:  25 min (P1) / 25 min (P2)
```

First verified solver takes the problem for both. Timer expires with no solve means a draw on that problem, then next problem. Most problems won takes the match.

### DUEL Format (ICPC-style)

One total timer: 20 minutes x number of problems. Players start on Problem 1 and unlock the next only after their own verified solve of the current one (delivered ephemerally). If a player finishes all problems early, the match ends immediately. On timer expiry: solve count wins; tie on count means lower total solve time wins; 0-0 or identical count and time means draw.

### Problem Targets

```
base = max(800, ceil(avg_player_rating / 100) * 100)

CF normal  3-problem:  [base-100, base+100, base+200]
CF normal  2-problem:  [base, base+100]
ICPC       3-problem:  [base, base+100, base+300]
ICPC       2-problem:  [base+100, base+200]
LC         3-problem:  [Easy, Medium, Hard]
LC         2-problem:  [Medium, Medium]
```

The CF problem pool is loaded once, cached for 6 hours. For each target rating, the picker tries exact match then widens by ±100, ±200, ±300, ±500, then falls back to closest-rated match in the pool. ICPC mode requires tags from both the math family and the algo family.

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

### Forfeit / Force-end

Forfeiting applies proper Elo delta using the same K-factor as the mode (not a flat penalty). `!endduel` force-cancels with no rating change to either player.

### Match Channels

Each Discord match creates a private text channel under a `Duels` category:
- Named `{family}-{kind}-{id}-{p1}-vs-{p2}`
- `@everyone` can see the channel exists but cannot read or send
- Only the two players and the bot have full access
- Channel auto-deletes 10 seconds after the match finishes

---

## AI Coach System

Powered by Google Gemini via the free-tier REST API. Uses `gemini-2.5-flash` with automatic fallback to `gemini-1.5-flash`. Requires `GEMINI_API_KEY` in environment.

| Command | What it does |
|---|---|
| `!coach [@user]` | Pulls the user's registered handles, solve history, and duel ratings from the DB. Builds a personalised training roadmap with weak area identification, recommended problem ratings, and a weekly grind plan |
| `!hint <problem_id>` | Retrieves problem metadata (tags, rating, examples), generates progressive hints at three levels without ever showing code. Works for CF problem IDs (e.g. `1234A`) and LC slugs |
| `!explain <topic>` | Produces a CP-focused explanation of a technique or algorithm with an annotated C++17 template and a list of representative problems |
| `!review <code>` | Analyses pasted code for time and space complexity, TLE/MLE risk given typical CP constraints, and logical bugs, without rewriting the solution |

---

## Environment Variables

Copy `.env.example` to `.env` and fill in your values. Never commit real values.

| Variable | Required | Default | Description |
|---|---|---|---|
| `DISCORD_TOKEN` | yes | | Discord bot token |
| `PREFIX` | | `!` | Command prefix |
| `ADMIN_ROLE` | | `Admin` | Role name that grants admin commands |
| `DATABASE_URL` | yes | | Supabase PostgreSQL connection string (pooler URL works) |
| `DATABASE_URL_CF` | | `DATABASE_URL` | Neon pool for CF problem cache. Falls back to main DB if not set |
| `DATABASE_URL_LC` | | | Neon pool for LC problem metadata |
| `GEMINI_API_KEY` | | | Google Gemini API key for `!coach`, `!hint`, `!explain`, `!review` |
| `PORT` | | `10000` | HTTP API server port |
| `RENDER_URL` | | | Public URL for keepalive ping |
| `GUILD_ID` | yes | | Discord server ID (used by the REST API for guild-scoped queries) |
| `BB_API_KEY` | yes | | Shared secret for `/api/internal/*`. Generate: `openssl rand -hex 32` |
| `BB_ALLOWED_ORIGINS` | | `http://localhost:5173,...` | Comma-separated CORS origins |
| `VERIFICATION_CHANNEL` | | `verification` | Channel name for verification prompts |
| `WELCOME_CHANNEL` | | `general` | Channel name for welcome messages |
| `LINKEDIN_URL` | | | LinkedIn page URL used in the verification embed |
| `VERIFICATION_ROLE` | | `Verification` | Role for pending verification state |
| `MEMBER_ROLE` | | `Member` | Role granted after joining (auto-granted instantly on join) |
| `INACTIVITY_CHANNEL` | | `inactivity-info` | Channel name for inactivity reports |
| `INACTIVITY_CHANNEL_ID` | | | Channel ID (takes precedence over name) |
| `CHECKALL_CHANNEL_ID` | | | Channel ID for nightly auto-check summary |
| `LEADERBOARD_ANNOUNCE_CHANNEL_ID` | | | Channel ID for week/month-end announcements |
| `LEADERBOARD_PING_ROLE_ID` | | | Role ID to ping in period announcements |
| `CONTEST_REMINDER_CHANNEL` | | `contest-reminder` | Channel name for contest reminders |
| `CONTEST_REMINDER_ROLE` | | `everyone` | Role to ping: `everyone`, a role name, or blank for no ping |
| `DUEL_CP_DUEL_CHANNEL` | | | Channel ID/name for CP Duel match announcements |
| `DUEL_CP_BLITZ_CHANNEL` | | | Channel ID/name for CP Blitz |
| `DUEL_DSA_DUEL_CHANNEL` | | | Channel ID/name for DSA Duel |
| `DUEL_DSA_BLITZ_CHANNEL` | | | Channel ID/name for DSA Blitz |
| `DUEL_ICPC_DUEL_CHANNEL` | | | Channel ID/name for ICPC Duel |
| `DUEL_ICPC_BLITZ_CHANNEL` | | | Channel ID/name for ICPC Blitz |
| `BOT_STARTUP_DELAY` | | `45` | Seconds to wait after starting the API before connecting to Discord |
| `BOT_RETRY_DELAY` | | `60` | Base seconds before restart on rate-limit. Doubles on each retry, capped at 600 |
| `DISCORD_CLIENT_ID` | | | Discord OAuth2 application client ID |
| `DISCORD_CLIENT_SECRET` | | | Discord OAuth2 application client secret |
| `DISCORD_REDIRECT_URI` | | | OAuth2 redirect URI (must match Developer Portal) |

---

## Deployment (Render)

The `render.yaml` configures a single web service on Render.

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
2. Create a new Web Service on [render.com](https://render.com) connected to the repo.
3. Render picks up `render.yaml` automatically.
4. Set all required environment variables in the Render dashboard under Environment. Variables marked `sync: false` in `render.yaml` must be set manually.
5. Deploy. Render uses `/health` to confirm the service is alive.

**Startup sequence:**

1. DB pool initialised (Supabase + CF Neon + LC Neon).
2. API server starts immediately on `PORT`. Render's port-open check passes within seconds.
3. Bot waits `BOT_STARTUP_DELAY` (default 45s) to avoid Discord 429 on cold start.
4. All 14 cogs loaded.
5. Discord gateway connected.
6. On 429 or network error: exponential backoff via `os.execv` process restart. Delay doubles each time, capped at 600s.

---

## Local Setup

> For a detailed walkthrough covering Discord role hierarchy, channel permissions, and a first-run checklist, see the [Complete Setup Guide](SETUP_README.md).

### Prerequisites

- Python 3.11+ (3.14 recommended)
- A PostgreSQL database (Supabase free tier works)
- A Discord application with a bot token
- (Optional) A Google Gemini API key for the AI Coach commands

### Installation

```bash
# 1. Clone the repository
git clone <repo-url>
cd CP-Bot

# 2. Create and activate a virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up environment variables
copy .env.example .env
# Edit .env with your values

# 5. Initialise the database
# Run database/schema.sql in your PostgreSQL client (Supabase SQL Editor, psql, etc.)
# Then run migrations in order:
#   database/migrate_v1_to_v2.sql
#   database/migration_duels.sql
#   database/migration_v2_2.sql
#   database/migration_website_sync.sql
# community_threads, community_comments, custom_contests, and team_members
# are created automatically at startup.

# 6. Start the bot
python bot.py
```

The bot prints `Logged in as <Name> (ID: <ID>)` when ready.

### Required Discord Intents

Enable in the Discord Developer Portal under Bot > Privileged Gateway Intents:

- MESSAGE CONTENT INTENT
- SERVER MEMBERS INTENT

### Minimum Bot Permissions

`Send Messages`, `Embed Links`, `Manage Webhooks`, `Manage Channels`, `Manage Roles`, `Read Message History`, `Add Reactions`, `View Channel`

---

## Version History

### v14.0.2 — Current (25 Aug 2026)

- 15-day community forum retention: `community_threads` and `community_comments` tables with cascading deletes.
- `!prune_community` command (aliases `!prune_community_posts`, `!prune_forum`) deletes threads and comments older than 15 days.
- DB deletion sync in `on_raw_message_delete` now also removes matching rows from `discord_threads`.
- `!adminhelp` updated to show all current maintenance commands.

### v14.0.1 (25 Aug 2026)

- Fix editorial PDF sync filter in `cogs/website_sync.py`. PDFs were being missed on some thread syncs.

### v14.0.0 (25 Aug 2026)

- 15th channel `daily_problems` added to `SYNCED_CHANNELS`.
- Thread sync for `daily_problems`: skips first prompt message, enforces 10 KB attachment limit on member uploads.
- `!prunedaily` command (aliases `!pruneold`, `!prune30d`): removes `daily_problems` and `daily_editorials` messages and threads older than 30 days. User solve history 100% safe.
- `!syncprune` added to `cogs/website_sync.py`.
- `!adminhelp` split into two batches covering all 6 admin embed sections.

### v13.0.4 (24 Aug 2026)

- New cog `cogs/ai_agent.py` with `!coach`, `!hint`, `!explain`, `!review` powered by Google Gemini 2.5-flash.
- `GEMINI_API_KEY` environment variable.
- `!team` admin command persists team member cards to DB, served at `GET /api/team`.
- `GET /api/announcements` reads `bot_config` rows with `announcement:` prefix.
- Bot stats logging on startup.
- Config aliases in `database/queries.py`.

### v13.0.3 (23 Aug 2026)

- Resolve player display names in `GET /api/duels/live` and `GET /api/duels` match history. Previously returned raw discord IDs.

### v13.0.2 (23 Aug 2026)

- Arena forfeit on browser unload: `POST /api/duels/forfeit` uses real Elo delta (same K-factor as Discord bot), not flat penalty.
- Inline PDF viewer endpoint for daily editorials.
- `!team` and `!newcontest` admin commands.
- Contest fetch: custom contests from `custom_contests` table included in `/api/contests`.

### v13.0.1 (17 Aug 2026)

- README updated with Render deployment guide and version history.

### v13.0.0 (17 Aug 2026)

- `GET /api/problems/{key}/statement` live problem statement API.
- Multi-source fallback chain: Neon DB cache -> live CF/LC API -> CF HTML scraper -> Hugging Face HARDTESTS dataset.
- `GET /api/internal/hardtests/{pid}` internal endpoint.
- Neon PostgreSQL pools for CF and LC problem metadata.
- `DATABASE_URL_CF` and `DATABASE_URL_LC` env vars.

### v12.2.0 (17 Aug 2026)

- Discord OAuth2 gateway: `/api/discord/login`, `/api/discord/callback`, `/api/discord/me`, `/api/discord/logout`.
- `bb_user_session` cookie-based session.
- `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `DISCORD_REDIRECT_URI` env vars.
- Channel mirror sync improvements.

### v12.1.0 (17 Aug 2026)

- Real-time session sync.
- Leaderboard engine improvements: `GET /api/leaderboard/points` and `GET /api/leaderboard/rating` serving the website.
- `GET /api/modes` lists active duel modes from DB.

### v12.0.0 (17 Aug 2026)

- Bot REST API gateway (`api_server.py`) fully replaces `keep_alive.py`.
- Neon PostgreSQL sync for problem cache.
- `GET /api/users/{discord_id}` full profile endpoint.
- `GET /api/guild` guild snapshot endpoint.
- `GUILD_ID`, `BB_API_KEY`, `BB_ALLOWED_ORIGINS` env vars.
- `POST /api/internal/membership` internal endpoint.

### v11.0.0 (17 Aug 2026)

- Webhooks and branded embeds throughout: `Z4s`, `Contest Reminder`, `Zodiac`, `Your Helper`.
- Real-time duel broadcasting: private match channels created per match, animated ASCII countdown.
- `POST /api/duels/create`, `POST /api/duels/verify` web arena endpoints.
- `GET /api/duels/live` live match strip.

### v10.0.0 (17 Aug 2026)

- Full embed and UI design system overhaul across all cogs.
- Branded `_brand()` helper used consistently.
- `!help` / `!adminhelp` redesigned with ANSI art and structured sections.

### v9.0.0 (17 Aug 2026)

- Automated week-end and month-end leaderboard announcements.
- `LEADERBOARD_ANNOUNCE_CHANNEL_ID` and `LEADERBOARD_PING_ROLE_ID` env vars.
- `monthly_solves` table; `!resetmonth` scope-isolated from weekly data.

### v8.0.0 (17 Aug 2026)

- `cogs/duels.py`: 1v1 duel and blitz system with CP, DSA, ICPC families.
- Bot opponent (Z4s) with sigmoid-calibrated timing model in `duel_bot_engine.py`.
- Elo ratings and CF-style tiers in `duel_ranks.py`.
- Pair history tracking to avoid repeated problems.
- `duel_ratings`, `duels`, `duel_problems`, `pair_history` tables.
- `DUEL_*_CHANNEL` env vars for mode-specific channel routing.

### v7.0.0 (17 Aug 2026)

- `cogs/contests.py`: contest reminders at 12h and 1h for CF, LC, CC, AtCoder.
- `CONTEST_REMINDER_CHANNEL` and `CONTEST_REMINDER_ROLE` env vars.
- `!contests` and `!contestcheck` commands.
- `!adminhelp` is now separate from `!help`.
- `!setcookie` for AtCoder REVEL_SESSION, message auto-deleted.

### v6.0.0 (17 Aug 2026)

- `cogs/verification.py`: Member role auto-granted on join. Zodiac webhook welcome in `#welcome`. DM onboarding guide. LinkedIn button flow for `!reverify`.
- `cogs/inactivity.py`: tiered reports (15-29 / 30-49 / 50+ days) on 15/20/25/30 of each month. Posts to `#inactivity-info`. No auto-kick.
- `VERIFICATION_CHANNEL`, `WELCOME_CHANNEL`, `LINKEDIN_URL`, `VERIFICATION_ROLE`, `MEMBER_ROLE`, `INACTIVITY_CHANNEL` env vars.

### v5.0.0 (17 Aug 2026)

- CodeChef platform adapter (`cogs/codechef.py`).
- Multi-platform verification suite.

### v4.0.0 (17 Aug 2026)

- Multi-platform command registry. `!register`, `!unregister`, `!profile`, `!handles`.
- AtCoder adapter with session-cookie auth.

### v3.0.0 (17 Aug 2026)

- Codeforces engine with bulk submission fetch and `CFBlockedError`.
- CF problem pool for duels with 6-hour cache.

### v2.0.0 (17 Aug 2026)

- Database integration: asyncpg pools, Supabase.
- `months` table and monthly leaderboard scope.
- `point_adjustments` table and `!addpoints`, `!subpoints`, `!setmemberpoints`, `!pointlog`.
- `DATABASE_URL` env var, PgBouncer-safe `statement_cache_size=0`.

### v1.0.0 (24 Jun 2026)

- Initial release: Codeforces + LeetCode solve tracking, daily/weekly leaderboards, `!check`, `!checkall`, basic registration.
