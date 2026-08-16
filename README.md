# 🤖 CP Discord Bot  v2

Competitive Programming tracker with **Daily / Weekly / Monthly leaderboards**, day-locked scoring, manual point management, and a fully professional embed UI.

---

## 📁 Project Structure

```
cp-bot/
├── bot.py                     ← Entry point  (prefix: /)
├── config.py                  ← Env vars, colours, timezone (IST)
├── keep_alive.py              ← Aiohttp /health server
├── ping.py                    ← Local keep-alive script
├── render.yaml                ← Render deploy config
├── requirements.txt
├── .env.example
│
├── cogs/
│   ├── admin.py               ← /setweek /setmonth /currentweek /setpoints /points
│   ├── checker.py             ← /check /checkall + auto-check every 6h
│   ├── leaderboard.py         ← /leaderboard  (daily + weekly + monthly)
│   ├── points.py  ★ NEW       ← /addpoints /subpoints /setmemberpoints /pointlog
│   ├── problems.py            ← /addproblem /removeproblem /problems /setdifficulty
│   ├── registry.py            ← /register /unregister /profile /handles
│   ├── reset.py               ← /resetdaily /resetweek /resetmonth /resetalltime …
│   └── submissions.py         ← /submissions
│
├── database/
│   ├── connection.py          ← asyncpg pool
│   ├── queries.py             ← All SQL (day windows, leaderboards, adjustments)
│   └── schema.sql             ← Run once in Supabase SQL editor
│
└── platforms/
    ├── __init__.py
    ├── base.py
    ├── atcoder.py  codeforces.py  codechef.py  leetcode.py
```

---

## ⚡ Key Changes from v1

| Feature | v1 | v2 |
|---------|----|----|
| Prefix | `!` | `/` |
| Leaderboard | weekly only | **Daily + Weekly + Monthly** |
| Day locking | ❌ Points any time in week | ✅ Points only on assigned day (midnight–midnight IST) |
| Remove problem | Deletes solve history | `/removeproblem 42` keeps history by default |
| Manual points | ❌ | ✅ `/addpoints` `/subpoints` `/setmemberpoints` |
| Monthly tracking | ❌ | ✅ `/setmonth` links problems to month |
| Reset scope | One command | `/resetdaily` `/resetweek` `/resetmonth` (scoped) |
| Embed style | Basic | Professional with thumbnails, icons, medals |

---

## 🗄️ Step 1 — Supabase Setup

1. Go to https://supabase.com → create a free project.
2. **SQL Editor → New Query** → paste `database/schema.sql` → **Run**.
3. **Settings → Database → Connection string → URI → Transaction pooler** (port 6543).
4. Copy the URI — this is your `DATABASE_URL`.

> **Upgrading from v1?** The new schema adds `months`, `point_adjustments` tables, and makes `assigned_date` required in `problems`. Run the full schema — `CREATE TABLE IF NOT EXISTS` is safe to re-run.

---

## 🤖 Step 2 — Discord Bot

1. https://discord.com/developers/applications → **New Application → Bot**.
2. Copy **Token** (`DISCORD_TOKEN`).
3. Enable **Privileged Intents**: Server Members + Message Content.
4. **OAuth2 → URL Generator** → Scopes: `bot` → Permissions: `Send Messages`, `Embed Links`, `Read Message History`, `View Channels` → invite to your server.

---

## 🚀 Step 3 — Deploy to Render

```bash
git init && git add . && git commit -m "v2 init"
gh repo create cp-discord-bot --private --push
```

1. https://render.com → **New → Web Service** → connect repo.
2. Render auto-reads `render.yaml`.
3. **Environment Variables** in Render dashboard:

| Key | Value |
|-----|-------|
| `DISCORD_TOKEN` | from Step 2 |
| `DATABASE_URL` | Supabase pooler URI |
| `RENDER_URL` | `https://your-bot.onrender.com` (set after first deploy) |
| `PREFIX` | `/` |
| `ADMIN_ROLE` | `Admin` (your role name) |

4. Deploy → watch logs for `✅ Logged in as …`

---

## ⏰ Step 4 — Keep-Alive (cron-job.org — free)

