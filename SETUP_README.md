# CP-Bot v7 — Complete Setup Guide
### Binary Beats Discord Server

> This guide covers everything needed to run CP-Bot from scratch: Discord server configuration, role hierarchy, channel permissions, environment variables, database initialisation, and a first-run checklist.

---

## Contents

1. [What's in the bot](#whats-in-the-bot)
2. [Part 1 — Discord Server Setup](#part-1--discord-server-setup)
3. [Part 2 — Environment Variables](#part-2--environment-variables)
4. [Part 3 — Database Setup](#part-3--database-setup)
5. [Part 4 — Deploy on Render](#part-4--deploy-on-render)
6. [Part 5 — First-Run Commands](#part-5--first-run-commands)
7. [Part 6 — Test Everything](#part-6--test-everything)
8. [Admin Command Quick Reference](#admin-command-quick-reference)
9. [Troubleshooting](#troubleshooting)

---

## What's in the bot

| Feature | Cog |
|---|---|
| Handle registration, profile | `cogs/registry.py` |
| Daily problem management | `cogs/problems.py` |
| Solve checking (manual + auto) | `cogs/checker.py` |
| Leaderboards (daily/weekly/monthly) | `cogs/leaderboard.py` |
| Recent submissions | `cogs/submissions.py` |
| Manual point adjustments | `cogs/points.py` |
| Data reset commands | `cogs/reset.py` |
| Member onboarding + LinkedIn flow | `cogs/verification.py` |
| Inactivity reports (15/20/25/30) | `cogs/inactivity.py` |
| Contest reminders (12h and 1h) | `cogs/contests.py` |
| 1v1 duels (CP, DSA, ICPC) | `cogs/duels.py` |
| Website channel mirror | `cogs/website_sync.py` |
| AI CP Coach (Gemini) | `cogs/ai_agent.py` |
| Server config, team, maintenance | `cogs/admin.py` |
| REST API for website | `api_server.py` |

---

## Part 1 — Discord Server Setup

All of this is done inside Discord before touching any code.

---

### Step 1.1 — Create the Required Roles

Go to **Server Settings -> Roles** and create these two roles if they don't already exist.

**Role: `Verification`**

This is assigned to a member when they first join, before they complete the LinkedIn flow. Members with only this role cannot see any regular channels.

- Name: `Verification` (capital V, rest lowercase)
- Color: red or orange so admins can spot pending members easily
- Permissions: none at all

**Role: `Member`**

This is the access role. A new member gets it instantly on join (the bot auto-grants it). The LinkedIn button flow exists for the manual `!reverify` case.

- Name: `Member` (capital M)
- Color: blue or green
- Permissions: standard read/send access for regular channels

**Bot role hierarchy (critical):**

The bot's own role must be above both `Member` and `Verification` in the role list, otherwise it cannot assign or remove those roles.

```
Server Settings -> Roles

Required order (top to bottom):
  Admin
  CP-Bot         <- drag to here
  Member
  Verification
  @everyone
```

Drag the CP-Bot role until it sits above Member and Verification, then save changes.

---

### Step 1.2 — Channels to Create

These channels are expected by the bot. Create them if they don't exist.

| Channel name | Purpose |
|---|---|
| `#verification` | Where the LinkedIn button embed is posted. New members who need manual reverify land here |
| `#welcome` | Where the Zodiac webhook posts the welcome embed when a member joins |
| `#inactivity-info` | Where inactivity reports are posted on 15/20/25/30 of each month |
| `#contest-reminder` | Where contest reminder embeds are posted 12h and 1h before each contest |

You can name them differently, but then you must set the corresponding env variables (`WELCOME_CHANNEL`, `INACTIVITY_CHANNEL`, etc.) to match.

---

### Step 1.3 — Channel Permissions

**`#verification`**

```
@everyone
  View Channel:    ON   (so new members see it when they join)
  Send Messages:   OFF

Verification role
  View Channel:    ON
  Send Messages:   OFF

Member role
  View Channel:    OFF  (verified members don't need to see this)

CP-Bot role
  View Channel:    ON
  Send Messages:   ON
  Embed Links:     ON
  Manage Messages: ON
```

**All other regular channels (apply at category level for convenience)**

```
Verification role
  View Channel:    OFF  <- pending members see nothing

Member role
  View Channel:    ON
  Send Messages:   ON
```

**`#welcome`**

```
@everyone
  View Channel:    ON
  Send Messages:   OFF

Verification role
  View Channel:    ON   (new members should see welcome messages)

Member role
  View Channel:    ON
  Send Messages:   ON

CP-Bot role
  View Channel:    ON
  Send Messages:   ON
  Embed Links:     ON
  Manage Webhooks: ON   (needed for the Zodiac welcome webhook)
```

**`#contest-reminder` and `#inactivity-info`**

Bot needs Send Messages, Embed Links, and Manage Webhooks.

---

### Step 1.4 — Bot Permissions in Discord Developer Portal

Go to [discord.com/developers/applications](https://discord.com/developers/applications), select your application, go to the **Bot** tab.

Under **Privileged Gateway Intents**, enable:

```
SERVER MEMBERS INTENT    <- required for on_member_join
MESSAGE CONTENT INTENT   <- required for prefix commands
```

**Bot Permissions (OAuth2 invite or Server Settings -> Integrations):**

```
View Channels
Send Messages
Read Message History
Embed Links
Use External Emojis
Attach Files
Add Reactions
Manage Roles          <- to grant/remove Member/Verification
Manage Channels       <- to create/delete private duel channels
Manage Webhooks       <- for Z4s, Contest Reminder, Zodiac, Your Helper webhooks
```

---

### Step 1.5 — Duel Channel Setup (Optional but Recommended)

The duel system creates a private text channel per match under a `Duels` category. You can optionally create dedicated announcement channels per mode so players know when a match starts.

Recommended structure:

```
Category: Duels
  #cp-duel-matches
  #cp-blitz-matches
  #dsa-duel-matches
  #dsa-blitz-matches
  #icpc-duel-matches
  #icpc-blitz-matches
```

Then set the `DUEL_*_CHANNEL` env variables to those channel IDs. This is optional — if not set, the bot picks the first matching channel or the command's origin channel.

---

## Part 2 — Environment Variables

Create a `.env` file by copying `.env.example`:

```bash
copy .env.example .env
```

Then fill in the values below. On Render, add these in the dashboard under **Environment**.

### Required

```env
DISCORD_TOKEN=your_bot_token
DATABASE_URL=postgresql://user:pass@host:port/dbname
GUILD_ID=your_discord_server_id
BB_API_KEY=generate_with_openssl_rand_hex_32
```

### Discord OAuth (for website login)

```env
DISCORD_CLIENT_ID=your_app_client_id
DISCORD_CLIENT_SECRET=your_app_client_secret
DISCORD_REDIRECT_URI=https://yoursite.com/api/discord/callback
```

### Database (optional, fall back to DATABASE_URL)

```env
DATABASE_URL_CF=neon_postgresql_url_for_cf_problem_cache
DATABASE_URL_LC=neon_postgresql_url_for_lc_problem_metadata
```

### AI Coach

```env
GEMINI_API_KEY=your_google_gemini_api_key
```

Get one free at [aistudio.google.com](https://aistudio.google.com). Without this key, `!coach`, `!hint`, `!explain`, and `!review` will tell users the feature is not configured.

### Bot Behaviour

```env
PREFIX=!
ADMIN_ROLE=Admin
BOT_STARTUP_DELAY=45
BOT_RETRY_DELAY=60
PORT=10000
RENDER_URL=https://your-bot.onrender.com
```

### Channels and Roles

```env
VERIFICATION_CHANNEL=verification
WELCOME_CHANNEL=welcome
LINKEDIN_URL=https://www.linkedin.com/company/binarybeatshq
VERIFICATION_ROLE=Verification
MEMBER_ROLE=Member

INACTIVITY_CHANNEL=inactivity-info
INACTIVITY_CHANNEL_ID=your_channel_id_here

CONTEST_REMINDER_CHANNEL=contest-reminder
CONTEST_REMINDER_ROLE=everyone

CHECKALL_CHANNEL_ID=your_channel_id_here
LEADERBOARD_ANNOUNCE_CHANNEL_ID=your_channel_id_here
LEADERBOARD_PING_ROLE_ID=your_role_id_here
```

### Duel Channels (optional)

```env
DUEL_CP_DUEL_CHANNEL=channel_id_or_name
DUEL_CP_BLITZ_CHANNEL=channel_id_or_name
DUEL_DSA_DUEL_CHANNEL=channel_id_or_name
DUEL_DSA_BLITZ_CHANNEL=channel_id_or_name
DUEL_ICPC_DUEL_CHANNEL=channel_id_or_name
DUEL_ICPC_BLITZ_CHANNEL=channel_id_or_name
```

### Website CORS

```env
BB_ALLOWED_ORIGINS=https://yoursite.com,https://www.yoursite.com,http://localhost:5173
```

---

## Part 3 — Database Setup

### 3.1 — Run the Base Schema

In your Supabase SQL Editor (or any PostgreSQL client), run:

```
database/schema.sql
```

This creates: `users`, `handles`, `weeks`, `months`, `problems`, `solves`, `difficulty_points`, `point_adjustments`, `bot_config`.

All statements are `CREATE TABLE IF NOT EXISTS` so it's safe to re-run on an existing database.

### 3.2 — Run Migrations in Order

```
database/migrate_v1_to_v2.sql
database/migration_duels.sql
database/migration_v2_2.sql
database/migration_website_sync.sql
```

These add: duel tables (`duels`, `duel_problems`, `duel_ratings`, `pair_history`), `monthly_solves` table, and website sync tables (`discord_messages`, `discord_threads`, `guild_snapshot`).

### 3.3 — Auto-created Tables

The following tables are created automatically the first time the bot starts. You don't need to create them manually:

- `community_threads` and `community_comments` — website forum
- `custom_contests` — contests added via `!newcontest`
- `team_members` — team roster from `!team`

### 3.4 — Default Difficulty Points

After first run, set your difficulty points if the defaults (easy=5, medium=10, hard=20, expert=35, master=50) don't suit your community:

```
!setpoints easy 5
!setpoints medium 10
!setpoints hard 20
!setpoints expert 35
!setpoints master 50
```

---

## Part 4 — Deploy on Render

1. Push the repository to GitHub.
2. Create a new **Web Service** on [render.com](https://render.com), connected to the repo.
3. Render reads `render.yaml` automatically. Build command: `pip install -r requirements.txt`. Start command: `python -u bot.py`.
4. Go to **Environment** in the Render dashboard and add all the variables from Part 2. Variables marked `sync: false` in `render.yaml` must be added manually.
5. Click **Manual Deploy -> Deploy Latest Commit**.

**Expected startup logs:**

```
[db] CF Neon DB pool initialized successfully.
[db] LC Neon DB pool initialized successfully.
Starting API server on port 10000...
Startup cooldown: 45s ...
Loading cogs...
  cogs.registry
  cogs.admin
  ...
  cogs.ai_agent
Logged in as CP-Bot (ID: ...)
```

The API server starts immediately and responds to `/health` while the bot is still in its startup delay. This satisfies Render's health check.

---

## Part 5 — First-Run Commands

Run these once after the bot is online.

### 5.1 — Set Up the First Week

```
!setweek "Week 1" 2026-08-18 2026-08-24
```

Without an active week, `!check`, `!problems`, and `!leaderboard` all show "no active week" errors.

### 5.2 — Set Up the Current Month

```
!setmonth "August 2026" 2026-08-01 2026-08-31
```

### 5.3 — Add Today's Problems

```
!addproblem cf 1234A easy 2026-08-18
!addproblem lc two-sum medium 2026-08-18
```

Date must fall within the active week's range.

### 5.4 — Post the Verification Embed

In your `#verification` channel:

```
!sendverification
```

This posts the persistent LinkedIn button embed. The buttons keep working after bot restarts.

### 5.5 — Send Verification DMs to Existing Members

```
!verifyall
```

This sends DMs to all members who don't yet have the Member role, and re-posts the verification embed in `#verification`. Members with DMs closed still see the channel embed.

### 5.6 — Set AtCoder Session Cookie

If you want AtCoder checking to work reliably (the bot uses a logged-in session):

```
!setcookie YOUR_REVEL_SESSION_COOKIE_VALUE
```

Your message is auto-deleted immediately after the value is stored.

### 5.7 — Do an Initial Channel Sync

If you want your existing channel history mirrored to the website:

```
!syncultimate
```

This fetches complete history for all 15 configured channels. Use `!syncall` for just the last 10 messages.

---

## Part 6 — Test Everything

### Verification Flow

1. Have a second account join the server (or ask someone).
2. They should only see `#verification`.
3. They click **Follow on LinkedIn**, the LinkedIn page opens.
4. They click **I Have Followed**.
5. The Verification role is removed, Member role is added.
6. `#welcome` shows the Zodiac webhook welcome embed.
7. They receive a DM with the onboarding guide.

### Solve Check

1. Register a handle: `!register cf tourist`
2. Run: `!check`
3. If you have an accepted submission for today's assigned CF problem, you get points.

### Inactivity Report

```
!inactivitycheck
```

This runs the full report immediately and posts it to `#inactivity-info`. Check the three tiers (15-29, 30-49, 50+ days) show correctly.

### Contest Reminders

```
!contestcheck
```

Triggers the reminder check immediately. If any contests are within 12h or 1h, reminders are posted to `#contest-reminder`.

### Duel Test

```
!duel @someone cp
```

If they don't accept within the timeout (around 15 seconds), you get auto-matched against the Z4s bot opponent.

### AI Coach Test

```
!coach
!explain binary search
!hint 1234A
```

If `GEMINI_API_KEY` is not set, you get a clear error message. If it is set, you get a Gemini-powered response.

---

## Admin Command Quick Reference

### Periods

```
!setweek "Week 1" 2026-08-18 2026-08-24
!setmonth "August 2026" 2026-08-01 2026-08-31
!currentweek
```

### Problems

```
!addproblem cf 1234A hard 2026-08-18
!addproblem lc two-sum medium 2026-08-18 7   (custom 7 pts)
!rius 42                                      (remove problem if unsolved)
!removeproblem 42 no                          (remove + delete solve records)
!setdifficulty 42 expert
```

### Solve Checking

```
!check              (member-facing, 3/day rate limit)
!checkall           (admin, requires Administrator permission)
```

### Leaderboards

```
!leaderboard        (member-facing, shows top 3 in each scope)
!lbfull daily       (admin, full paginated list)
!lbfull weekly
!lbfull monthly
```

### Points

```
!addpoints @user 10 Bonus for contest participation
!subpoints @user 5 Late submission
!setmemberpoints @user 50 Override
!pointlog @user
```

### Verification

```
!sendverification
!reverify @user
!verifyall
!verificationstatus
```

### Inactivity

```
!inactivity
!inactivitycheck
!exemptinactivity @user
!unexemptinactivity @user
```

### Contests and Team

```
!newcontest "Binary Beats Grand Contest 1" https://codeforces.com/contest/1234 2026-08-20T18:00
!team "Jane Doe" "Dev Lead" https://linkedin.com/in/janedoe https://github.com/janedoe
!updatestats 11 2 500
```

### Duels

```
!duelsetrank @user cp_duel 1400
!endduel                  (list active duels)
!endduel 42               (force-cancel duel 42, no rating change)
```

### Resets

```
!resetdaily            (informational, daily is auto)
!resetweek             (requires yes confirmation)
!resetmonth            (requires yes confirmation)
!resetalltime          (requires typing CONFIRM WIPE username)
!resetuser @user week
!resetproblem 42
!resetweekfull         (requires yes confirmation)
!saferemove 42
```

### Website Sync

```
!syncultimate          (full history, run once on setup)
!syncall               (last 10 messages, all channels)
!sync daily_editorials (last 10 messages, one channel)
!syncchannel daily_problems 500
!syncstatus
!syncprune             (30-day prune for daily_problems and daily_editorials)
```

### Database Maintenance

```
!prunedaily            (remove daily problem records older than 30 days)
!prune_community       (remove forum threads older than 15 days)
```

### Bot Config

```
!setcookie REVEL_SESSION_VALUE   (message auto-deleted)
!adminhelp                        (full admin reference in Discord)
```

---

## Troubleshooting

**"Role 'Member' not found" or "Role 'Verification' not found"**

The role name in `.env` must match the server role name exactly, including capitalisation. `Member` and `member` are different. Check `MEMBER_ROLE` and `VERIFICATION_ROLE` in your env.

**Bot cannot assign or remove roles — Missing Permissions**

The bot's role must be above Member and Verification in the role hierarchy. Go to Server Settings -> Roles and drag the CP-Bot role above both.

**Verification buttons do nothing after bot restart**

The bot registers the persistent view on startup. Try restarting the bot and clicking again. If the issue persists, run `!sendverification` again to post a fresh embed.

**New members can see all channels when they should only see `#verification`**

The Verification role is missing `View Channel: OFF` on those channels or categories. Set it at the category level so all child channels inherit it.

**`!checkall` gives "You need administrator permission"**

This command requires the Discord Administrator permission, not just the admin role. Grant Administrator to the user, or use a bot admin account.

**CF checks fail or show "Codeforces temporarily unavailable"**

Codeforces is rate-limiting the bot. The bot uses one bulk fetch per user and backs off entirely when blocked. Wait a few minutes and run `!check` again. Do not run `!checkall` repeatedly — it makes the block worse.

**AtCoder checks always fail**

Set the REVEL_SESSION cookie: `!setcookie your_cookie_value`. Get the cookie by logging into atcoder.jp and copying the `REVEL_SESSION` cookie from browser dev tools.

**AI Coach commands say "GEMINI_API_KEY not configured"**

Add `GEMINI_API_KEY=your_key` to your environment and restart the bot. Get a free key at [aistudio.google.com](https://aistudio.google.com).

**Inactivity reports are not posting**

The report only runs on the 15th, 20th, 25th, and 30th of each month at 09:00 IST. Run `!inactivitycheck` to trigger it immediately for testing. Confirm `INACTIVITY_CHANNEL_ID` or `INACTIVITY_CHANNEL` is set correctly.

**Contest reminders are not posting**

Check `CONTEST_REMINDER_CHANNEL` is set to the correct channel name. Run `!contestcheck` to trigger a check immediately. The reminder only fires when a contest is within 12h or 1h of its start time.

**`!problems` shows "No active week"**

Run `!setweek "Week 1" YYYY-MM-DD YYYY-MM-DD` to create one. The start and end dates must use the format `YYYY-MM-DD`.

**Bot keeps restarting on Render**

Check the logs for the restart reason. Common causes: invalid `DISCORD_TOKEN`, `DATABASE_URL` connection error, or missing required env variable. The bot uses exponential backoff for Discord 429 errors — it will recover on its own. For DB errors, check the Supabase connection string and that the database is active.

**Website shows no data from the bot**

Confirm `GUILD_ID` matches your Discord server ID. Run `!syncultimate` to populate the channel mirror tables. Confirm the `BB_API_KEY` in the bot's env matches the key in the website's server env.

**Duel match channel not deleted after match ends**

The channel auto-deletes 10 seconds after the match finishes. If the bot was offline when the match ended, the channel stays. Use `!endduel <id>` to force-cancel and the channel will be cleaned up.

---

## Complete File Structure (v7)

```
CP-Bot/
+-- bot.py                    v7 entry point, 14 cogs, startup logic
+-- config.py                 all env vars and constants
+-- api_server.py             50+ REST API endpoints
+-- duel_bot_engine.py        bot opponent (sigmoid timing model)
+-- duel_ranks.py             Newbie -> LGM tier definitions
+-- keep_alive.py             legacy health server (kept as fallback)
+-- ping.py                   health ping utility
+-- requirements.txt
+-- render.yaml
|
+-- cogs/
|    +-- admin.py             period config, contests, team, maintenance
|    +-- ai_agent.py          NEW: Gemini AI Coach
|    +-- checker.py           !check, !checkall, nightly tasks
|    +-- contests.py          12h/1h reminders, custom contests
|    +-- duels.py             1v1 system, 6 modes, bot opponent
|    +-- inactivity.py        tiered reports on 15/20/25/30
|    +-- leaderboard.py       daily/weekly/monthly boards
|    +-- points.py            manual point adjustments
|    +-- problems.py          daily problem management
|    +-- registry.py          handle registration, profile
|    +-- reset.py             scoped and targeted resets
|    +-- submissions.py       recent submissions view
|    +-- verification.py      auto-join, LinkedIn flow, reverify
|    +-- website_sync.py      15-channel mirror, hourly sync
|
+-- database/
|    +-- connection.py        3 asyncpg pools, auto-creates tables
|    +-- queries.py           core queries
|    +-- duel_queries.py      duel-specific queries
|    +-- schema.sql           base schema (run once)
|    +-- migrate_v1_to_v2.sql
|    +-- migration_duels.sql
|    +-- migration_v2_2.sql
|    +-- migration_website_sync.sql
|
+-- platforms/
     +-- base.py              abstract adapter
     +-- codeforces.py        bulk fetch, CFBlockedError
     +-- leetcode.py          GraphQL check
     +-- codechef.py          public API check
     +-- atcoder.py           session-cookie check
     +-- duel_cf_pool.py      CF problem pool, 6h cache
     +-- duel_lc_pool.py      LC problem sequencer
```

---

## First-Run Checklist

```
Discord Server:
  [ ] "Verification" role created
  [ ] "Member" role created
  [ ] CP-Bot role is above both in role hierarchy
  [ ] #verification channel created, permissions set
  [ ] #welcome channel permissions set
  [ ] #inactivity-info channel created
  [ ] #contest-reminder channel created
  [ ] All regular channels/categories: Verification role = View Channel OFF
  [ ] SERVER MEMBERS INTENT enabled in Developer Portal
  [ ] MESSAGE CONTENT INTENT enabled in Developer Portal
  [ ] Bot has Manage Roles permission
  [ ] Bot has Manage Webhooks permission
  [ ] Bot has Manage Channels permission

Environment Variables:
  [ ] DISCORD_TOKEN set
  [ ] DATABASE_URL set
  [ ] GUILD_ID set
  [ ] BB_API_KEY set (openssl rand -hex 32)
  [ ] GEMINI_API_KEY set (optional, for AI Coach)
  [ ] VERIFICATION_CHANNEL, WELCOME_CHANNEL, MEMBER_ROLE set
  [ ] INACTIVITY_CHANNEL_ID or INACTIVITY_CHANNEL set
  [ ] CONTEST_REMINDER_CHANNEL set

Database:
  [ ] schema.sql run
  [ ] migrate_v1_to_v2.sql run
  [ ] migration_duels.sql run
  [ ] migration_v2_2.sql run
  [ ] migration_website_sync.sql run

First-Run Commands:
  [ ] !setweek "Week 1" YYYY-MM-DD YYYY-MM-DD
  [ ] !setmonth "Month 1" YYYY-MM-DD YYYY-MM-DD
  [ ] !addproblem ... (add today's problems)
  [ ] !sendverification (post embed in #verification)
  [ ] !verifyall (DM existing members)
  [ ] !syncultimate (populate website mirror)
  [ ] !setcookie ... (if using AtCoder)
  [ ] !inactivitycheck (verify report works)
  [ ] !contestcheck (verify reminders work)
```

---

*CP-Bot v7 — Binary Beats*
*[README.md](README.md) has the full API reference and architecture documentation.*
