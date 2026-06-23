# 🤖 CP Discord Bot

A fully featured **Competitive Programming Discord Bot** that tracks weekly problem assignments,
verifies solves across Codeforces, LeetCode, CodeChef, and AtCoder, and maintains leaderboards.
Backend hosted on **Render**, database on **Supabase**, keep-alive cron on **Vercel**.

---

## 📁 Project Structure

```
cp-bot/
├── bot.py                  ← Entry point
├── config.py               ← All env vars and constants
├── keep_alive.py           ← Aiohttp /health server (Render keep-alive)
├── ping.py                 ← Local ping script (alternative to Vercel cron)
├── render.yaml             ← Render deployment config
├── requirements.txt
├── .env.example            ← Copy to .env and fill in values
│
├── cogs/
│   ├── admin.py            ← !setweek, !currentweek, !setpoints, !points
│   ├── checker.py          ← !check, !checkall + auto-check every 6h
│   ├── leaderboard.py      ← !leaderboard [all]
│   ├── problems.py         ← !addproblem, !removeproblem, !problems, !setdifficulty
│   ├── registry.py         ← !register, !unregister, !profile, !handles
│   ├── reset.py            ← !resetweek, !resetalltime, !resetuser, !resetproblem, !resetweekfull
│   └── submissions.py      ← !submissions
│
├── database/
│   ├── connection.py       ← asyncpg pool
│   ├── queries.py          ← All SQL (including reset queries)
│   └── schema.sql          ← Run once in Supabase SQL editor
│
└── platforms/
    ├── __init__.py         ← Platform registry
    ├── base.py             ← Abstract PlatformAdapter
    ├── atcoder.py
    ├── codechef.py
    ├── codeforces.py
    └── leetcode.py
```

---

## 🗄️ Step 1 — Supabase Database Setup

1. Go to https://supabase.com and create a free project.
2. In your project, go to **SQL Editor → New Query**.
3. Paste the entire contents of `database/schema.sql` and click **Run**.
4. Go to **Settings → Database → Connection string → URI**.
   - Use the **Transaction pooler** URI (port `6543`), not the direct connection.
   - It looks like:
     ```
     postgresql://postgres.xxxx:YOUR_PASSWORD@aws-0-ap-south-1.pooler.supabase.com:6543/postgres
     ```
5. Copy this URI — you'll need it as `DATABASE_URL`.

---

## 🤖 Step 2 — Discord Bot Setup

1. Go to https://discord.com/developers/applications → **New Application**.
2. Go to **Bot** tab → **Add Bot** → copy the **Token** (this is `DISCORD_TOKEN`).
3. Under **Privileged Gateway Intents**, enable:
   - ✅ **Server Members Intent**
   - ✅ **Message Content Intent**
4. Go to **OAuth2 → URL Generator**:
   - Scopes: `bot`
   - Bot Permissions: `Send Messages`, `Embed Links`, `Read Message History`, `View Channels`
5. Copy the generated URL and open it to invite the bot to your server.

---

## 🚀 Step 3 — Deploy to Render

1. Push this project to a GitHub repository:
   ```bash
   git init
   git add .
   git commit -m "initial"
   gh repo create cp-discord-bot --private --push
   ```