1. https://cron-job.org → Create cron job:
   - **URL:** `https://your-bot-name.onrender.com/health`
   - **Schedule:** Every 10 minutes
2. Done. Bot never sleeps.

---

## 📋 Command Reference

### 👤 Registration
| Command | Description |
|---------|-------------|
| `/register <platform> <handle>` | Link CP handle (verified) |
| `/unregister <platform>` | Unlink handle |
| `/profile [@user]` | Stats + linked handles |
| `/handles [platform]` | All registered members |

### 📅 Problems
| Command | Description |
|---------|-------------|
| `/addproblem <plat> <id> <diff> <YYYY-MM-DD> [pts]` | Add problem to a specific day |
| `/removeproblem <db_id> [keep_history]` | Remove problem (default: keeps solve history) |
| `/problems` | This week's schedule grouped by day |
| `/setdifficulty <db_id> <diff>` | Change difficulty |

### 🔍 Checking
| Command | Description |
|---------|-------------|
| `/check [@user]` | Check **today's** problems only |
| `/checkall` | Bulk-check all members (admin) |
| `/submissions <plat> [count] [@user]` | Recent platform submissions |

### 🏆 Leaderboard
| Command | Description |
|---------|-------------|
| `/leaderboard` | Shows Daily + Weekly + Monthly at once |

**Reset schedule:**
- Daily → resets at midnight IST every day
- Weekly → resets with `/resetweek` after week end-date
- Monthly → resets with `/resetmonth` after month end-date

### ⚙️ Admin — Config
| Command | Description |
|---------|-------------|
| `/setweek "Label" YYYY-MM-DD YYYY-MM-DD` | Create/activate a week |
| `/setmonth "Label" YYYY-MM-DD YYYY-MM-DD` | Create/activate a month |
| `/currentweek` | Show active week + month |
| `/setpoints <difficulty> <pts>` | Configure points per difficulty |
| `/points` | Show difficulty → points table |

### 🎛️ Admin — Manual Points
| Command | Description |
|---------|-------------|
| `/addpoints @user <n> [reason]` | Add bonus points |
| `/subpoints @user <n> [reason]` | Deduct points |
| `/setmemberpoints @user <n> [reason]` | Force adjustment total to exact value |
| `/pointlog [@user]` | View last 10 adjustments |

### 🔄 Admin — Reset (scoped)
| Command | Clears | Leaves intact |
|---------|--------|---------------|
| `/resetdaily` | Today's daily solves | Weekly + Monthly |
| `/resetweek` | All weekly solves | Monthly |
| `/resetmonth` | All monthly solves (incl. daily + weekly) | — |
| `/resetalltime` | ☢️ Everything | — |
| `/resetuser @user [week\|all]` | One member's solves | Others |
| `/resetproblem <db_id>` | Solves for one problem | Everything else |
| `/resetweekfull` | Solves + problems + deactivate week | — |

---

## 🧠 How Day Locking Works

Every problem has an `assigned_date` (set when you run `/addproblem`).

When `/check` runs:
1. It fetches only **today's problems** (IST date).
2. It checks if the member solved the problem **on or after midnight IST of that day**.
3. If the problem was solved **before** or **after** that day — **no points awarded**.

This means:
- Solving a problem one day early → no points (not yet the right day)
- Solving after midnight IST when the next day begins → no points for that problem
- Solving exactly on the day → ✅ full points

---

## 🌐 Platforms

| Key | Platform |
|-----|----------|
| `cf` | Codeforces |
| `lc` | LeetCode |
| `cc` | CodeChef |
| `atcoder` | AtCoder |

---

## 🛠️ Troubleshooting

| Problem | Fix |
|---------|-----|
| Bot doesn't start | Check `DISCORD_TOKEN` and Message Content Intent |
| DB error | Check `DATABASE_URL` uses pooler (port 6543) |
| `/check` says "no problems today" | Problems must be added with today's date via `/addproblem` |
| Monthly leaderboard empty | Run `/setmonth` first, then add problems (month must be active when problem is added) |
| Admin commands denied | Role name must exactly match `ADMIN_ROLE` env var |
| Bot sleeps on Render | Set up cron-job.org pinging `/health` every 10 min |
