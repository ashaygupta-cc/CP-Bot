# CP Practice Bot

**Competitive Programming Practice Tracker + 1v1 Duel System**

A comprehensive Discord bot for managing competitive programming practice, tracking daily problem solving, maintaining leaderboards, and engaging in ranked 1v1 duels across Codeforces, LeetCode, CodeChef, and AtCoder.

---

## Overview

### Core Features

**Practice Tracking**
- Multi-platform handle registration (Codeforces, LeetCode, CodeChef, AtCoder)
- Daily problem scheduling with automatic solve verification
- Day-locked leaderboards (24-hour IST windows)
- Weekly and monthly leaderboards with automatic resets
- Point adjustment system for manual score management

**Competitive Dueling**
- Real-time 1v1 matches across 6 game modes
- Codeforces-style rating system (800 Newbie → 3000+ Legendary Grandmaster)
- Private match channels with automatic cleanup
- Bot opponent available at any skill level
- Detailed match history and rankings per mode

---

## Technology Stack

- **Language:** Python 3.8+
- **Discord Framework:** discord.py 2.0+
- **Database:** PostgreSQL (Supabase)
- **Hosting:** Render (with keep-alive)
- **APIs:** Codeforces, LeetCode, CodeChef, AtCoder

---

## Project Structure

```
cp-bot/
├── bot.py                      Entry point, help commands
├── config.py                   Configuration, environment variables
├── keep_alive.py               Health endpoint server
├── requirements.txt
│
├── cogs/
│   ├── admin.py                Period setup, points configuration
│   ├── checker.py              Solve verification, auto-checking
│   ├── leaderboard.py          Daily/weekly/monthly rankings
│   ├── points.py               Manual point adjustments
│   ├── problems.py             Problem management
│   ├── registry.py             Handle registration
│   ├── reset.py                Data resets (scoped and targeted)
│   ├── submissions.py          Recent submission browsing
│   ├── contests.py             Upcoming contest reminders
│   ├── inactivity.py           Inactivity tracking and warnings
│   ├── verification.py         LinkedIn verification on join
│   └── duels.py                1v1 duel system
│
├── database/
│   ├── connection.py           asyncpg connection pooling
│   ├── queries.py              SQL access layer
│   ├── duel_queries.py         Duel-specific database operations
│   └── schema.sql              Initial database schema
│
└── platforms/
    ├── base.py                 Abstract adapter class
    ├── codeforces.py           Codeforces API integration
    ├── leetcode.py             LeetCode API integration
    ├── codechef.py             CodeChef API integration
    ├── atcoder.py              AtCoder session-based checking
    ├── duel_cf_pool.py         Codeforces problem selector
    └── duel_lc_pool.py         LeetCode problem selector
```

---

## Quick Start

### 1. Database Setup

