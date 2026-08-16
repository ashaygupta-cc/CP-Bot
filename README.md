# CP Discord Bot

A competitive programming practice tracker for Discord servers. Supports multi-platform handle registration, day-locked problem scheduling, automated solve checking, and daily, weekly, and monthly leaderboards.

---

## Table of Contents

- [Project Structure](#project-structure)
- [Setup: Database](#setup-database)
- [Setup: Discord Bot](#setup-discord-bot)
- [Setup: Deployment](#setup-deployment)
- [Setup: Keep-Alive](#setup-keep-alive)
- [Environment Variables](#environment-variables)
- [Command Reference](#command-reference)
- [How Day Locking Works](#how-day-locking-works)
- [Supported Platforms](#supported-platforms)
- [Troubleshooting](#troubleshooting)

---

## Project Structure

```
cp-bot/
├── bot.py                    Entry point (prefix: !)
├── config.py                 Environment variables, colors, timezone (IST)
├── keep_alive.py             Aiohttp /health server
├── ping.py                   Local keep-alive script
├── render.yaml               Render deploy config
├── requirements.txt
│
├── cogs/
│   ├── admin.py              !setweek !setmonth !currentweek !setpoints !points
│   ├── checker.py            !check !checkall + auto-check every 6h
│   ├── leaderboard.py        !leaderboard (daily + weekly + monthly)
│   ├── points.py             !addpoints !subpoints !setmemberpoints !pointlog
│   ├── problems.py           !addproblem !removeproblem !problems !setdifficulty
│   ├── registry.py           !register !unregister !profile !handles
│   ├── reset.py              !resetdaily !resetweek !resetmonth !resetalltime ...
│   └── submissions.py        !submissions
│
├── database/
│   ├── connection.py         asyncpg connection pool
│   ├── queries.py            All SQL (day windows, leaderboards, adjustments)
│   └── schema.sql            Run once in Supabase SQL editor
│
└── platforms/
    ├── base.py
    ├── atcoder.py
    ├── codechef.py
    ├── codeforces.py
    └── leetcode.py
```

---

## Setup: Database

1. Go to [supabase.com](https://supabase.com) and create a free project.
2. Open **SQL Editor > New Query**, paste the contents of `database/schema.sql`, and run it.
3. Go to **Settings > Database > Connection string > URI > Transaction pooler** (port 6543).
4. Copy the URI — this becomes your `DATABASE_URL`.

> **Upgrading from v1:** The schema adds `months` and `point_adjustments` tables and makes `assigned_date` required on `problems`. The full schema uses `CREATE TABLE IF NOT EXISTS`, so it is safe to re-run.

---

## Setup: Discord Bot

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications) and create a new application.
2. Navigate to the **Bot** tab and copy the token — this is your `DISCORD_TOKEN`.
3. Enable **Privileged Gateway Intents**: Server Members Intent and Message Content Intent.
4. Go to **OAuth2 > URL Generator**, select the `bot` scope, and grant the following permissions:
   - Send Messages
   - Embed Links
   - Read Message History
   - View Channels
5. Use the generated URL to invite the bot to your server.

---

## Setup: Deployment

The project is configured for deployment on [Render](https://render.com) via `render.yaml`.

```bash
git init && git add . && git commit -m "initial commit"
gh repo create cp-discord-bot --private --push
```

1. Go to Render and create a **New > Web Service**, connecting your repository.
2. Render will automatically read `render.yaml`.
3. Add the following environment variables in the Render dashboard.

---

## Environment Variables

| Variable        | Description                                         |
|-----------------|-----------------------------------------------------|
| `DISCORD_TOKEN` | Bot token from the Discord developer portal         |
| `DATABASE_URL`  | Supabase transaction pooler URI (port 6543)         |
| `RENDER_URL`    | Your Render service URL (set after first deploy)    |
| `PREFIX`        | Command prefix, defaults to `!`                     |
| `ADMIN_ROLE`    | Exact name of the admin role in your server         |

---

## Setup: Keep-Alive

Render's free tier spins down idle services. To prevent this, set up a cron job to ping the health endpoint.

1. Go to [cron-job.org](https://cron-job.org) and create a new cron job.
2. Set the URL to `https://your-bot-name.onrender.com/health`.
3. Set the schedule to every 10 minutes.

---

## Command Reference

### Registration

| Command | Description |
|---------|-------------|
| `!register <platform> <handle>` | Link a CP handle (verified) |
| `!unregister <platform>` | Unlink a handle |
| `!profile [@user]` | View stats and linked handles |
| `!handles [platform]` | List all registered members |

### Problems

| Command | Description |
|---------|-------------|
| `!addproblem <platform> <id> <difficulty> <YYYY-MM-DD> [pts]` | Add a problem to a specific day |
| `!removeproblem <db_id> [keep_history]` | Remove a problem (keeps solve history by default) |
| `!problems` | View this week's schedule grouped by day |
| `!setdifficulty <db_id> <difficulty>` | Change a problem's difficulty |

### Solve Checking

| Command | Description |
|---------|-------------|
| `!check [@user]` | Check today's problems only |
| `!checkall` | Bulk-check all members (admin) |
| `!submissions <platform> [count] [@user]` | View recent platform submissions |

### Leaderboard

| Command | Description |
|---------|-------------|
| `!leaderboard` | Show daily, weekly, and monthly leaderboards |

Reset schedule:
- **Daily** — resets automatically at midnight IST
- **Weekly** — resets manually with `!resetweek` after the week's end date
- **Monthly** — resets manually with `!resetmonth` after the month's end date

### Admin — Configuration

| Command | Description |
|---------|-------------|
| `!setweek "Label" YYYY-MM-DD YYYY-MM-DD` | Create and activate a week |
| `!setmonth "Label" YYYY-MM-DD YYYY-MM-DD` | Create and activate a month |
| `!currentweek` | Show the active week and month |
| `!setpoints <difficulty> <pts>` | Configure points per difficulty tier |
| `!points` | Show the difficulty-to-points mapping |

### Admin — Manual Points

| Command | Description |
|---------|-------------|
| `!addpoints @user <n> [reason]` | Add bonus points to a member |
| `!subpoints @user <n> [reason]` | Deduct points from a member |
| `!setmemberpoints @user <n> [reason]` | Force a member's adjustment total to an exact value |
| `!pointlog [@user]` | View the last 10 point adjustments |

### Admin — Resets

| Command | Clears | Leaves intact |
|---------|--------|---------------|
| `!resetdaily` | Today's daily solves | Weekly, monthly |
| `!resetweek` | All weekly solves | Monthly |
| `!resetmonth` | All monthly solves (includes daily and weekly) | — |
| `!resetalltime` | Everything | — |
| `!resetuser @user [week\|all]` | One member's solves | All other members |
| `!resetproblem <db_id>` | Solves for one specific problem | Everything else |
| `!resetweekfull` | Solves, problems, and the active week | — |

---

## How Day Locking Works

Every problem has an `assigned_date` set when it is added via `!addproblem`. When `!check` runs:

1. It fetches only the problems assigned to today's IST date.
2. It checks whether the member solved the problem on or after midnight IST of that day.
3. If the problem was solved before that day begins or after it ends, no points are awarded.

| Scenario | Result |
|----------|--------|
| Solved one day early | No points — the window has not opened |
| Solved on the correct day | Full points |
| Solved after midnight IST the following day | No points — the window has closed |

---

## Supported Platforms

| Key | Platform |
|-----|----------|
| `cf` | Codeforces |
| `lc` | LeetCode |
| `cc` | CodeChef |
| `atcoder` | AtCoder |

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Bot fails to start | Verify `DISCORD_TOKEN` and that Message Content Intent is enabled |
| Database connection error | Confirm `DATABASE_URL` uses the transaction pooler URI on port 6543 |
| `!check` reports no problems today | Problems must be added with today's date via `!addproblem` |
| Monthly leaderboard is empty | Run `!setmonth` first, then add problems — a month must be active when a problem is added |
| Admin commands are denied | The role name in `ADMIN_ROLE` must exactly match the role name in your server |
| Bot sleeps on Render | Configure a cron job to ping `/health` every 10 minutes |
