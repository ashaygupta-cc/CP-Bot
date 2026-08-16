"""
cogs/duels.py — Professional 1v1 Duel system with Codeforces-style ratings.

TWO DISTINCT MATCH FORMATS
──────────────────────────
BLITZ (cp_blitz / dsa_blitz / icpc_blitz) — "fast fingers", shared problems:
  • Problems are played ONE AT A TIME, shared by both players.
  • Each problem has its own timer, scaling with difficulty ordering:
      3-problem format → 15 min / 25 min / 35 min (easy → medium → hard)
      2-problem format → 25 min / 25 min (medium + medium)
    Same rule for CF and LC blitz alike.
  • First verified solver takes the problem; it ends for BOTH players.
  • Timer expires with no solve → problem is a draw → next problem.
  • Most problems won takes the match.

DUEL (cp_duel / dsa_duel / icpc_duel) — ICPC-contest style, independent progress:
  • ONE total timer for the whole match: 20 min × number of problems
    (2 problems → 40 min, 3 problems → 60 min).
  • Both players start on Problem 1. Each player unlocks Problem k+1 ONLY
    after their own verified solve of Problem k — delivered privately
    (ephemeral); the opponent cannot see it.
  • If a player solves ALL problems, the match ends immediately.
  • When time expires (or early finish): score = solve count.
    Tie on count → lower total solve time (sum from match start) wins.
    0–0, or identical count AND time → draw.
  • Ratings: Elo on rating difference (beating a stronger player pays more).

PROBLEM DIFFICULTY TARGETS  (base = max(800, avg-of-ratings ceiled to 100))
  CF  normal: 3 → [base−100, base+100, base+200]   2 → [base, base+100]
  ICPC      : 3 → [base, base+100, base+300]       2 → [base+100, base+200]
  DSA (LC)  : 3 → [Easy, Medium, Hard]             2 → [Medium, Medium]
  CF problems display their actual RATING; Easy/Medium/Hard labels are LC-only.

CHANNEL VISIBILITY
  Everyone can SEE that a match channel exists (ongoing matches are public),
  but only the two players (+ bot) can read history or send messages.
"""

import asyncio
import math
import time
import discord
import aiohttp
from datetime import datetime, timedelta, timezone
from discord.ext import commands, tasks

import config
import duel_ranks
from database.connection import get_pool
from database import queries as q
from database import duel_queries as dq
import platforms as P
from platforms.codeforces import CFBlockedError
from platforms.duel_cf_pool import pick_cf_problems_at_ratings
from platforms.duel_lc_pool import pick_sequence
import duel_bot_engine as botengine

# ── Colors & constants ────────────────────────────────────────────────────
COLOR_SUCCESS = getattr(config, "COLOR_SUCCESS", 0x57F287)
COLOR_ERROR   = getattr(config, "COLOR_ERROR", 0xED4245)
COLOR_INFO    = getattr(config, "COLOR_INFO", 0x5865F2)
COLOR_WARN    = getattr(config, "COLOR_WARN", 0xFEE75C)
COLOR_CYAN    = getattr(config, "COLOR_CYAN", 0x00D9FF)
ADMIN_ROLE    = getattr(config, "ADMIN_ROLE", "Admin")

# ── Branding (matches logo: dark navy + cyan) ────────────────────────────
BOT_LOGO   = "https://raw.githubusercontent.com/ashaygupta-cc/ashaygupta-cc/main/Binary%20Beats.webp"
BOT_BANNER = "https://raw.githubusercontent.com/ashaygupta-cc/ashaygupta-cc/main/Binary%20Beats%20Banner.jpeg"
BRAND      = "Binary Beats"
WEBHOOK_NAME = "Z4s"

# Semantic palette — use these instead of raw hex in embeds
CLR_MATCH   = 0x00D9FF   # cyan — active, challenges, info
CLR_WIN     = 0x57F287   # green — accepted, success
CLR_LOSS    = 0xED4245   # red — errors, forfeit, declined
CLR_RESULT  = 0xFEE75C   # gold — match results / trophy
CLR_NEUTRAL = 0x2F3136   # dark — expired, neutral


def _brand(title: str, desc: str = None, color: int = CLR_MATCH,
           *, thumb: bool = True, banner: bool = False) -> discord.Embed:
    """Create a consistently-branded embed."""
    em = discord.Embed(title=title, description=desc, color=color)
    em.set_author(name=BRAND, icon_url=BOT_LOGO)
    if thumb:
        em.set_thumbnail(url=BOT_LOGO)
    if banner:
        em.set_image(url=BOT_BANNER)
    return em

IST = timezone(timedelta(hours=5, minutes=30))

MODES = {
    "cp_blitz":   {"platform": "cf", "family": "cp",   "icpc": False, "label": "CF Blitz"},
    "cp_duel":    {"platform": "cf", "family": "cp",   "icpc": False, "label": "CF Duel"},
    "dsa_blitz":  {"platform": "lc", "family": "dsa",  "icpc": False, "label": "LC Blitz"},
    "dsa_duel":   {"platform": "lc", "family": "dsa",  "icpc": False, "label": "LC Duel"},
    "icpc_blitz": {"platform": "cf", "family": "icpc", "icpc": True,  "label": "ICPC Blitz"},
    "icpc_duel":  {"platform": "cf", "family": "icpc", "icpc": True,  "label": "ICPC Duel"},
}

MODE_STATS_CHANNELS = {
    "cp_blitz":   ["cp-blitz", "cp-duels"],
    "cp_duel":    ["cp-duels", "cp-blitz"],
    "dsa_blitz":  ["dsa-blitz", "dsa-duels"],
    "dsa_duel":   ["dsa-duels", "dsa-blitz"],
    "icpc_blitz": ["icpc-blitz", "icpc-duels"],
    "icpc_duel":  ["icpc-duels", "icpc-blitz"],
}

# Per-mode headline used in the match embed. "ICPC-style" wording appears
# ONLY in the two ICPC modes — CF and LC matches describe themselves
# accurately instead of borrowing ICPC branding.
MODE_BLURB = {
    "cp_duel":    "Codeforces marathon",
    "dsa_duel":   "LeetCode marathon",
    "icpc_duel":  "ICPC-style contest",
    "cp_blitz":   "Codeforces speed race",
    "dsa_blitz":  "LeetCode speed race",
    "icpc_blitz": "ICPC-style speed race",
}

FORFEIT_PENALTY = 32
FORFEIT_REWARD  = 16
DUEL_SECS_PER_PROBLEM = 1200   # duel total time = n × 20 min

# Blitz per-problem timer scales with difficulty ordering, same rule for
# CF and LC blitz alike (position in the sequence = difficulty rank):
#   3-problem format → 15 min / 25 min / 35 min (easy → medium → hard)
#   2-problem format → 25 min / 25 min (medium + medium)
BLITZ_SECS_3 = {1: 15 * 60, 2: 25 * 60, 3: 35 * 60}
BLITZ_SECS_2 = {1: 25 * 60, 2: 25 * 60}


def _blitz_secs_for(idx: int, total: int) -> int:
    """Per-problem blitz time limit based on the problem's position/difficulty rank."""
    table = BLITZ_SECS_3 if total >= 3 else BLITZ_SECS_2
    return table.get(idx, table[max(table)])

CHECK_COOLDOWN_SEC = 20
_last_check: dict[tuple, float] = {}
_duel_locks: dict[str, asyncio.Lock] = {}
# Per-duel lock so a blitz problem can never be resolved twice concurrently
# (e.g. both players clicking Check in the same second, or a Check racing
# the auto_check expiry sweep). Lock is always taken BEFORE acquiring a DB
# connection, in every caller, so lock→conn ordering is consistent.
_resolve_locks: dict[int, asyncio.Lock] = {}


def is_admin():
    async def predicate(ctx):
        return (
            any(r.name == ADMIN_ROLE for r in ctx.author.roles)
            or ctx.author.guild_permissions.administrator
        )
    return commands.check(predicate)


def _is_duel_mode(mode: str) -> bool:
    return mode.endswith("_duel")


def _fmt_secs(s: float) -> str:
    s = max(0, int(s))
    m, sec = divmod(s, 60)
    return f"{m}m {sec:02d}s" if m else f"{sec}s"


def _aware(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _ist_now_str() -> str:
    return datetime.now(IST).strftime("%H:%M:%S IST")


def _base_rating(p1_rating: int, p2_rating: int) -> int:
    """base = max(800, average ceiled to the nearest 100)."""
    avg = (p1_rating + p2_rating) / 2
    return max(800, int(math.ceil(avg / 100.0) * 100))


def _cf_targets(base: int, n: int, icpc: bool) -> list[int]:
    """Target problem ratings per spec."""
    if icpc:
        if n >= 3:
            t = [base, base + 100, base + 300]
        elif n == 2:
            t = [base + 100, base + 200]
        else:
            t = [base + 100]
    else:
        if n >= 3:
            t = [max(800, base - 100), base + 100, base + 200]
        elif n == 2:
            t = [base, base + 100]
        else:
            t = [base + 100]
    return [min(3500, max(800, x)) for x in t]


def _elo_delta(my_rating: int, opp_rating: int, score: float, k: int) -> int:
    expected = 1.0 / (1.0 + 10 ** ((opp_rating - my_rating) / 400.0))
    return round(k * (score - expected))


def _problem_difficulty_display(prob: dict) -> str:
    """CF → rating number; LC → Easy/Medium/Hard."""
    if prob["platform"] == "cf":
        return str(prob.get("rating") or prob.get("difficulty") or "?")
    return (prob.get("difficulty") or "Medium").title()


# ══════════════════════════════════════════════════════════════════════════
#  Views
# ══════════════════════════════════════════════════════════════════════════

class ChallengeView(discord.ui.View):
    def __init__(self, cog, ctx, challenger, opponent, mode, timeout_s, format_num=3):
        super().__init__(timeout=timeout_s)
        self.cog, self.ctx = cog, ctx
        self.challenger, self.opponent, self.mode = challenger, opponent, mode
        self.format_num = format_num
        self.responded = False
        self.message: discord.Message | None = None

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message("Only the challenged player can respond.", ephemeral=True)
            return
        self.responded = True
        self.stop()
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(content="✅ **Accepted** — setting up the arena…", view=self)
        await self.cog.begin_human_match(self.ctx, self.challenger, self.opponent, self.mode, self.format_num)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.secondary)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message("Only the challenged player can respond.", ephemeral=True)
            return
        self.responded = True
        self.stop()
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(content="Challenge declined.", view=self)
        await self.cog.offer_bot_fallback(interaction.channel, self.challenger, self.mode)

    async def on_timeout(self):
        if self.responded:
            return
        for c in self.children:
            c.disabled = True
        try:
            if self.message:
                await self.message.edit(content="Challenge expired — no response.", view=self)
        except Exception:
            pass
        channel = self.message.channel if self.message else self.ctx.channel
        await self.cog.offer_bot_fallback(channel, self.challenger, self.mode)


