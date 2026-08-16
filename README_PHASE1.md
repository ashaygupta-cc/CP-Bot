# Binary Beats — Phase 1

This is the platform foundation: Discord identity, a public/member split, real
routing, and the first four bot-backed pages. Everything is built with the
existing tokens and primitives, so it should read as version 2.0 of the same
site rather than a new one.

---

## What was actually broken

Worth reading before you apply anything, because two of these were blocking.

**The root `package.json` was the old Mongo backend's file.** Name `backend`,
`"type": "commonjs"`, dependencies `mongoose`/`helmet`/`express-rate-limit`,
and no `react`, `vite`, `tailwindcss` or build script at all. `npm install &&
npm run build` could not work on a clean clone. It has been restored to the
real frontend manifest (`binary-beats-web`, version bumped to 2.0.0).

**`server/.env` was committed with live values** — Discord client secret,
session secret and database URL. It has been removed from the tree and
`.gitignore` now blocks `.env` everywhere. **Rotate those three secrets**:
they are in your git history regardless of this change.

**`CP-Bot/.env` was committed too.** Same treatment, same advice.

**The bot had no API.** `keep_alive.py` exposed only `/health`, so there was
nothing for the website to consume. `api_server.py` adds it.

**The navbar had no mobile menu.** Tabs were `hidden md:flex` with no
replacement below that breakpoint, so the site was unnavigable on a phone.

**`BinaryBeats-main/backend/`** was a second, superseded Express+Mongo backend
sitting inside the repo. Removed — `server/` is the real one. If you still
need anything from it, pull it out of git history rather than the working tree.

---

## Apply it

### 1. CP-Bot

Nothing to install; `aiohttp` and `asyncpg` are already in
`requirements.txt`.

New file: `api_server.py`
Changed: `bot.py` (one import), `config.py` (new settings block),
`render.yaml`, `.gitignore`, `.env.example`

Set these on the bot service:

```
GUILD_ID=<your Discord server id>
BB_API_KEY=<openssl rand -hex 32>
BB_ALLOWED_ORIGINS=http://localhost:5173,https://<your-site>.vercel.app
```

`GUILD_ID` is required. The cogs get the guild from `ctx.guild.id`, but an
HTTP request has no Discord context, so the API has to be told.

Verify after deploy:

```
curl https://<bot-host>/health
curl https://<bot-host>/api/stats
curl "https://<bot-host>/api/leaderboard/points?scope=all&limit=5"
```

`keep_alive.py` is untouched and still works — revert the import in `bot.py`
if you need to roll back.

### 2. Website backend

```bash
cd server
cp .env.example .env    # fill it in
npm install
npm run dev
```

The values that matter:

| Variable | Note |
|---|---|
| `SESSION_SECRET` | `openssl rand -hex 32` |
| `DISCORD_CLIENT_ID` / `DISCORD_CLIENT_SECRET` | Discord Developer Portal → your app → OAuth2 |
| `DISCORD_REDIRECT_URI` | `http://localhost:4000/api/discord/callback` — port **4000**, the API, not the Vite port |
| `DISCORD_GUILD_ID` | same server id as the bot's `GUILD_ID` |
| `BOT_API_URL` | bot service base URL, no trailing slash |
| `BB_API_KEY` | must match the bot exactly |
| `WEB_ORIGIN` | where the frontend runs — used for the post-login redirect |

Register `DISCORD_REDIRECT_URI` verbatim under **OAuth2 → Redirects** in the
Developer Portal. If it is missing there, the callback fails silently and you
land back on the site logged out with no error.

### 3. Frontend

```bash
cp .env.example .env.local   # set VITE_DISCORD_INVITE
npm install
npm run dev
```

Leave `VITE_API_URL` empty locally — `vite.config.ts` proxies `/api` to
port 4000. Set it in Vercel only if the API is on a different domain.

### 4. Check it works

1. Open the site logged out. Home, Daily Problems, Leaderboards and Community
   should all load and show real bot data.
2. Sign in with Discord. The navbar should show your avatar.
3. If you are in the server, `#/arena` opens. If not, you get the gate card
   with a Join button, and the navbar shows a `guest` tag.