2. Go to https://render.com → **New → Web Service**.
3. Connect your GitHub repo.
4. Render will auto-detect `render.yaml`. Confirm these settings:
   - **Environment:** Python
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python bot.py`

5. Add **Environment Variables** in the Render dashboard:

   | Key              | Value                                                      |
   |------------------|------------------------------------------------------------|
   | `DISCORD_TOKEN`  | Your bot token from Step 2                                 |
   | `DATABASE_URL`   | Supabase transaction pooler URI from Step 1                |
   | `RENDER_URL`     | Your Render service URL (e.g. `https://cp-bot.onrender.com`) — set AFTER first deploy |
   | `PREFIX`         | `!` (or whatever prefix you want)                          |
   | `ADMIN_ROLE`     | `Admin` (or your server's admin role name)                 |

6. Click **Deploy**. Watch the logs — you should see:
   ```
   🔌 Connecting to database...
   ✅ Database connected.
      ✓ Loaded cogs.registry
      ✓ Loaded cogs.admin
      ...
   ✅ Logged in as YourBot#1234 (ID: 123456789)
   🌐 Health server listening on port 10000
   ```

7. Copy your Render service URL and add it as `RENDER_URL` in environment variables.

---

## ⏰ Step 4 — Keep-Alive Cron Job

Render free tier sleeps after 15 minutes of inactivity. The bot has a built-in `/health`
endpoint. You need an external service to ping it every 10 minutes.

### Option A — Vercel Cron (requires Vercel Pro for every 10 min)

Use the separate `cp-bot-keepalive/` project (see its own README).

### Option B — cron-job.org (100% free, recommended)

1. Sign up at https://cron-job.org (free).
2. Click **Create Cron Job**:
   - **Title:** CP Bot Keep-Alive
   - **URL:** `https://your-bot-name.onrender.com/health`
   - **Schedule:** Every 10 minutes
3. Save. Done. Your bot will never sleep again.

### Option C — Run ping.py locally

```bash
# In your .env set RENDER_URL=https://your-bot-name.onrender.com
python ping.py
```

This pings every 10 minutes while your machine is on.

---

## 💻 Local Development

```bash
# 1. Clone and enter
git clone https://github.com/you/cp-discord-bot.git
cd cp-discord-bot

# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up environment
cp .env.example .env
# Edit .env with your DISCORD_TOKEN, DATABASE_URL

# 5. Run the bot
python bot.py
```

---

## 📋 Command Reference

### 👤 Registration
| Command | Description |
|---------|-------------|
| `!register <platform> <handle>` | Link your CP handle (auto-verified) |
| `!unregister <platform>` | Unlink a handle |
| `!profile [@user]` | View linked handles and point totals |
| `!handles [platform]` | List all registered members |

### 📅 Problems (Admin)
| Command | Description |
|---------|-------------|
| `!addproblem <plat> <id> <diff> [pts]` | Add a problem to current week |
| `!removeproblem <db_id>` | Remove a problem |
| `!problems` | List this week's problems |
| `!setdifficulty <db_id> <diff>` | Change a problem's difficulty |

### 🔍 Checking
| Command | Description |
|---------|-------------|
| `!check [@user]` | Check your (or another's) solve status |
| `!checkall` | Bulk-check all members *(admin)* |
| `!submissions <plat> [count] [@user]` | View recent platform submissions |

### 🏆 Leaderboard
| Command | Description |
|---------|-------------|
| `!leaderboard` | This week's rankings |
| `!leaderboard all` | All-time rankings |

### ⚙️ Admin Config
| Command | Description |
|---------|-------------|
| `!setweek "Label" YYYY-MM-DD YYYY-MM-DD` | Create/activate a new week |
| `!currentweek` | Show the active week |
| `!setpoints <difficulty> <pts>` | Configure points per difficulty |
| `!points` | Show difficulty → points table |

### 🔄 Reset (Admin)
| Command | Description |
|---------|-------------|
| `!resetweek` | Clear solves for current week (problems kept) |
| `!resetweekfull` | Delete solves + problems + deactivate week |
| `!resetalltime` | ☢️ Wipe ALL solves ever (requires exact confirmation phrase) |
| `!resetuser @user [week\|all]` | Reset a specific member's solves |
| `!resetproblem <db_id>` | Un-mark all solves for one problem |

### 🌐 Platforms
| Key | Platform |
|-----|----------|
| `cf` | Codeforces |
| `lc` | LeetCode |
| `cc` | CodeChef |
| `atcoder` | AtCoder |

---

## 🔧 Adding a New Platform

1. Create `platforms/myplatform.py`, subclass `PlatformAdapter`, implement:
   - `verify_handle(handle)` → `(bool, str)`
   - `get_recent_submissions(handle, limit)` → `list[Submission]`
   - `check_solved(handle, problem_id, since_ts)` → `(bool, str)`
2. Add it to `platforms/__init__.py`:
   ```python
   from platforms.myplatform import MyPlatformAdapter
   ADAPTERS["mp"] = MyPlatformAdapter()
   ```
3. Done — all cogs pick it up automatically.

---

## 🛠️ Troubleshooting

| Problem | Fix |
|---------|-----|
| Bot doesn't start | Check `DISCORD_TOKEN` is correct and Message Content Intent is enabled |
| Database error on startup | Check `DATABASE_URL` uses the **transaction pooler** (port 6543), not direct |
| Commands don't work | Ensure the bot has `Send Messages` + `Read Message History` permissions |
| `!register` fails | The platform API may be rate-limiting; wait and retry |
| Bot sleeps on Render | Make sure cron-job.org or Vercel cron is pinging `/health` every ≤10 min |
| Admin commands denied | Ensure your Discord role is named exactly as `ADMIN_ROLE` env var, or you have Administrator permission |

---

## 📄 Environment Variables Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DISCORD_TOKEN` | ✅ | — | Bot token from Discord Developer Portal |
| `DATABASE_URL` | ✅ | — | Supabase PostgreSQL transaction pooler URI |
| `RENDER_URL` | ✅ | — | Your Render service URL (for keep-alive) |
| `PREFIX` | ❌ | `!` | Bot command prefix |
| `ADMIN_ROLE` | ❌ | `Admin` | Discord role name with admin privileges |
| `PORT` | ❌ | `10000` | HTTP health server port (Render sets this automatically) |