class SubmissionCheckerView(discord.ui.View):
    def __init__(self, cog, duel_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.duel_id = duel_id

    @discord.ui.button(label="Check Submissions", style=discord.ButtonStyle.primary)
    async def check(self, interaction: discord.Interaction, button: discord.ui.Button):
        print(f"[CHECK] {interaction.user.name} → duel {self.duel_id}", flush=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            key = (self.duel_id, interaction.user.id)
            if time.monotonic() - _last_check.get(key, 0) < CHECK_COOLDOWN_SEC:
                await interaction.followup.send(
                    f"Checked recently — try again in {CHECK_COOLDOWN_SEC}s.", ephemeral=True)
                return
            _last_check[key] = time.monotonic()

            pool = get_pool()
            async with pool.acquire() as conn:
                duel = await dq.get_duel(conn, self.duel_id)
            if not duel or duel["status"] != "active":
                await interaction.followup.send("This duel is no longer active.", ephemeral=True)
                return
            duel = dict(duel)

            if _is_duel_mode(duel["mode"]):
                await self.cog.duel_check(interaction, duel)
            else:
                await self.cog.blitz_check(interaction, duel)
        except Exception as e:
            print(f"[CHECK] ❌ {type(e).__name__}: {e}", flush=True)
            import traceback; traceback.print_exc()
            try:
                await interaction.followup.send(f"Check failed: {e}", ephemeral=True)
            except Exception:
                pass


class ForfeitView(discord.ui.View):
    def __init__(self, cog, duel_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.duel_id = duel_id

    @discord.ui.button(label="Forfeit Match", style=discord.ButtonStyle.secondary)
    async def forfeit(self, interaction: discord.Interaction, button: discord.ui.Button):
        print(f"[FORFEIT] {interaction.user.name} → duel {self.duel_id}", flush=True)
        await interaction.response.defer(ephemeral=True)
        try:
            await self.cog.handle_forfeit(interaction, self.duel_id)
        except Exception as e:
            print(f"[FORFEIT] ❌ {type(e).__name__}: {e}", flush=True)
            import traceback; traceback.print_exc()
            try:
                await interaction.followup.send(f"Forfeit failed: {e}", ephemeral=True)
            except Exception:
                pass


class MatchControlView(discord.ui.View):
    """Combined Check + Forfeit — single row, clean layout."""
    def __init__(self, cog, duel_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.duel_id = duel_id

    @discord.ui.button(label="Check Submissions", style=discord.ButtonStyle.primary)
    async def check(self, interaction: discord.Interaction, button: discord.ui.Button):
        print(f"[CHECK] {interaction.user.name} → duel {self.duel_id}", flush=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            key = (self.duel_id, interaction.user.id)
            if time.monotonic() - _last_check.get(key, 0) < CHECK_COOLDOWN_SEC:
                await interaction.followup.send(
                    f"Checked recently — try again in {CHECK_COOLDOWN_SEC}s.", ephemeral=True)
                return
            _last_check[key] = time.monotonic()

            pool = get_pool()
            async with pool.acquire() as conn:
                duel = await dq.get_duel(conn, self.duel_id)
            if not duel or duel["status"] != "active":
                await interaction.followup.send("This duel is no longer active.", ephemeral=True)
                return
            duel = dict(duel)

            if _is_duel_mode(duel["mode"]):
                await self.cog.duel_check(interaction, duel)
            else:
                await self.cog.blitz_check(interaction, duel)
        except Exception as e:
            print(f"[CHECK] ❌ {type(e).__name__}: {e}", flush=True)
            import traceback; traceback.print_exc()
            try:
                await interaction.followup.send(f"Check failed: {e}", ephemeral=True)
            except Exception:
                pass

    @discord.ui.button(label="Forfeit Match", style=discord.ButtonStyle.secondary)
    async def forfeit(self, interaction: discord.Interaction, button: discord.ui.Button):
        print(f"[FORFEIT] {interaction.user.name} → duel {self.duel_id}", flush=True)
        await interaction.response.defer(ephemeral=True)
        try:
            await self.cog.handle_forfeit(interaction, self.duel_id)
        except Exception as e:
            print(f"[FORFEIT] ❌ {type(e).__name__}: {e}", flush=True)
            import traceback; traceback.print_exc()
            try:
                await interaction.followup.send(f"Forfeit failed: {e}", ephemeral=True)
            except Exception:
                pass


class BotFallbackView(discord.ui.View):
    def __init__(self, cog, challenger, mode, timeout_s: int = 180):
        super().__init__(timeout=timeout_s)
        self.cog, self.challenger, self.mode = cog, challenger, mode

    @discord.ui.button(label="Play vs Bot", style=discord.ButtonStyle.primary)
    async def play_bot(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.challenger.id:
            await interaction.response.send_message("Only the challenger can use this.", ephemeral=True)
            return
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(view=self)
        await self.cog.begin_bot_match(interaction, self.challenger, None, self.mode)


# ══════════════════════════════════════════════════════════════════════════
#  Main Cog
# ══════════════════════════════════════════════════════════════════════════

class Duels(commands.Cog):
    """1v1 Duel system with Codeforces, LeetCode, and ICPC modes."""

    def __init__(self, bot):
        self.bot = bot
        self._bot_solve_tasks: dict[int, asyncio.Task] = {}
        self._finalizing: set[int] = set()
        # Duels whose _finish_match has already begun — a match must never
        # be "finished" twice (double stats post, double rating apply).
        self._finished: set[int] = set()
        self._webhook_cache: dict[int, discord.Webhook] = {}
        self._live_cards: dict[int, discord.Message] = {}   # duel_id → jump-link msg
        self.auto_check.start()

    def cog_unload(self):
        self.auto_check.cancel()
        for t in self._bot_solve_tasks.values():
            t.cancel()

    # ── Webhook helpers ────────────────────────────────────────────────────

    async def _get_webhook(self, channel) -> discord.Webhook | None:
        """Get or create a branded webhook for the channel."""
        if channel.id in self._webhook_cache:
            return self._webhook_cache[channel.id]
        try:
            webhooks = await channel.webhooks()
            for wh in webhooks:
                if wh.name == WEBHOOK_NAME:
                    self._webhook_cache[channel.id] = wh
                    return wh
            wh = await channel.create_webhook(name=WEBHOOK_NAME)
            self._webhook_cache[channel.id] = wh
            return wh
        except Exception as e:
            print(f"[WEBHOOK] Failed to get/create: {e}", flush=True)
            return None

    async def _send_branded(self, channel, embed=None, content=None, **kwargs):
        """Send via webhook for premium branding (custom name + avatar)."""
        wh = await self._get_webhook(channel)
        if wh:
            try:
                return await wh.send(
                    content=content, embed=embed,
                    username=WEBHOOK_NAME, avatar_url=BOT_LOGO,
                    wait=True, **kwargs)
            except Exception as e:
                print(f"[WEBHOOK] Send failed, fallback: {e}", flush=True)
        return await channel.send(content=content, embed=embed, **kwargs)

    # ── ASCII art countdown digits ──────────────────────────────────────
    _CD_ART = {
        7: "███████\n"
           "     ██\n"
           "    ██\n"
           "   ██\n"
           "  ██",
        6: " █████\n"
           "██\n"
           "██████\n"
           "██  ██\n"
           " █████",
        5: "██████\n"
           "██\n"
           "█████\n"
           "    ██\n"
           "█████",
        4: "██  ██\n"
           "██  ██\n"
           "██████\n"
           "    ██\n"
           "    ██",
        3: "█████\n"
           "   ██\n"
           " ████\n"
           "   ██\n"
           "█████",
        2: " █████\n"
           "    ██\n"
           " ████\n"
           "██\n"
           "██████",
        1: "  ██\n"
           " ███\n"
           "  ██\n"
           "  ██\n"
           "██████",
    }
    _CD_GO = " ████  ████  ██\n" \
             "██    ██  ██ ██\n" \
             "██ ██ ██  ██ ██\n" \
             "██  █ ██  ██\n" \
             " ████  ████  ██"

    def _cd_frame(self, n: int) -> str:
        art = self._CD_ART.get(n, "") if n > 0 else self._CD_GO
        lines = art.split("\n")
        w = max(len(l) for l in lines)
        pad = max(w + 6, 18)
        centered = "\n".join(l.center(pad) for l in lines)
        return f"```ansi\n\u001b[1;36m{centered}\u001b[0m\n```"

    async def _run_countdown(self, channel, seconds: int = 7):
        """Animated ASCII art countdown above the match card."""
        try:
            msg = await channel.send(self._cd_frame(seconds))
            return msg
        except Exception:
            return None

    async def _animate_countdown(self, msg, seconds: int = 7):
        """Edit the countdown message 6→1→GO! then delete."""
        if not msg:
            return
        try:
            for i in range(seconds - 1, 0, -1):
                await asyncio.sleep(1)
                await msg.edit(content=self._cd_frame(i))
            await asyncio.sleep(1)
            await msg.edit(content=self._cd_frame(0))
            await asyncio.sleep(2)
            await msg.delete()
        except Exception:
            pass

    # ── Commands ───────────────────────────────────────────────────────────

    @commands.command(name="duel", help="Challenge a player or bot to a duel.")
    async def duel_cmd(self, ctx, opponent_str: str, mode: str,
                       format_or_rating: str = None, rating_str: str = None):
        """
        !duel @user cp_blitz              Challenge a player (3-problem)
        !duel @user cp_duel 2             Challenge a player (2-problem)
        !duel bot cp_blitz                Bot at your rating (3 problems)
        !duel bot cp_duel 2 1600          Bot, 2 problems, rating 1600
        """
        print(f"[DUEL_CMD] opponent={opponent_str} mode={mode} a3={format_or_rating} a4={rating_str}", flush=True)
        # Delete the command message for a clean channel
        try:
            await ctx.message.delete()
        except Exception:
            pass
        mode = mode.lower()
        if mode not in MODES:
            await ctx.send(f"Unknown mode. Available: {', '.join(MODES.keys())}")
            return

        format_num = 3
        bot_rating_override = None
        if format_or_rating:
            if format_or_rating in ("2", "3"):
                format_num = int(format_or_rating)
                bot_rating_override = rating_str
            else:
                bot_rating_override = format_or_rating

        pool = get_pool()
        async with pool.acquire() as conn:
            cfg = await dq.get_duel_config(conn, str(ctx.guild.id))
            author_busy = await self._active_duel_for_user(conn, str(ctx.guild.id), str(ctx.author.id))
        if author_busy:
            await ctx.send(await self._busy_message(author_busy, "You"))
            return

        if opponent_str.lower() == "bot":
            fallback_rating = await self._get_or_create_duel_rating(ctx.author, ctx.guild, mode)
            bot_rating = botengine.resolve_bot_rating(bot_rating_override, fallback_rating["rating"])
            await self.begin_bot_match(ctx, ctx.author, None, mode, bot_rating, format_num)
        else:
            try:
                opponent = await commands.MemberConverter().convert(ctx, opponent_str)
            except commands.BadArgument:
                await ctx.send(f"Couldn't find player {opponent_str}.")
                return
            if opponent.id == ctx.author.id:
                await ctx.send("You can't duel yourself.")
                return
            if opponent.bot:
                await ctx.send("To challenge the bot, use `!duel bot <mode>`.")
                return

            async with pool.acquire() as conn:
                opp_busy = await self._active_duel_for_user(conn, str(ctx.guild.id), str(opponent.id))
            if opp_busy:
                await ctx.send(await self._busy_message(opp_busy, opponent.mention))
                return

            challenge_timeout = int(cfg.get("challenge_timeout", 90))

            # ── Fetch ratings for display ──
            p1r = await self._get_or_create_duel_rating(ctx.author, ctx.guild, mode)
            p2r = await self._get_or_create_duel_rating(opponent, ctx.guild, mode)
            p1_rank = duel_ranks.get_rank(p1r["rating"])
            p2_rank = duel_ranks.get_rank(p2r["rating"])

            em = _brand("__Duel Challenge__", color=CLR_MATCH, banner=True)

            if _is_duel_mode(mode):
                total_min = format_num * DUEL_SECS_PER_PROBLEM // 60
                fmt_desc = (
                    f"{MODE_BLURB[mode]} · **{total_min} min** total\n"
                    f"Solve in order — next problem unlocks\n"
                    f"when you finish the current one.")
            else:
                fmt_desc = (
                    f"{MODE_BLURB[mode]} · one problem at a time\n"
                    f"First verified solve takes each problem.")

            em.description = (
                f"**{MODES[mode]['label']}** · **{format_num}**-Problem Format\n\n"
                f"{fmt_desc}")

            em.add_field(
                name="__Challenger__",
                value=f"{ctx.author.mention}\n**{p1_rank['name']}** · `{p1r['rating']}`",
                inline=True)
            em.add_field(
                name="__Opponent__",
                value=f"{opponent.mention}\n**{p2_rank['name']}** · `{p2r['rating']}`",
                inline=True)
            em.set_footer(
                text=f"Only {opponent.name} can accept/decline · Expires in {challenge_timeout}s",
                icon_url=BOT_LOGO)

            view = ChallengeView(self, ctx, ctx.author, opponent, mode, challenge_timeout, format_num)
            view.message = await ctx.send(embed=em, view=view)

    @commands.command(name="duelprofile", help="View your or someone's duel ratings.")
    async def duel_profile(self, ctx, user: discord.User = None):
        target = user or ctx.author
        pool = get_pool()
        async with pool.acquire() as conn:
            profile = await dq.get_profile(conn, str(target.id), str(ctx.guild.id))
        if not profile:
            await ctx.send(f"{target.name} hasn't participated in any duels yet.")
            return
        em = _brand(f"__{target.name}__", color=CLR_MATCH, thumb=False)
        em.description = "Ratings and records across all modes\n"
        em.timestamp = datetime.now(timezone.utc)
        for row in profile:
            rating = row["rating"]
            rank_info = duel_ranks.get_rank(rating)
            w, l, d = row["wins"], row["losses"], row["draws"]
            record = f"`{w}W` `{l}L` `{d}D`" if (w + l + d) > 0 else "No matches"
            field_value = f"**{rank_info['name']}** · `{rating}`\n{record}"
            if row["streak"] != 0:
                streak_type = "Win" if row["streak"] > 0 else "Loss"
                field_value += f"\n{streak_type} streak ×{abs(row['streak'])}"
            em.add_field(name=f"__{MODES[row['mode']]['label']}__", value=field_value, inline=True)
        em.set_footer(text=BRAND, icon_url=BOT_LOGO)
        await ctx.send(embed=em)

    @commands.command(name="duelrank", help="Check your rank/tier in a mode.")
    async def duel_rank(self, ctx, mode: str = None):
        if mode:
            mode = mode.lower()
            if mode not in MODES:
                await ctx.send(f"Invalid mode: {mode}")
                return
            modes_to_check = [mode]
        else:
            modes_to_check = list(MODES.keys())
        pool = get_pool()
        async with pool.acquire() as conn:
            profile = await dq.get_profile(conn, str(ctx.author.id), str(ctx.guild.id))
        em = _brand(f"__{ctx.author.name}__", color=CLR_MATCH, thumb=False)
        em.description = "Current ranks"
        em.timestamp = datetime.now(timezone.utc)
        for row in profile:
            if row["mode"] not in modes_to_check:
                continue
            rank_info = duel_ranks.get_rank(row["rating"])
            em.add_field(name=f"__{MODES[row['mode']]['label']}__",
                         value=f"**{rank_info['name']}** · `{row['rating']}`", inline=True)
        em.set_footer(text=BRAND, icon_url=BOT_LOGO)
        await ctx.send(embed=em)

    @commands.command(name="duelleaderboard", help="Top duel players in a mode.")
    async def duel_leaderboard(self, ctx, mode: str = "cp_duel"):
        mode = mode.lower()
        if mode not in MODES:
            await ctx.send(f"Invalid mode: {mode}")
            return
        pool = get_pool()
        async with pool.acquire() as conn:
            rows = await dq.get_leaderboard(conn, str(ctx.guild.id), mode, limit=10)
        em = _brand(f"__Leaderboard — {MODES[mode]['label']}__", color=CLR_MATCH, thumb=False)
        em.timestamp = datetime.now(timezone.utc)
        if not rows:
            em.description = "No players yet."
            await ctx.send(embed=em)
            return
        lines = []
        for i, row in enumerate(rows, 1):
            user = self.bot.get_user(int(row["discord_id"])) if row["discord_id"].isdigit() else None
            name = user.display_name if user else f"User {row['discord_id']}"
            rank_info = duel_ranks.get_rank(row["rating"])
            record = f"`{row['wins']}W` `{row['losses']}L` `{row['draws']}D`"
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"`{i}.`")
            lines.append(f"{medal} **{name}** — {rank_info['name']} · `{row['rating']}`\n  {record}")
        em.description = "\n\n".join(lines)
        em.set_footer(text=BRAND, icon_url=BOT_LOGO)
        await ctx.send(embed=em)

    # ── Bot fallback ─────────────────────────────────────────────────────

    async def offer_bot_fallback(self, channel, challenger, mode: str):
        """Offer the challenger a bot match when the human opponent declined/timed out."""
        em = _brand(
            "__No opponent?__",
            desc=f"Challenge a bot instead — same mode, same rating system.",
            color=CLR_NEUTRAL, thumb=False)
        em.set_footer(text="Bot matches still affect your rating.", icon_url=BOT_LOGO)
        try:
            await channel.send(embed=em, view=BotFallbackView(self, challenger, mode))
        except Exception:
            pass

    # ── Match setup ────────────────────────────────────────────────────────

    async def _active_duel_for_user(self, conn, guild_id: str, user_id: str) -> dict | None:
        """
        Returns the user's currently-active duel in this guild, or None.
        A user can't start/accept a new match while one of theirs is still
        active — active ends only via forfeit, time-expiry finalize, or a
        player solving everything. Filtered client-side since
        get_all_active_duels() returns active duels across all guilds
        (same pattern already used by the auto_check background loop).
        """
        duels = await dq.get_all_active_duels(conn)
        for d in duels:
            d = dict(d)
            if str(d.get("guild_id")) != str(guild_id):
                continue
            if d.get("player1_id") == user_id or d.get("player2_id") == user_id:
                return d
        return None

    async def _busy_message(self, duel: dict, who) -> str:
        ch = self.bot.get_channel(int(duel["channel_id"])) if duel.get("channel_id") else None
        where = ch.mention if ch else f"match #{duel.get('duel_number') or '?'}"
        return f"❌ {who} already has an active match in {where} — it must finish first."

    async def _get_or_create_duel_rating(self, user, guild, mode: str):
        pool = get_pool()
        async with pool.acquire() as conn:
            return await dq.get_or_create_rating(conn, str(user.id), str(guild.id), mode)

    async def _void_failed_setup(self, duel_id: int, channel):
        """
        Setup crashed after the duel row was created (problem fetch failed,
        channel creation failed, etc). Void the row so the players aren't
        blocked by the one-match-at-a-time check, and remove the orphan
        room where no match ever started. No ratings are touched.
        """
        try:
            pool = get_pool()
            async with pool.acquire() as conn:
                await dq.finish_duel(conn, duel_id, None)
            print(f"[MATCH] 🧹 voided failed-setup duel {duel_id}", flush=True)
        except Exception as e:
            print(f"[MATCH] ⚠️ could not void duel {duel_id}: {e}", flush=True)
        if channel is not None:
            try:
                await channel.delete(reason="Duel setup failed — cleaning up")
                print(f"[MATCH] 🧹 deleted orphan channel {channel.name}", flush=True)
            except Exception as e:
                print(f"[MATCH] ⚠️ could not delete orphan channel: {e}", flush=True)

    async def begin_human_match(self, ctx, challenger, opponent, mode: str, format_num: int = 3):
        try:
            print(f"[MATCH] human: {challenger.name} vs {opponent.name} mode={mode} n={format_num}", flush=True)
            pool = get_pool()
            guild_id = str(ctx.guild.id)
            lock = _duel_locks.setdefault(guild_id, asyncio.Lock())

            async with pool.acquire() as conn:
                cfg = await dq.get_duel_config(conn, guild_id)
                p1_rating = await dq.get_or_create_rating(conn, str(challenger.id), guild_id, mode)
                p2_rating = await dq.get_or_create_rating(conn, str(opponent.id), guild_id, mode)

            async with lock:
                async with pool.acquire() as conn:
                    # Re-check right before creation — the challenge could've
                    # sat for up to challenge_timeout seconds, during which
                    # either player might have joined another active match.
                    busy = await self._active_duel_for_user(conn, guild_id, str(challenger.id))
                    if not busy:
                        busy = await self._active_duel_for_user(conn, guild_id, str(opponent.id))
                        busy_who = opponent.mention if busy else None
                    else:
                        busy_who = "You"
                    if busy:
                        await ctx.channel.send(await self._busy_message(busy, busy_who))
                        return

                    used = await dq.get_active_duel_numbers(conn, guild_id)
                    duel_num = dq.next_free_number(used)
                    duel_id = await dq.create_duel(
                        conn, guild_id, mode, str(challenger.id), str(opponent.id),
                        is_bot_match=False, bot_rating=None,
                        total_games=format_num, duel_number=duel_num)

            channel = None
            try:
                channel = await self._create_duel_channel(ctx, challenger, opponent, mode, duel_num)

                async with pool.acquire() as conn:
                    await dq.activate_duel(conn, duel_id, str(channel.id))
                    await self._setup_problems(conn, duel_id, mode,
                                               p1_rating["rating"], p2_rating["rating"],
                                               cfg, format_num=format_num)
            except Exception:
                # CRITICAL: if channel creation or problem setup fails, the
                # duel row must NOT stay 'pending'/'active' — that would
                # permanently block both players (one-match-at-a-time check)
                # and leave an orphan room where no match ever started.
                await self._void_failed_setup(duel_id, channel)
                raise
            # ── Jump-link back to the original channel ──
            try:
                jump = _brand("__Match room is live.__", color=CLR_WIN, thumb=False)
                jump.description = f"→ {channel.mention}"
                jump.set_footer(text="Tap the channel to jump straight in.", icon_url=BOT_LOGO)
                jump_msg = await self._send_branded(ctx.channel, embed=jump)
                if jump_msg:
                    self._live_cards[duel_id] = jump_msg
            except Exception:
                pass

            if _is_duel_mode(mode):
                await self._start_duel_match(channel, duel_id, mode, cfg)
            else:
                await self._start_blitz_problem(channel, duel_id, mode, cfg)
        except Exception as e:
            print(f"[MATCH] ❌ human setup failed: {type(e).__name__}: {e}", flush=True)
            import traceback; traceback.print_exc()
            try:
                await ctx.channel.send(f"❌ Match setup failed: {e}")
            except Exception:
                pass

    async def begin_bot_match(self, ctx, challenger, opponent, mode: str,
                              bot_rating: int = None, format_num: int = 3):
        try:
            print(f"[MATCH] bot: {challenger.name} mode={mode} bot={bot_rating} n={format_num}", flush=True)
            pool = get_pool()
            guild = ctx.guild
            guild_id = str(guild.id)
            lock = _duel_locks.setdefault(guild_id, asyncio.Lock())

            async with pool.acquire() as conn:
                cfg = await dq.get_duel_config(conn, guild_id)
                p1_rating = await dq.get_or_create_rating(conn, str(challenger.id), guild_id, mode)

            if bot_rating is None:
                bot_rating = p1_rating["rating"]

            async with lock:
                async with pool.acquire() as conn:
                    busy = await self._active_duel_for_user(conn, guild_id, str(challenger.id))
                    if busy:
                        await ctx.channel.send(await self._busy_message(busy, "You"))
                        return

                    used = await dq.get_active_duel_numbers(conn, guild_id)
                    duel_num = dq.next_free_number(used)
                    duel_id = await dq.create_duel(
                        conn, guild_id, mode, str(challenger.id), None,
                        is_bot_match=True, bot_rating=bot_rating,
                        total_games=format_num, duel_number=duel_num)

            channel = None
            try:
                channel = await self._create_duel_channel(ctx, challenger, None, mode, duel_num, is_bot=True)

                async with pool.acquire() as conn:
                    await dq.activate_duel(conn, duel_id, str(channel.id))
                    await self._setup_problems(conn, duel_id, mode,
                                               p1_rating["rating"], bot_rating, cfg,
                                               is_bot=True, format_num=format_num)
            except Exception:
                # Same cleanup as human matches: never leave a stale
                # 'pending'/'active' duel row + orphan room behind.
                await self._void_failed_setup(duel_id, channel)
                raise
            # ── Jump-link back to the original channel ──
            try:
                jump = _brand("__Match room is live.__", color=CLR_WIN, thumb=False)
                jump.description = f"→ {channel.mention}"
                jump.set_footer(text="Tap the channel to jump straight in.", icon_url=BOT_LOGO)
                jump_msg = await self._send_branded(ctx.channel, embed=jump)
                if jump_msg:
                    self._live_cards[duel_id] = jump_msg
            except Exception:
                pass

            if _is_duel_mode(mode):
                await self._start_duel_match(channel, duel_id, mode, cfg, is_bot=True)
            else:
                await self._start_blitz_problem(channel, duel_id, mode, cfg, is_bot=True)
            print(f"[MATCH] ✅ started duel_id={duel_id} #{duel_num}", flush=True)
        except Exception as e:
            print(f"[MATCH] ❌ bot setup failed: {type(e).__name__}: {e}", flush=True)
            import traceback; traceback.print_exc()
            try:
                await ctx.channel.send(f"❌ Bot match setup failed: {e}")
            except Exception:
                pass

    async def _create_duel_channel(self, ctx, player1, player2, mode: str,
                                   duel_number: int, is_bot: bool = False) -> discord.TextChannel:
        family = MODES[mode]["family"]
        kind = "duel" if _is_duel_mode(mode) else "blitz"   # room name matches the actual format
        if is_bot:
            channel_name = f"{family}-{kind}-{duel_number}-{player1.name}-vs-bot"
        else:
            channel_name = f"{family}-{kind}-{duel_number}-{player1.name}-vs-{player2.name}"
        channel_name = channel_name.lower().replace(" ", "-")[:100]

        guild = ctx.guild
        category = discord.utils.get(guild.categories, name="Duels")
        if not category:
            category = await guild.create_category("Duels")
            print("[DUEL] Created 'Duels' category", flush=True)

        p1 = guild.get_member(player1.id) or player1
        p2 = guild.get_member(player2.id) if player2 else None

        # Everyone can SEE the ongoing match channel exists, but cannot read
        # history or send messages. Only the two players (+ bot) have access.
        # manage_channels/manage_messages are explicitly DENIED for players
        # and @everyone — this is a channel-level override, so it holds even
        # if a player's server role would otherwise grant Manage Channels.
        # Only genuine admins/mods (guild-wide permission, outside this
        # override) or the bot itself can delete the channel early; normal
        # players never can. The channel is meant to auto-destroy only when
        # the match actually ends (forfeit / time-expiry / all solved).
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=True,
                read_message_history=False,
                send_messages=False,
                add_reactions=False,
                manage_channels=False,
                manage_messages=False,
            ),
            p1: discord.PermissionOverwrite(
                view_channel=True, read_message_history=True,
                send_messages=True, add_reactions=True,
                manage_channels=False, manage_messages=False),
            self.bot.user: discord.PermissionOverwrite(
                view_channel=True, read_message_history=True,
                send_messages=True, embed_links=True, attach_files=True,
                manage_messages=True, manage_channels=True),
        }
        if p2:
            overwrites[p2] = discord.PermissionOverwrite(
                view_channel=True, read_message_history=True,
                send_messages=True, add_reactions=True,
                manage_channels=False, manage_messages=False)

        channel = await guild.create_text_channel(
            channel_name, category=category, overwrites=overwrites,
            reason=f"Duel: {p1.name} vs {p2.name if p2 else 'Bot'}")

        mode_label = MODES[mode]["label"]
        topic = (f"🤖 {mode_label} · {p1.name} vs Bot" if is_bot
                 else f"{mode_label} · {p1.name} vs {p2.name}")
        try:
            await channel.edit(topic=topic)
        except Exception:
            pass
        print(f"[DUEL] ✅ Channel created: {channel.name}", flush=True)
        return channel

    async def _setup_problems(self, conn, duel_id: int, mode: str, p1_rating: int,
                              p2_rating: int, cfg: dict, is_bot: bool = False,
                              format_num: int = 3):
        duel = await dq.get_duel(conn, duel_id)
        n = format_num
        platform = MODES[mode]["platform"]
        icpc = MODES[mode]["icpc"]

        pkey = dq.pair_key(duel["player1_id"], duel["player2_id"])
        pair_history = await dq.get_pair_history(conn, pkey, platform)

        if platform == "cf":
            base = _base_rating(p1_rating, p2_rating)
            targets = _cf_targets(base, n, icpc)
            print(f"[DUEL] p1={p1_rating} p2={p2_rating} base={base} icpc={icpc} targets={targets}", flush=True)
            problems = await pick_cf_problems_at_ratings(targets, pair_history, icpc=icpc)
        else:
            problems = await pick_sequence(n, pair_history)

        if len(problems) < n:
            raise RuntimeError(f"Could only find {len(problems)}/{n} problems — try again.")

        # Timers: duel = ONE total deadline for all problems; blitz = per
        # problem, scaling with difficulty ordering (15/25/35 min for a
        # 3-problem match, 25/25 min for a 2-problem match) — same rule
        # for CF and LC blitz.
        if _is_duel_mode(mode):
            total = n * DUEL_SECS_PER_PROBLEM
            deadline = datetime.now(timezone.utc) + timedelta(seconds=total)
            deadlines = [deadline] * n
        else:
            deadlines = [
                datetime.now(timezone.utc) + timedelta(seconds=_blitz_secs_for(i, n))
                for i in range(1, n + 1)
            ]

        for i, prob in enumerate(problems, 1):
            if platform == "cf":
                difficulty = str(prob.get("rating") or "?")   # CF shows rating, never easy/med/hard
            else:
                difficulty = prob.get("difficulty_label") or prob.get("difficulty", "Medium")
            await dq.add_duel_problem(
                conn, duel_id, i, platform,
                prob["problem_id"], prob["title"],
                difficulty, prob.get("rating"), prob["url"],
                deadlines[i - 1],
                topic=prob.get("topic"),
            )
            await dq.add_pair_history(conn, pkey, platform, prob["problem_id"])
        print(f"[DUEL] {len(problems)} problem(s) ready for duel {duel_id}", flush=True)

    async def _get_all_problems(self, conn, duel_id: int, total: int) -> list[dict]:
        out = []
        for k in range(1, total + 1):
            p = await dq.get_current_problem(conn, duel_id, k)
            if p:
                out.append(dict(p))
        return out

    def _problem_embed(self, prob: dict, idx: int, total: int, mode: str,
                       header: str | None = None) -> discord.Embed:
        em = _brand(header or f"Problem {idx}/{total}", color=CLR_MATCH, thumb=False)
        em.add_field(
            name="__Problem__",
            value=f"[{prob['title']}]({prob['url']})\n"
                  f"Difficulty `{_problem_difficulty_display(prob)}`",
            inline=False)
        return em

    # ══════════════════════════════════════════════════════════════════════
    #  BLITZ FLOW — shared problems, one at a time, per-problem timer
    # ══════════════════════════════════════════════════════════════════════

    async def _start_blitz_problem(self, channel, duel_id: int, mode: str,
                                   cfg: dict, is_bot: bool = False):
        pool = get_pool()
        async with pool.acquire() as conn:
            duel = await dq.get_duel(conn, duel_id)
            if not duel or duel["status"] != "active":
                return
            duel = dict(duel)
            prob = await dq.get_current_problem(conn, duel_id, duel["current_game"])
        if not prob:
            await channel.send("Error: No problem found for this match.")
            return
        prob = dict(prob)

        p1_id, p2_id = duel["player1_id"], duel["player2_id"]
        p1 = self.bot.get_user(int(p1_id))
        p2 = self.bot.get_user(int(p2_id)) if p2_id else None
        p1_rating = await self._get_or_create_duel_rating(p1, channel.guild, mode)
        p2_rating = (await self._get_or_create_duel_rating(p2, channel.guild, mode)
                     if p2 else {"rating": duel["bot_rating"]})
        p1_name = p1.display_name if p1 else "Player 1"
        p2_name = p2.display_name if p2 else "Z4s"
        p1_rank = duel_ranks.get_rank(p1_rating["rating"])
        p2_rank = duel_ranks.get_rank(p2_rating["rating"])

        idx, total = duel["current_game"], duel["total_games"]
        per = _blitz_secs_for(idx, total)

        match_num = duel['duel_number'] or '?'
        is_first = (idx == 1)
        em = _brand(
            f"__{MODES[mode]['label']} — Match #{match_num}__",
            color=CLR_MATCH, banner=is_first)

        if is_first:
            # VS card with centered alignment
            vs_line = f"{p1_name}  vs  {p2_name}"
            rat_line = f"{p1_rank['name']} ({p1_rating['rating']})  ·  ({p2_rating['rating']}) {p2_rank['name']}"
            w = max(len(vs_line), len(rat_line)) + 4
            em.description = (
                f"**{MODE_BLURB[mode]}** · **{total}** problems, one at a time\n\n"
                f"```\n"
                f"{vs_line:^{w}}\n"
                f"{rat_line:^{w}}\n"
                f"```\n"
                f"First verified solve takes each problem.\n"
                f"Timer expires → draw → next problem.")
        else:
            em.description = (
                f"**{MODE_BLURB[mode]}** · Problem **{idx}** of **{total}**")

        em.add_field(
            name=f"__Problem {idx}/{total}__",
            value=f"[{prob['title']}]({prob['url']})", inline=True)
        em.add_field(
            name="__Difficulty__",
            value=f"`{_problem_difficulty_display(prob)}`", inline=True)
        em.add_field(
            name="__Time Limit__",
            value=f"`{_fmt_secs(per)}`", inline=True)

        em.set_footer(
            text="First verified solve takes this problem · Forfeit costs 32 rating",
            icon_url=BOT_LOGO)

        # 1) Countdown message first (appears above)
        cd_msg = await self._run_countdown(channel, seconds=7)

        # 2) Match embed below
        if is_first:
            msg = await channel.send(embed=em, view=MatchControlView(self, duel_id))
            p1_mention = f"<@{p1_id}>"
            p2_mention = f"<@{p2_id}>" if p2_id else ""
            await self._send_branded(channel,
                content=f"{p1_mention} {p2_mention} — the arena is live. Good luck.")
        else:
            msg = await channel.send(embed=em, view=SubmissionCheckerView(self, duel_id))

        # 3) Animate countdown above, then delete
        await self._animate_countdown(cd_msg, seconds=7)

        async with pool.acquire() as conn:
            deadline = datetime.now(timezone.utc) + timedelta(seconds=per)
            await conn.execute("UPDATE duel_problems SET deadline_at=$1 WHERE id=$2",
                               deadline, prob["id"])

        if is_bot or duel["is_bot_match"]:
            old = self._bot_solve_tasks.pop(duel_id, None)
            if old:
                old.cancel()
            self._bot_solve_tasks[duel_id] = asyncio.create_task(
                self._simulate_bot_blitz(channel, duel_id, prob, per, duel["bot_rating"]))

    async def _simulate_bot_blitz(self, channel, duel_id: int, prob: dict,
                                  time_limit: int, bot_rating: int):
        try:
            if prob["platform"] == "cf":
                solved, solve_time = botengine.simulate_attempt(
                    prob.get("rating") or 1500, bot_rating, time_limit)
            else:
                solved, solve_time = botengine.simulate_lc_game(
                    (prob.get("difficulty") or "medium").lower(), bot_rating, time_limit)
            if solve_time is None:
                return
            await asyncio.sleep(solve_time)
            pool = get_pool()
            async with pool.acquire() as conn:
                duel = await dq.get_duel(conn, duel_id)
                if not duel or duel["status"] != "active":
                    return
                await dq.mark_solved(conn, prob["id"], "p2", datetime.now(timezone.utc))
            try:
                await self._send_branded(channel, content=f"Bot solved **{prob['title']}** in {_fmt_secs(solve_time)}.")
            except Exception:
                pass
            # A bot solve is known instantly — resolve the problem NOW instead
            # of waiting for a human Check click or the deadline sweep (the
            # old behavior left the match frozen after the bot solved).
            # Same lock→conn ordering as blitz_check / auto_check; the resolve
            # still queries the human's submissions first, so if the player
            # solved earlier but never clicked Check, their timestamp wins.
            lock = _resolve_locks.setdefault(duel_id, asyncio.Lock())
            async with lock:
                async with pool.acquire() as conn:
                    fresh = await dq.get_duel(conn, duel_id)
                    if not fresh or fresh["status"] != "active":
                        return
                    fresh = dict(fresh)
                    prob_row = await dq.get_current_problem(conn, fresh["id"], fresh["current_game"])
                    if prob_row and prob_row["id"] == prob["id"]:
                        await self._resolve_blitz_problem(conn, fresh, dict(prob_row), channel)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[BOT_SIM] blitz error: {e}", flush=True)

    async def blitz_check(self, interaction: discord.Interaction, duel: dict):
        """Check Submissions handler for BLITZ matches."""
        lock = _resolve_locks.setdefault(duel["id"], asyncio.Lock())
        async with lock:
            pool = get_pool()
            async with pool.acquire() as conn:
                # Re-fetch under the lock — another Check / the expiry sweep
                # may have just resolved this problem or ended the match.
                fresh = await dq.get_duel(conn, duel["id"])
                if not fresh or fresh["status"] != "active":
                    await interaction.followup.send("❌ This duel isn't active anymore.", ephemeral=True)
                    return
                fresh = dict(fresh)
                prob = await dq.get_current_problem(conn, fresh["id"], fresh["current_game"])
                if not prob:
                    await interaction.followup.send("❌ No active problem found.", ephemeral=True)
                    return
                outcome = await self._resolve_blitz_problem(conn, fresh, dict(prob), interaction.channel)
        if outcome == "match_over":
            msg = "Checked — match finished. Result posted above."
        elif outcome == "problem_over":
            msg = "Checked — problem decided. Next problem starting."
        else:
            msg = "Checked — no verified solve yet. Keep going."
        await interaction.followup.send(msg, ephemeral=True)

    async def _resolve_blitz_problem(self, conn, duel: dict, prob: dict, channel) -> str:
        """Returns 'match_over' | 'problem_over' | 'pending'."""
        if prob.get("game_winner"):
            return "pending"  # already decided by a concurrent resolver
        p1_id, p2_id = duel["player1_id"], duel["player2_id"]
        since = _aware(duel.get("started_at")) or (datetime.now(timezone.utc) - timedelta(hours=6))

        p1_time = _aware(prob.get("p1_solved_at")) or await self._check_side(conn, p1_id, prob, since)
        if p2_id:
            p2_time = _aware(prob.get("p2_solved_at")) or await self._check_side(conn, p2_id, prob, since)
        else:
            p2_time = _aware(prob.get("p2_solved_at"))  # bot marks itself

        if p1_time and not prob.get("p1_solved_at"):
            await dq.mark_solved(conn, prob["id"], "p1", p1_time)
        if p2_time and p2_id and not prob.get("p2_solved_at"):
            await dq.mark_solved(conn, prob["id"], "p2", p2_time)

        deadline = _aware(prob.get("deadline_at"))
        expired = deadline is not None and datetime.now(timezone.utc) > deadline

        if not p1_time and not p2_time and not expired:
            return "pending"

        if p1_time and not p2_time:
            winner = "p1"
        elif p2_time and not p1_time:
            winner = "p2"
        elif p1_time and p2_time:
            winner = "p1" if p1_time <= p2_time else "p2"
        else:
            winner = "draw"

        await dq.set_game_winner(
            conn, prob["id"],
            p1_id if winner == "p1" else (p2_id or "BOT") if winner == "p2" else "DRAW")
        await dq.bump_game_score(conn, duel["id"], winner)

        t = self._bot_solve_tasks.pop(duel["id"], None)
        if t and t is not asyncio.current_task():
            t.cancel()

        updated = dict(await dq.get_duel(conn, duel["id"]))
        total = updated["total_games"]
        done = updated["current_game"] - 1
        p1_w, p2_w = updated["p1_games_won"], updated["p2_games_won"]
        remaining = total - done
        match_over = done >= total or abs(p1_w - p2_w) > remaining

        p1_user = self.bot.get_user(int(p1_id))
        p2_user = self.bot.get_user(int(p2_id)) if p2_id else None
        p1_name = p1_user.display_name if p1_user else "Player 1"
        p2_name = p2_user.display_name if p2_user else "Z4s"
        if winner == "p1":
            line = f"**Problem {done}:** {p1_name} takes it."
        elif winner == "p2":
            line = f"**Problem {done}:** {p2_name} takes it."
        else:
            line = f"**Problem {done}:** Draw (time expired)."
        try:
            await self._send_branded(channel, content=f"{line}  Score: **{p1_w} — {p2_w}**")
        except Exception:
            pass

        if match_over:
            if p1_w > p2_w:
                final_winner = p1_id
            elif p2_w > p1_w:
                final_winner = p2_id or "BOT"
            else:
                final_winner = None
            await dq.finish_duel(conn, duel["id"], final_winner)
            refreshed = dict(await dq.get_duel(conn, duel["id"]))
            asyncio.create_task(self._finish_match(channel, refreshed))
            return "match_over"
        else:
            cfg = await dq.get_duel_config(conn, updated["guild_id"])
            asyncio.create_task(self._start_blitz_problem(
                channel, duel["id"], updated["mode"], cfg, is_bot=updated["is_bot_match"]))
            return "problem_over"

    # ══════════════════════════════════════════════════════════════════════
    #  DUEL FLOW — ICPC-style: one total timer, independent progress
    # ══════════════════════════════════════════════════════════════════════

    async def _start_duel_match(self, channel, duel_id: int, mode: str,
                                cfg: dict, is_bot: bool = False):
        pool = get_pool()
        async with pool.acquire() as conn:
            duel = await dq.get_duel(conn, duel_id)
            if not duel or duel["status"] != "active":
                return
            duel = dict(duel)
            probs = await self._get_all_problems(conn, duel_id, duel["total_games"])
        if not probs:
            await channel.send("Error: No problems found for this match.")
            return

        n = duel["total_games"]
        total_secs = n * DUEL_SECS_PER_PROBLEM

        p1_id, p2_id = duel["player1_id"], duel["player2_id"]
        p1 = self.bot.get_user(int(p1_id))
        p2 = self.bot.get_user(int(p2_id)) if p2_id else None
        p1_rating = await self._get_or_create_duel_rating(p1, channel.guild, mode)
        p2_rating = (await self._get_or_create_duel_rating(p2, channel.guild, mode)
                     if p2 else {"rating": duel["bot_rating"]})
        p1_name = p1.display_name if p1 else "Player 1"
        p2_name = p2.display_name if p2 else "Z4s"
        p1_rank = duel_ranks.get_rank(p1_rating["rating"])
        p2_rank = duel_ranks.get_rank(p2_rating["rating"])

        match_num = duel['duel_number'] or '?'
        em = _brand(
            f"__{MODES[mode]['label']} — Match #{match_num}__",
            color=CLR_MATCH, banner=True)

        # VS card with centered alignment
        vs_line = f"{p1_name}  vs  {p2_name}"
        rat_line = f"{p1_rank['name']} ({p1_rating['rating']})  ·  ({p2_rating['rating']}) {p2_rank['name']}"
        w = max(len(vs_line), len(rat_line)) + 4
        em.description = (
            f"**{MODE_BLURB[mode]}** · **{n}** problems · **{total_secs // 60} min** total\n\n"
            f"```\n"
            f"{vs_line:^{w}}\n"
            f"{rat_line:^{w}}\n"
            f"```\n"
            f"Solve in order. Next problem unlocks (privately)\n"
            f"when you finish the current one.")

        first = probs[0]
        em.add_field(
            name=f"__Problem 1/{n}__",
            value=f"[{first['title']}]({first['url']})", inline=True)
        em.add_field(
            name="__Difficulty__",
            value=f"`{_problem_difficulty_display(first)}`", inline=True)
        em.add_field(
            name="__Total Time__",
            value=f"`{_fmt_secs(total_secs)}`", inline=True)

        em.set_footer(
            text="Scoring: solves → total time tiebreak · Check after each solve",
            icon_url=BOT_LOGO)

        # 1) Countdown message first (appears above)
        cd_msg = await self._run_countdown(channel, seconds=7)

        # 2) Match embed below
        msg = await channel.send(embed=em, view=MatchControlView(self, duel_id))
        p1_mention = f"<@{p1_id}>"
        p2_mention = f"<@{p2_id}>" if p2_id else ""
        await self._send_branded(channel,
            content=f"{p1_mention} {p2_mention} — the arena is live. Good luck.")

        # 3) Animate countdown above, then delete
        await self._animate_countdown(cd_msg, seconds=7)

        # Reset the shared deadline + start time now that the countdown finished
        deadline = datetime.now(timezone.utc) + timedelta(seconds=total_secs)
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE duel_problems SET deadline_at=$1 WHERE duel_id=$2",
                deadline, duel_id)
            await conn.execute(
                "UPDATE duels SET started_at=$1 WHERE id=$2",
                datetime.now(timezone.utc), duel_id)

        if is_bot or duel["is_bot_match"]:
            old = self._bot_solve_tasks.pop(duel_id, None)
            if old:
                old.cancel()
            self._bot_solve_tasks[duel_id] = asyncio.create_task(
                self._simulate_bot_duel(channel, duel_id, probs, total_secs, duel["bot_rating"]))

    async def _simulate_bot_duel(self, channel, duel_id: int, probs: list[dict],
                                 total_secs: int, bot_rating: int):
        """Bot solves problems sequentially against the shared clock."""
        try:
            elapsed = 0.0
            for prob in probs:
                remaining = total_secs - elapsed
                if remaining <= 0:
                    return
                if prob["platform"] == "cf":
                    solved, solve_time = botengine.simulate_attempt(
                        prob.get("rating") or 1500, bot_rating, int(remaining))
                else:
                    solved, solve_time = botengine.simulate_lc_game(
                        (prob.get("difficulty") or "medium").lower(), bot_rating, int(remaining))
                if solve_time is None:
                    return  # bot is stuck — no further progress this match
                await asyncio.sleep(solve_time)
                elapsed += solve_time

                pool = get_pool()
                async with pool.acquire() as conn:
                    duel = await dq.get_duel(conn, duel_id)
                    if not duel or duel["status"] != "active":
                        return
                    await dq.mark_solved(conn, prob["id"], "p2", datetime.now(timezone.utc))
                try:
                    await self._send_branded(channel, content=f"Bot solved **Problem {prob['game_number']}**.")
                except Exception:
                    pass
            # Bot solved everything → match ends early
            await self._finalize_duel(channel, duel_id)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[BOT_SIM] duel error: {e}", flush=True)

    async def duel_check(self, interaction: discord.Interaction, duel: dict):
        """Check Submissions handler for DUEL matches (per-player progress)."""
        uid = interaction.user.id
        p1_id = int(duel["player1_id"])
        p2_id = int(duel["player2_id"]) if duel["player2_id"] else None

        if uid == p1_id:
            side = "p1"
        elif p2_id and uid == p2_id:
            side = "p2"
        else:
            await interaction.followup.send("❌ Only the match players can check submissions.", ephemeral=True)
            return

        pool = get_pool()
        n = duel["total_games"]
        async with pool.acquire() as conn:
            probs = await self._get_all_problems(conn, duel["id"], n)

            deadline = _aware(probs[0].get("deadline_at")) if probs else None
            if deadline and datetime.now(timezone.utc) > deadline:
                await interaction.followup.send("⏰ Time is up! Calculating results…", ephemeral=True)
                await self._finalize_duel(interaction.channel, duel["id"])
                return

            current = None
            for p in probs:
                if p.get(f"{side}_solved_at") is None:
                    current = p
                    break

            if current is None:
                await interaction.followup.send("You've already finished all problems.", ephemeral=True)
                return

            since = _aware(duel.get("started_at")) or (datetime.now(timezone.utc) - timedelta(hours=6))
            user_id = duel["player1_id"] if side == "p1" else duel["player2_id"]
            solve_ts = await self._check_side(conn, user_id, current, since)

            if not solve_ts:
                em = self._problem_embed(current, current["game_number"], n, duel["mode"],
                                         header=f"Your current problem — {current['game_number']}/{n}")
                em.set_footer(text="No verified solve yet. Only you can see this.", icon_url=BOT_LOGO)
                await interaction.followup.send(embed=em, ephemeral=True)
                return

            await dq.mark_solved(conn, current["id"], side, solve_ts)
            k = current["game_number"]

        name = interaction.user.display_name
        try:
            await self._send_branded(interaction.channel, content=f"**{name}** solved Problem {k}/{n}.")
        except Exception:
            pass

        if k == n:
            await interaction.followup.send("You solved everything — finalizing the match.", ephemeral=True)
            await self._finalize_duel(interaction.channel, duel["id"])
            return

        nxt = probs[k]  # probs is 0-indexed, so probs[k] is problem k+1
        em = self._problem_embed(nxt, k + 1, n, duel["mode"],
                                 header=f"Problem {k + 1}/{n} unlocked")
        em.set_footer(text="Only you can see this. Your opponent must solve their way here.", icon_url=BOT_LOGO)
        await interaction.followup.send(embed=em, ephemeral=True)

    async def _finalize_duel(self, channel, duel_id: int):
        """Score a DUEL match: solve count → total time tiebreak → draw."""
        if duel_id in self._finalizing:
            return
        self._finalizing.add(duel_id)
        try:
            pool = get_pool()
            async with pool.acquire() as conn:
                duel = await dq.get_duel(conn, duel_id)
                if not duel or duel["status"] != "active":
                    return
                duel = dict(duel)
                n = duel["total_games"]
                probs = await self._get_all_problems(conn, duel_id, n)
                start = _aware(duel.get("started_at")) or datetime.now(timezone.utc)

                c1 = c2 = 0
                t1 = t2 = 0.0
                for p in probs:
                    s1 = _aware(p.get("p1_solved_at"))
                    s2 = _aware(p.get("p2_solved_at"))
                    if s1:
                        c1 += 1
                        t1 += max(0.0, (s1 - start).total_seconds())
                    if s2:
                        c2 += 1
                        t2 += max(0.0, (s2 - start).total_seconds())

                if c1 > c2:
                    side, s1_, s2_, r1_, r2_ = "p1", 1.0, 0.0, "win", "loss"
                elif c2 > c1:
                    side, s1_, s2_, r1_, r2_ = "p2", 0.0, 1.0, "loss", "win"
                elif c1 == 0:
                    side, s1_, s2_, r1_, r2_ = "draw", 0.5, 0.5, "draw", "draw"
                elif t1 < t2:
                    side, s1_, s2_, r1_, r2_ = "p1", 1.0, 0.0, "win", "loss"
                elif t2 < t1:
                    side, s1_, s2_, r1_, r2_ = "p2", 0.0, 1.0, "loss", "win"
                else:
                    side, s1_, s2_, r1_, r2_ = "draw", 0.5, 0.5, "draw", "draw"

                if side == "p1":
                    winner_id = duel["player1_id"]
                elif side == "p2":
                    winner_id = duel["player2_id"] or "BOT"
                else:
                    winner_id = None

                await conn.execute(
                    "UPDATE duels SET p1_games_won=$1, p2_games_won=$2 WHERE id=$3",
                    c1, c2, duel_id)
                await dq.finish_duel(conn, duel_id, winner_id)
                refreshed = dict(await dq.get_duel(conn, duel_id))

            t = self._bot_solve_tasks.pop(duel_id, None)
            # NEVER cancel ourselves: when the bot solves its last problem,
            # this finalize runs INSIDE the bot-sim task — cancelling it here
            # killed the finish flow mid-air (status already 'finished', so
            # auto_check ignored it too → orphan room, no stats, no ratings).
            if t and t is not asyncio.current_task():
                t.cancel()

            extra = None
            if c1 or c2:
                extra = [f"⏱️ Total solve time — P1: {_fmt_secs(t1)} | P2: {_fmt_secs(t2)}"]
            await self._finish_match(channel, refreshed,
                                     result_override=(s1_, s2_, r1_, r2_),
                                     extra_lines=extra)
        finally:
            self._finalizing.discard(duel_id)

    # ── Solve checking (shared) ────────────────────────────────────────────

    async def _check_side(self, conn, user_id: str, prob: dict, since_dt):
        """Earliest valid solve time (aware datetime) after `since_dt`, or None."""
        platform = prob["platform"]
        handle = await dq.get_handle_for_user(conn, user_id, platform)
        if not handle:
            return None

        problem_id = prob["problem_id"]
        since_ts = _aware(since_dt).timestamp() if since_dt else 0

        if platform == "cf":
            adapter = P.CodeforcesAdapter()
            subs = await adapter.fetch_all_submissions(handle)
            best = None
            for s in subs:
                if s.get("verdict") != "OK":
                    continue
                p = s.get("problem", {})
                pid = f"{p.get('contestId', '')}{p.get('index', '')}"
                if pid != problem_id:
                    continue
                ts = float(s.get("creationTimeSeconds", 0))
                if ts < since_ts:
                    continue
                if best is None or ts < best:
                    best = ts
            return datetime.fromtimestamp(best, tz=timezone.utc) if best else None
        else:
            adapter = P.LeetCodeAdapter()
            subs = await adapter.get_recent_submissions(handle)
            best = None
            for s in subs:  # Submission dataclass — attribute access
                if s.problem_id != problem_id:
                    continue
                ts = float(s.timestamp)
                if ts < since_ts:
                    continue
                if best is None or ts < best:
                    best = ts
            return datetime.fromtimestamp(best, tz=timezone.utc) if best else None

    # ── Ratings & finish (shared) ──────────────────────────────────────────

    async def _apply_final_ratings(self, conn, duel: dict, result_override=None) -> dict:
        mode = duel["mode"]
        guild_id = duel["guild_id"]
        p1_id = str(duel["player1_id"])
        p2_id = str(duel["player2_id"]) if duel["player2_id"] else None
        is_bot = duel["is_bot_match"]
        cfg = await dq.get_duel_config(conn, guild_id)

        p1_row = await dq.get_or_create_rating(conn, p1_id, guild_id, mode)
        p1_old = p1_row["rating"]
        if p2_id:
            p2_row = await dq.get_or_create_rating(conn, p2_id, guild_id, mode)
            p2_old = p2_row["rating"]
        else:
            p2_old = duel["bot_rating"] or p1_old

        if result_override:
            s1, s2, r1, r2 = result_override
        else:
            p1_w, p2_w = duel["p1_games_won"], duel["p2_games_won"]
            if p1_w > p2_w:
                s1, s2, r1, r2 = 1.0, 0.0, "win", "loss"
            elif p2_w > p1_w:
                s1, s2, r1, r2 = 0.0, 1.0, "loss", "win"
            else:
                s1, s2, r1, r2 = 0.5, 0.5, "draw", "draw"

        family = MODES[mode]["family"]
        if family == "dsa":
            if _is_duel_mode(mode):
                win_pts = int(cfg.get("lc_duel_win", 22))
                loss_pts = int(cfg.get("lc_duel_loss", 12))
            else:
                win_pts = int(cfg.get("lc_blitz_med_win", 12))
                loss_pts = int(cfg.get("lc_blitz_med_loss", 6))
            d1 = win_pts if s1 == 1.0 else (-loss_pts if s1 == 0.0 else 0)
            d2 = win_pts if s2 == 1.0 else (-loss_pts if s2 == 0.0 else 0)
        else:
            if family == "icpc":
                k = int(cfg.get("icpc_k", 40))
            elif _is_duel_mode(mode):
                k = int(cfg.get("cp_duel_k", 32))
            else:
                k = int(cfg.get("cp_blitz_k", 24))
            d1 = _elo_delta(p1_old, p2_old, s1, k)
            d2 = _elo_delta(p2_old, p1_old, s2, k)

        await dq.apply_rating_delta(conn, p1_id, guild_id, mode, d1, r1, is_bot)
        if p2_id:
            await dq.apply_rating_delta(conn, p2_id, guild_id, mode, d2, r2, is_bot)
        else:
            d2 = 0

        return {"p1_old": p1_old, "p1_new": p1_old + d1, "p1_delta": d1,
                "p2_old": p2_old, "p2_new": p2_old + d2, "p2_delta": d2}

    def _find_stats_channel(self, guild: discord.Guild, mode: str):
        raw = getattr(config, "DUEL_MODE_CHANNELS", {}).get(mode, "")
        if raw:
            if str(raw).isdigit():
                ch = guild.get_channel(int(raw))
                if ch:
                    return ch
            ch = discord.utils.get(guild.text_channels, name=str(raw).lstrip("#"))
            if ch:
                return ch
        for name in MODE_STATS_CHANNELS.get(mode, []):
            ch = discord.utils.get(guild.text_channels, name=name)
            if ch:
                return ch
        return discord.utils.get(guild.text_channels, name=mode.replace("_", "-"))

    async def _finish_match(self, channel, duel: dict, forfeited_by: str = None,
                            forfeit_ratings: dict = None, result_override=None,
                            extra_lines: list[str] | None = None):
        # A match must never finish twice — forfeit racing an auto-check
        # expiry, or two resolvers reaching match_over back-to-back, would
        # otherwise double-post stats and double-apply ratings.
        did = duel.get("id")
        if did in self._finished:
            print(f"[FINISH] ⏭️ duel {did} already finishing — skipped duplicate.", flush=True)
            return
        self._finished.add(did)
        try:
            pool = get_pool()
            mode = duel["mode"]
            p1_id = duel["player1_id"]
            p2_id = duel["player2_id"]

            p1 = self.bot.get_user(int(p1_id))
            p2 = self.bot.get_user(int(p2_id)) if p2_id else None
            p1_name = p1.display_name if p1 else f"Player {p1_id}"
            p2_name = p2.display_name if p2 else "Z4s"

            if forfeit_ratings is None:
                async with pool.acquire() as conn:
                    ratings = await self._apply_final_ratings(conn, duel, result_override)
            else:
                ratings = forfeit_ratings

            p1_w, p2_w = duel["p1_games_won"], duel["p2_games_won"]
            if result_override:
                s1, s2 = result_override[0], result_override[1]
                if s1 > s2:
                    winner_name = p1_name
                elif s2 > s1:
                    winner_name = p2_name
                else:
                    winner_name = "Draw"
            elif p1_w > p2_w:
                winner_name = p1_name
            elif p2_w > p1_w:
                winner_name = p2_name
            else:
                winner_name = "Draw"
            score_str = f"{p1_w}-{p2_w}"

            # 1) quick result in the private room
            quick_color = CLR_WIN if winner_name != "Draw" else CLR_MATCH
            quick_title = (f"**{winner_name}** wins {score_str}"
                           if winner_name != "Draw" else f"Draw — {score_str}")
            quick = _brand("__Match Complete__", desc=quick_title, color=quick_color, thumb=False)

            quick.add_field(
                name=f"__{p1_name}__",
                value=f"`{ratings['p1_old']}` → `{ratings['p1_new']}` ({ratings['p1_delta']:+d})",
                inline=True)
            if p2_id:
                quick.add_field(
                    name=f"__{p2_name}__",
                    value=f"`{ratings['p2_old']}` → `{ratings['p2_new']}` ({ratings['p2_delta']:+d})",
                    inline=True)
            if extra_lines:
                quick.add_field(name="__Details__", value="\n".join(extra_lines), inline=False)
            quick.set_footer(text="Posting stats to the public channel… room closes in 10s", icon_url=BOT_LOGO)
            try:
                await self._send_branded(channel, embed=quick)
            except Exception:
                pass

            # 2) public stats
            stats_ch = self._find_stats_channel(channel.guild, mode)
            if stats_ch:
                s_color = CLR_LOSS if forfeited_by else (CLR_RESULT if winner_name != "Draw" else CLR_MATCH)
                s_title = "__Match Result (Forfeit)__" if forfeited_by else "__Match Result__"
                match_num = duel.get('duel_number') or '?'
                em = _brand(s_title, color=s_color, banner=True)

                result_line = (f"**{winner_name}** wins {score_str}" if winner_name != "Draw"
                               else f"Draw — {score_str}")
                if forfeited_by:
                    result_line += f"\n*{forfeited_by} forfeited*"

                em.description = (
                    f"{MODES[mode]['label']} · Match #{match_num} · "
                    f"{duel['total_games']}-Problem\n"
                    f"\n"
                    f"{result_line}")

                # Rating fields
                p1_delta = ratings['p1_delta']
                p1_arrow = f"{'📈' if p1_delta >= 0 else '📉'}"
                em.add_field(
                    name=f"{p1_arrow} {p1_name}",
                    value=f"`{ratings['p1_old']}` → `{ratings['p1_new']}` **({p1_delta:+d})**",
                    inline=True)
                if p2_id:
                    p2_delta = ratings['p2_delta']
                    p2_arrow = f"{'📈' if p2_delta >= 0 else '📉'}"
                    em.add_field(
                        name=f"{p2_arrow} {p2_name}",
                        value=f"`{ratings['p2_old']}` → `{ratings['p2_new']}` **({p2_delta:+d})**",
                        inline=True)

                rank = duel_ranks.get_rank(ratings["p1_new"])
                rank_line = f"{p1_name}: **{rank['name']}**"
                if extra_lines:
                    rank_line += "\n" + "\n".join(extra_lines)
                em.add_field(name="__Details__", value=rank_line, inline=False)

                em.set_footer(text=f"Completed at {_ist_now_str()}", icon_url=BOT_LOGO)
                try:
                    await self._send_branded(stats_ch, embed=em)
                    print(f"[FINISH] ✅ Stats → #{stats_ch.name}", flush=True)
                except Exception as e:
                    print(f"[FINISH] ❌ stats post failed: {e}", flush=True)
            else:
                print(f"[FINISH] ❌ no stats channel for {mode}", flush=True)

            t = self._bot_solve_tasks.pop(duel["id"], None)
            if t and t is not asyncio.current_task():
                t.cancel()

            # 3) edit jump-link card → "Match ended" → auto-delete
            jump_msg = self._live_cards.pop(duel["id"], None)
            if jump_msg:
                try:
                    ended = _brand("__Match ended.__", color=CLR_NEUTRAL, thumb=False)
                    ended.set_footer(text="Room closing.", icon_url=BOT_LOGO)
                    await jump_msg.edit(embed=ended)
                    await jump_msg.delete(delay=10)
                except Exception:
                    pass

            # 4) delete the room
            await asyncio.sleep(10)
            try:
                await channel.delete(reason="Duel finished")
                print(f"[FINISH] ✅ Channel deleted: {channel.name}", flush=True)
            except Exception as e:
                print(f"[FINISH] ❌ channel delete failed: {e}", flush=True)
        except Exception as e:
            print(f"[FINISH] ❌ crashed: {type(e).__name__}: {e}", flush=True)
            import traceback; traceback.print_exc()

    # ── Forfeit ────────────────────────────────────────────────────────────

    async def handle_forfeit(self, interaction: discord.Interaction, duel_id: int):
        pool = get_pool()
        async with pool.acquire() as conn:
            duel = await dq.get_duel(conn, duel_id)
            if not duel or duel["status"] != "active":
                await interaction.followup.send("❌ This match isn't active anymore.", ephemeral=True)
                return
            duel = dict(duel)

            uid = interaction.user.id
            p1_id = int(duel["player1_id"])
            p2_id = int(duel["player2_id"]) if duel["player2_id"] else None
            if uid != p1_id and uid != p2_id:
                await interaction.followup.send("❌ Only the match players can forfeit.", ephemeral=True)
                return

            is_p1 = uid == p1_id
            total = duel["total_games"]
            if is_p1:
                winner_id = duel["player2_id"] or "BOT"
                await conn.execute("UPDATE duels SET p2_games_won=$1 WHERE id=$2", total, duel_id)
            else:
                winner_id = duel["player1_id"]
                await conn.execute("UPDATE duels SET p1_games_won=$1 WHERE id=$2", total, duel_id)
            await dq.finish_duel(conn, duel_id, winner_id)

            mode = duel["mode"]
            guild_id = duel["guild_id"]

            f_row = await dq.get_or_create_rating(conn, str(uid), guild_id, mode)
            f_old = f_row["rating"]
            await dq.apply_rating_delta(conn, str(uid), guild_id, mode,
                                        -FORFEIT_PENALTY, "loss", duel["is_bot_match"])

            if winner_id and winner_id != "BOT":
                w_row = await dq.get_or_create_rating(conn, str(winner_id), guild_id, mode)
                w_old = w_row["rating"]
                await dq.apply_rating_delta(conn, str(winner_id), guild_id, mode,
                                            FORFEIT_REWARD, "win", duel["is_bot_match"])
            else:
                w_old = duel["bot_rating"] or 800

            duel = dict(await dq.get_duel(conn, duel_id))

        if is_p1:
            ratings = {"p1_old": f_old, "p1_new": f_old - FORFEIT_PENALTY, "p1_delta": -FORFEIT_PENALTY,
                       "p2_old": w_old,
                       "p2_new": w_old + (FORFEIT_REWARD if winner_id != "BOT" else 0),
                       "p2_delta": (FORFEIT_REWARD if winner_id != "BOT" else 0)}
        else:
            ratings = {"p1_old": w_old, "p1_new": w_old + FORFEIT_REWARD, "p1_delta": FORFEIT_REWARD,
                       "p2_old": f_old, "p2_new": f_old - FORFEIT_PENALTY, "p2_delta": -FORFEIT_PENALTY}

        t = self._bot_solve_tasks.pop(duel_id, None)
        if t and t is not asyncio.current_task():
            t.cancel()

        forfeit_embed = _brand(
            "__Match Forfeited__",
            desc=f"{interaction.user.mention} forfeited.\n"
                 f"**−{FORFEIT_PENALTY} rating** for {interaction.user.display_name}.",
            color=CLR_LOSS, thumb=False)
        forfeit_embed.set_footer(text=BRAND, icon_url=BOT_LOGO)
        try:
            await self._send_branded(interaction.channel, embed=forfeit_embed)
        except Exception:
            pass
        await interaction.followup.send(
            f"Match forfeited. −{FORFEIT_PENALTY} rating.", ephemeral=True)

        await self._finish_match(interaction.channel, duel,
                                 forfeited_by=interaction.user.display_name,
                                 forfeit_ratings=ratings)

    # ── Background auto-check ──────────────────────────────────────────────

    @tasks.loop(minutes=0.75)
    async def auto_check(self):
        pool = get_pool()
        try:
            async with pool.acquire() as conn:
                duels = await dq.get_all_active_duels(conn)
        except Exception as e:
            print(f"[AUTO] fetch error: {e}", flush=True)
            return

        for duel in duels:
            try:
                duel = dict(duel)
                channel = self.bot.get_channel(int(duel["channel_id"])) if duel["channel_id"] else None
                if not channel:
                    continue
                needs_duel_finalize = False
                needs_blitz_resolve = False
                async with pool.acquire() as conn:
                    prob = await dq.get_current_problem(conn, duel["id"], duel["current_game"])
                    if not prob:
                        continue
                    prob = dict(prob)
                    deadline = _aware(prob.get("deadline_at"))
                    if not deadline or datetime.now(timezone.utc) <= deadline:
                        continue
                    if _is_duel_mode(duel["mode"]):
                        needs_duel_finalize = True
                    else:
                        needs_blitz_resolve = True
                if needs_duel_finalize:
                    await self._finalize_duel(channel, duel["id"])
                elif needs_blitz_resolve:
                    # lock → conn ordering, same as blitz_check, so the sweep
                    # can never double-resolve a problem against a player's
                    # simultaneous Check click.
                    lock = _resolve_locks.setdefault(duel["id"], asyncio.Lock())
                    async with lock:
                        async with pool.acquire() as conn:
                            fresh = await dq.get_duel(conn, duel["id"])
                            if not fresh or fresh["status"] != "active":
                                continue
                            fresh = dict(fresh)
                            fprob = await dq.get_current_problem(conn, fresh["id"], fresh["current_game"])
                            if not fprob:
                                continue
                            fprob = dict(fprob)
                            fdl = _aware(fprob.get("deadline_at"))
                            if fdl and datetime.now(timezone.utc) > fdl:
                                await self._resolve_blitz_problem(conn, fresh, fprob, channel)
            except Exception as e:
                print(f"[AUTO] duel {duel.get('id')} error: {e}", flush=True)

    @auto_check.before_loop
    async def before_auto_check(self):
        await self.bot.wait_until_ready()

    # ── Safety net: channel deleted out from under an active duel ──────────

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        """
        Normal players are permission-denied from deleting an active match's
        channel (see the explicit manage_channels=False overrides in
        _create_duel_channel), so this should only ever fire for an active
        duel if a genuine admin/mod deletes it manually via their own
        server-wide permissions. If that happens, the duel row would
        otherwise stay stuck 'active' forever in the DB — which, combined
        with the no-simultaneous-match check, would permanently block both
        players from ever starting another duel. Void the match cleanly
        (no winner, no rating change) so that doesn't happen.
        """
        pool = get_pool()
        try:
            async with pool.acquire() as conn:
                duels = await dq.get_all_active_duels(conn)
                duel = next((dict(d) for d in duels
                            if str(d.get("channel_id")) == str(channel.id)), None)
                if not duel:
                    return
                # Void cleanly: no winner, no rating change, and score reset
                # to 0-0 so the DB row can't be misread as a real result.
                await conn.execute(
                    "UPDATE duels SET p1_games_won=0, p2_games_won=0 WHERE id=$1",
                    duel["id"])
                await dq.finish_duel(conn, duel["id"], None)
            print(f"[DUEL] ⚠️ Channel for active duel #{duel.get('duel_number')} "
                  f"(id={duel['id']}) was deleted externally — match voided, "
                  f"no rating changes applied.", flush=True)
            t = self._bot_solve_tasks.pop(duel["id"], None)
            if t and t is not asyncio.current_task():
                t.cancel()
        except Exception as e:
            print(f"[DUEL] on_guild_channel_delete error: {e}", flush=True)


async def setup(bot):
    await bot.add_cog(Duels(bot))