4. Join the server, then reload — the gate should clear within ~5 minutes
   without signing out (membership is re-checked, not baked into the session).

---

## What's in it

### Routing

`src/lib/router.ts` — hash-based, no new dependency. Hash rather than
`pushState` because the frontend deploys as a static Vite build and there is no
server to rewrite deep paths; `#/leaderboards/cp-duel` deep-links and survives
a refresh with zero config.

Routes: `#/` · `#/problems` · `#/leaderboards[/board]` · `#/community` ·
`#/feed` · `#/arena` · `#/ranking` · `#/u/<discord-id>` · anything else → 404 page.

`src/data/site.ts` is the single nav map. The navbar, mobile drawer and footer
all read from it, so they cannot drift apart. Adding a module is one entry.

### Auth and gating

`server/src/routes/discord.ts` — OAuth login/callback/me/logout plus a
`requireMember` middleware for future member-only endpoints.

Membership is deliberately **not** stored in the session JWT. It is re-read
from Discord on `/me` with a 5-minute cache, so joining or leaving the server
takes effect without a re-login. Non-members get `403 not_a_member`, which the
frontend renders as the gate card rather than an error.

`src/components/ui/MemberGate.tsx` is the one lock surface for every gated
feature. Pass `gateReason` from `useDiscordAuth` straight into it.

### Bot data

`server/src/routes/bot.ts` is a read-through proxy, not a passthrough. It
exists so `BB_API_KEY` never reaches the browser, so there is one CORS origin,
and so a free-tier bot instance is not hammered by every tab switch. It has a
per-path TTL cache, a route allow-list, and serves stale data on upstream
failure rather than showing an error for a leaderboard that was fine ten
seconds ago.

`src/lib/botApi.ts` is the typed client. No business logic lives on the
website — no rating maths, no point totals, no streak calculation. The bot
computes; the site displays.

### Pages

| Page | Access | Source |
|---|---|---|
| Daily Problems | public | bot |
| Leaderboards | public | bot |
| Community | public | bot |
| Profile | public | bot |
| Home | public | site API |
| Blitz & Duel, Feed, Session Ranking | members | site API |

Leaderboards are never merged — seven separate boards, each with its own
search and pagination, driven by `BOARDS` in `site.ts`.

### Responsiveness

- Mobile drawer with scroll lock, Escape-to-close, and close-on-navigate.
- Filter rails scroll horizontally on small screens instead of wrapping into a
  tall stack that pushes content off-screen.
- Leaderboard rows collapse from a four-column grid to a two-column card with
  values inline, so nothing is clipped at 320px.
- `overflow-x: hidden` and `max-width: 100%` on `html, body` as a global guard.
- Headings use `clamp()` rather than fixed breakpoint jumps.
- `100dvh` instead of `100vh`, so mobile browser chrome does not cause a jump.
- `prefers-reduced-motion` disables the new animations and the panel lift.

### Performance

Route-level `React.lazy` on every page except Home. Main bundle dropped from
785 kB to 695 kB; the blitz arena (29 kB) and community feed (24 kB) now load
only when opened.

---

## Verified

- `npx tsc -p tsconfig.app.json --noEmit` — clean
- `npx tsc --noEmit` in `server/` — clean
- `npm run build` — succeeds
- `api_server.py` builds and registers all 12 routes

Not verified, because it needs your live credentials: the OAuth round trip and
the bot API against real data.

---

## Not in this phase

Curated problem sets, roadmaps, arenas, matchmaking, the Monaco submit-and-
verify loop, the admin dashboard, and badges/XP. The routing, gating and data
layer here are what those hang off — each becomes a page file plus, where the
bot doesn't already own the data, a new read route in `api_server.py`.

One cleanup worth doing early: `server/src/routes/auth.ts` still serves
email/password login, and `server/src/auth.ts` stores passwords in plain text
(`hashPassword` returns its input unchanged). With Discord OAuth live, delete
`/register` and `/login` — two parallel identity systems means the member gate
can be sidestepped by the weaker one.