1. Create a free PostgreSQL instance at [supabase.com](https://supabase.com)
2. In Supabase SQL Editor, paste and execute `database/schema.sql`
3. Copy the connection string (Transaction Pooler, port 6543) — this is your `DATABASE_URL`

### 2. Discord Bot Setup

1. Create an application at [discord.com/developers/applications](https://discord.com/developers/applications)
2. Add a Bot to the application and copy the token — this is your `DISCORD_TOKEN`
3. Enable these Privileged Gateway Intents:
   - Server Members Intent
   - Message Content Intent
4. In OAuth2 > URL Generator, select `bot` scope with these permissions:
   - Send Messages
   - Embed Links
   - Read Message History
   - View Channels
5. Use the generated URL to invite the bot to your server

### 3. Environment Variables

Create a `.env` file in the project root:

```env
DISCORD_TOKEN=your_bot_token
DATABASE_URL=postgresql://user:password@host:6543/database
PREFIX=!
ADMIN_ROLE=Admin
RENDER_URL=https://your-service.onrender.com
PORT=10000
```

### 4. Dependencies

```bash
pip install -r requirements.txt
```

### 5. Run

```bash
python bot.py
```

---

## Command Reference

### Member Commands

#### Registration

```
!register <platform> <handle>     Link your CP handle
!unregister <platform>            Remove a linked handle
!profile [@user]                  View your stats and handles
!handles [platform]               List all registered members
```

Supported platforms: `cf` (Codeforces), `lc` (LeetCode), `cc` (CodeChef), `atcoder` (AtCoder)

#### Problems & Checking

```
!problems                          This week's problem schedule
!check [@user]                     Check today's solve status
!submissions <platform> [n] [@user] Browse recent submissions
```

#### Leaderboards

```
!leaderboard                       Daily + Weekly + Monthly rankings
```

Reset schedule: Daily (automatic at midnight IST), Weekly (manual), Monthly (manual)

#### Duels

```
!duel @user cp_blitz              Challenge a player (3-problem format, default)
!duel @user cp_blitz 2            Challenge a player (2-problem format)
!duel @user cp_blitz 3            Challenge a player (3-problem format)
!duel bot cp_blitz                Bot match at your rating (3 problems, default)
!duel bot cp_blitz 2              Bot match (2-problem format)
!duel bot cp_blitz 3              Bot match (3-problem format)
!duel bot cp_blitz 1600           Bot match at 1600 rating (3 problems)
!duel bot cp_blitz 2 1600         Bot match (2 problems, 1600 rating)
!duelprofile [@user]              View duel ratings and W/L record
!duelrank [mode]                  Check your rank/tier
```

**Problem Formats:**
- **2-Problem Format:** Medium difficulty + Medium difficulty
- **3-Problem Format (Bo3):** Easy → Medium → Hard with timing/cooldown between problems

**Duel Modes:** `cp_blitz` `cp_duel` `dsa_blitz` `dsa_duel` `icpc_blitz` `icpc_duel`

All modes support both 2 and 3 problem formats. Format selection is **optional** — defaults to 3 problems if not specified.

**Rating Tiers:** Newbie (800) → Pupil → Specialist → Expert → Candidate Master → Master → International Master → Grandmaster → International Grandmaster → Legendary Grandmaster (3000+)

### Admin Commands

#### Configuration

```
!setweek "Label" YYYY-MM-DD YYYY-MM-DD    Create/activate a week
!setmonth "Label" YYYY-MM-DD YYYY-MM-DD   Create/activate a month
!currentweek                               Show active week and month
!setpoints <difficulty> <pts>              Configure points per tier
!points                                    View difficulty → points table
!duelconfig show                           View all duel settings
!duelconfig <key> <value>                  Update duel setting
```

#### Problems

```
!addproblem <plat> <id> <diff> <YYYY-MM-DD> [pts]   Add a problem
!removeproblem <db_id> [keep_history]                Remove a problem
!setdifficulty <db_id> <diff>                        Change difficulty
```

#### Points

```
!addpoints @user <n> [reason]              Grant bonus points
!subpoints @user <n> [reason]              Deduct points
!setmemberpoints @user <n> [reason]        Force-set adjustment total
!pointlog [@user]                          View recent adjustments
```

#### Checking

```
!checkall                                  Bulk-check all members
```

#### Resets

```
!resetdaily                      Today's daily solves only
!resetweek                       This week's solves
!resetmonth                      This month's solves (all scopes)
!resetalltime                    Everything (irreversible)
!resetuser @user [week|all]      One member's solves
!resetproblem <db_id>            Solves for one problem
!saferemove <db_id>              Remove problem (warns if solved)
!resetweekfull                   Delete week + solves + problems
```

#### Duels

```
!duelsetrank @user <mode> <rating>        Set someone's rating
!duelcancel                               Force-cancel a duel
```

---

## Day-Locking System

Every problem has an assigned date. When a member runs `!check`:

1. Only problems assigned to today (IST) are checked
2. The solve must occur on or after midnight IST of that day
3. The solve must occur before midnight IST the next day
4. Only then are points awarded

| Scenario | Result |
|----------|--------|
| Solved one day early | No points — window not open |
| Solved on correct day | Full points awarded |
| Solved next day or later | No points — window closed |

---

## Duel System Details

### Modes

**Codeforces Blitz & Duel**
- Real Elo rating system
- Problems seeded from player ratings ± offset
- K-factor: 24 (blitz), 32 (duel)

**LeetCode Blitz & Duel**
- Fixed points per difficulty
- Blitz: single medium problem
- Duel: best-of-3 (easy → medium → hard)

**ICPC Blitz & Duel**
- Real Elo rating system
- Requires math + algorithmic tags
- K-factor: 40
- Rating gate: 1400+ minimum CF rating

### Match Flow

1. Challenge issued via `!duel @user <mode> [format]` where format is 2 or 3 (default: 3)
2. Accept/decline buttons appear (90-second timeout)
3. Private channel created (format: `<mode>-<num>-<player1>-vs-<player2>`)
4. **2-Problem Format:** Problem 1 (Medium) → Problem 2 (Medium)
5. **3-Problem Format:** Problem 1 (Easy) → Problem 2 (Medium) → Problem 3 (Hard)
6. For each problem:
   - Round intro embed with problem details
   - 3-2-1-GO countdown
   - Players solve on real platform (CF/LeetCode)
   - Bot verifies submissions (every 45 seconds + on-demand)
   - First correct solve wins the game
   - Cooldown before next problem (if applicable)
7. Match finishes → result posted → channel deleted (30 seconds)
8. Ratings updated (based on wins/losses across format)

### Rating Tiers

| Rating | Tier | Color |
|--------|------|-------|
| 800+ | Newbie | Gray |
| 1200+ | Pupil | Green |
| 1400+ | Specialist | Cerulean |
| 1600+ | Expert | Orange Red |
| 1950+ | Candidate Master | Orange |
| 2150+ | Master | Dark Cyan |
| 2300+ | International Master | Cyan |
| 2450+ | Grandmaster | Dodger Blue |
| 2650+ | International Grandmaster | Sky Blue |
| 3000+ | Legendary Grandmaster | Turquoise |

---

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DISCORD_TOKEN` | — | Bot token from Discord Developer Portal |
| `DATABASE_URL` | — | PostgreSQL connection string (port 6543) |
| `PREFIX` | `!` | Command prefix |
| `ADMIN_ROLE` | `Admin` | Name of admin role in your server |
| `RENDER_URL` | — | Your Render service URL |
| `PORT` | `10000` | Port for keep-alive server |
| `IST_OFFSET_HOURS` | `5` | Hours offset from UTC |
| `IST_OFFSET_MINS` | `30` | Minutes offset from UTC |

### Duel Configuration

After deployment, admins can tune settings:

```
!duelconfig cp_offset 150                  Problem difficulty band
!duelconfig cp_blitz_k 24                  K-factor for CF Blitz
!duelconfig countdown_seconds 3            Countdown length
!duelconfig spectator_role Spectator       Spectator access role
!duelconfig bot_affects_rating false       Bot practice rating toggle
```

Full list available via `!duelconfig show`

---

## Deployment

### Render

1. Create a new Web Service on [render.com](https://render.com)
2. Connect your GitHub repository
3. Render automatically reads `render.yaml`
4. Add environment variables in the Render dashboard
5. Deploy

### Keep-Alive (Free Tier)

Render spins down idle services. Set up a cron job:

1. Go to [cron-job.org](https://cron-job.org)
2. Create new job, URL: `https://your-service.onrender.com/health`
3. Schedule: every 10 minutes

---

## Development

### Adding a New Platform

1. Create `platforms/<platform>.py` inheriting from `base.py`
2. Implement `get_recent_submissions()` and `verify_solve()`
3. Add platform key to `SUPPORTED_PLATFORMS` in `config.py`
4. Update schema if needed (handle verification, etc.)

### Database Migrations

1. Test schema changes locally
2. Run migration in Supabase SQL Editor
3. Update `database/schema.sql` for reproducibility

### Testing

```bash
# Lint
python -m py_compile *.py cogs/*.py database/*.py platforms/*.py

# Test imports
python -c "import bot; print('OK')"
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Bot won't start | Check `DISCORD_TOKEN` and Message Content Intent enabled |
| Database connection error | Verify `DATABASE_URL` uses port 6543 (transaction pooler) |
| `!check` shows no problems | Add problems with today's date via `!addproblem` |
| Leaderboards empty | Activate week/month first, then add problems |
| Commands denied to admins | Verify `ADMIN_ROLE` matches exactly (case-sensitive) |
| Duel commands missing in help | Restart bot (cogs reload required) |
| Codeforces blocking requests | System retries automatically; wait 1-2 hours if persistent |
| Bot sleeps on Render | Configure cron job to ping `/health` every 10 minutes |

---

## Architecture Decisions

**Day-Locking Window**
Problems are scoped to exact 24-hour IST windows to prevent gaming the system (early solves, late solves on next day).

**Repeat-Avoidance Weighting**
Duel problems already played by a pair receive 0.1x selection weight instead of hard exclusion, allowing natural repeats after approximately 20 matches.

**Private Duel Channels**
Match rooms are private (only players + admins) to avoid distraction and keep competitive space clean.

**Reusable Duel Numbers**
Channel numbers (e.g., `cp-duel-3`) are freed immediately when a match finishes, preventing infinite increment while supporting simultaneous matches via lock-protected allocation.

**Elo vs Fixed Points**
Codeforces and ICPC use real Elo to reflect competitive skill progression. LeetCode uses fixed points (easier to tune per difficulty) since LC rating is not directly comparable to CF rating.

---

## Performance Notes

- Database queries use connection pooling (asyncpg)
- Problem pickers cache CF/LC problemsets in memory (6-hour TTL)
- Auto-checking runs every 6 hours in background
- Duel checks run every 45 seconds (scoped to active matches)
- All Discord API calls are rate-limited and retry with backoff

---

## Support & Feedback

For issues, feature requests, or questions:

1. Check the Troubleshooting section above
2. Review command documentation
3. Verify environment variable setup
4. Check bot logs for specific errors

---

## License

This project is provided as-is for educational and competitive programming community use.

---

## Credits

Built with [discord.py](https://github.com/Rapptz/discord.py), [asyncpg](https://github.com/MagicStack/asyncpg), and [Supabase](https://supabase.com).

Integrates with Codeforces, LeetCode, CodeChef, and AtCoder APIs.

---

**Version:** 2.0 (Practice Tracker + Duel System)

**Author:** Ashay Gupta (AKA Zodiac)

**Last Updated:** July 2024

**Status:** Production Ready