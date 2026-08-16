"""
cogs/duels.py — Professional 1v1 Duel system with Codeforces-style ratings.

Modes:
  cp_blitz / cp_duel     — Codeforces, rating-based problem(s), real Elo
  dsa_blitz / dsa_duel   — LeetCode, difficulty-based problem(s), fixed points
  icpc_blitz / icpc_duel — Codeforces, admin-set rating band + mandatory math+algo tags

blitz = 1 problem race. duel = Bo3 (best of 3 games).
Bot opponent available at any rating from Newbie (800) to Legendary Grandmaster (3000+).
Matches occur in dedicated mode-based channels. Professional formatting throughout.
"""

import asyncio
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
from platforms.duel_cf_pool import pick_many_cf_problems
from platforms.duel_lc_pool import pick_lc_problem, pick_sequence
import duel_bot_engine as botengine

# ── Colors & constants ────────────────────────────────────────────────────
COLOR_SUCCESS = getattr(config, "COLOR_SUCCESS", 0x57F287)
COLOR_ERROR   = getattr(config, "COLOR_ERROR", 0xED4245)
COLOR_INFO    = getattr(config, "COLOR_INFO", 0x5865F2)
COLOR_WARN    = getattr(config, "COLOR_WARN", 0xFEE75C)
COLOR_CYAN    = getattr(config, "COLOR_CYAN", 0x00D9FF)
ADMIN_ROLE    = getattr(config, "ADMIN_ROLE", "Admin")

MODES = {
    "cp_blitz":   {"platform": "cf", "games": 1, "family": "cp",   "icpc": False, "label": "CF Blitz"},
    "cp_duel":    {"platform": "cf", "games": 3, "family": "cp",   "icpc": False, "label": "CF Duel"},
    "dsa_blitz":  {"platform": "lc", "games": 1, "family": "dsa",  "icpc": False, "label": "LC Blitz"},
    "dsa_duel":   {"platform": "lc", "games": 3, "family": "dsa",  "icpc": False, "label": "LC Duel"},
    "icpc_blitz": {"platform": "cf", "games": 1, "family": "icpc", "icpc": True,  "label": "ICPC Blitz"},
    "icpc_duel":  {"platform": "cf", "games": 3, "family": "icpc", "icpc": True,  "label": "ICPC Duel"},
}

CHECK_COOLDOWN_SEC = 20
_last_check: dict[int, float] = {}
_duel_locks: dict[str, asyncio.Lock] = {}  # guild_id → lock for number allocation


def is_admin():
    """Check if user is admin or has ADMIN_ROLE."""
    async def predicate(ctx):
        return (
            any(r.name == ADMIN_ROLE for r in ctx.author.roles)
            or ctx.author.guild_permissions.administrator
        )
    return commands.check(predicate)


def _fmt_secs(s: float) -> str:
    """Format seconds as readable time string."""
    s = max(0, int(s))
    m, sec = divmod(s, 60)
    return f"{m}m {sec:02d}s" if m else f"{sec}s"


async def _fetch_cf_live_ratings(handles: list[str]) -> dict:
    """Fetch current CF ratings for problem-difficulty band & ICPC gate."""
    handles = [h for h in handles if h]
    if not handles:
        return {}
    url = f"https://codeforces.com/api/user.info?handles={';'.join(handles)}"
    try:
        async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0 (compatible; CPBot/1.0)"}) as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                data = await r.json()
    except Exception:
        return {}
    if data.get("status") != "OK":
        return {}
    return {u["handle"]: u.get("rating", 1200) for u in data.get("result", [])}


# ══════════════════════════════════════════════════════════════════════════
#  Views / UI Components
# ══════════════════════════════════════════════════════════════════════════

class ChallengeView(discord.ui.View):
    """Accept/Decline challenge buttons."""
    def __init__(self, cog, ctx, challenger: discord.Member, opponent: discord.Member, mode: str, timeout_s: int, format_num: int = 3):
        super().__init__(timeout=timeout_s)
        self.cog, self.ctx = cog, ctx
        self.challenger, self.opponent, self.mode = challenger, opponent, mode
        self.format_num = format_num  # ✅ NEW: Store format
        self.responded = False
        self.message: discord.Message | None = None

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.primary)  # ✅ Blurple instead of green
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.opponent.id:  # ✅ Only opponent can click
            await interaction.response.send_message("Only the challenged player can respond.", ephemeral=True)
            return
        self.responded = True
        self.stop()
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(
            content=f"✅ Accepted. Setting up match…",
            view=self,
        )
        await self.cog.begin_human_match(self.ctx, self.challenger, self.opponent, self.mode, self.format_num)  # ✅ Pass format_num

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.secondary)  # ✅ Gray instead of red
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.opponent.id:  # ✅ Only opponent can click
            await interaction.response.send_message("Only the challenged player can respond.", ephemeral=True)
            return
        self.responded = True
        self.stop()
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(content=f"❌ Challenge declined.", view=self)
        await self.cog.offer_bot_fallback(interaction.channel, self.challenger, self.mode)

    async def on_timeout(self):
        if self.responded:
            return
        for c in self.children:
            c.disabled = True
        try:
            if self.message:
                await self.message.edit(
                    content=f"Challenge expired — no response received.",
                    view=self,
                )
        except Exception:
            pass
        channel = self.message.channel if self.message else self.ctx.channel
        await self.cog.offer_bot_fallback(channel, self.challenger, self.mode)


class ForfeitView(discord.ui.View):
    """Forfeit button during active match."""
    def __init__(self, cog, duel_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.duel_id = duel_id

    @discord.ui.button(label="🏳️ Forfeit Match", style=discord.ButtonStyle.danger)
    async def forfeit(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        
        pool = get_pool()
        async with pool.acquire() as conn:
            duel = await dq.get_duel(conn, self.duel_id)
            
            if not duel or duel["status"] != "active":
                await interaction.followup.send("❌ This match isn't active anymore.", ephemeral=True)
                return
            
            # Only players can forfeit
            if interaction.user.id != int(duel["player1_id"]) and interaction.user.id != int(duel["player2_id"]):
                await interaction.followup.send("❌ Only the match players can forfeit.", ephemeral=True)
                return
            
            # Determine forfeit status
            forfeit_player_id = interaction.user.id
            is_player1 = forfeit_player_id == int(duel["player1_id"])
            
            # Give opponent the win (if they haven't won yet)
            if is_player1:
                # Player 1 forfeits, Player 2 wins
                winner_id = duel["player2_id"]
                # Award remaining games to player 2
                games_left = duel["total_games"] - duel["p1_games_won"] - duel["p2_games_won"]
                new_p2_wins = duel["p2_games_won"] + games_left
                await conn.execute(
                    "UPDATE duels SET p2_games_won = $1 WHERE id = $2",
                    new_p2_wins, self.duel_id
                )
            else:
                # Player 2 forfeits, Player 1 wins
                winner_id = duel["player1_id"]
                games_left = duel["total_games"] - duel["p1_games_won"] - duel["p2_games_won"]
                new_p1_wins = duel["p1_games_won"] + games_left
                await conn.execute(
                    "UPDATE duels SET p1_games_won = $1 WHERE id = $2",
                    new_p1_wins, self.duel_id
                )
            
            # Finish the duel
            await dq.finish_duel(conn, self.duel_id, winner_id)
            
            # Update ratings: forfeitor gets -32, winner gets +16
            forfeit_player_key = str(forfeit_player_id)
            winner_key = str(winner_id)
            mode = duel["mode"]
            guild_id = str(duel.get("guild_id", interaction.guild.id))
            
            await dq.apply_rating_delta(conn, forfeit_player_key, guild_id, mode, -32, "loss", False)
            await dq.apply_rating_delta(conn, winner_key, guild_id, mode, 16, "win", False)
        
        # Post forfeit message
        forfeit_embed = discord.Embed(
            title="🏳️ Match Forfeited",
            description=f"{interaction.user.mention} has forfeited the match.",
            color=0xFF6B6B,
        )
        forfeit_embed.add_field(
            name="❌ Rating Penalty",
            value=f"{interaction.user.display_name}: **-32 rating**",
            inline=False,
        )
        forfeit_embed.set_footer(text="Channel will be archived in 15 seconds")
        
        await interaction.channel.send(embed=forfeit_embed)
        await interaction.followup.send("✅ Match forfeited. You lost 32 rating points.", ephemeral=True)
        
        # Delete channel after delay
        await asyncio.sleep(15)
        try:
            await interaction.channel.delete(reason="Duel forfeited")
        except Exception:
            pass
    """'Check Submissions' button on round intro embed."""
    def __init__(self, cog, duel_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.duel_id = duel_id

    @discord.ui.button(label="✅ Check Submissions", style=discord.ButtonStyle.primary)  # ✅ Better label
    async def check(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        pool = get_pool()
        async with pool.acquire() as conn:
            duel = await dq.get_duel(conn, self.duel_id)
            if not duel or duel["status"] != "active":
                await interaction.followup.send("❌ This duel isn't active anymore.", ephemeral=True)
                return
            prob = await dq.get_current_problem(conn, duel["id"], duel["current_game"])
            if not prob:
                await interaction.followup.send("❌ No active game found.", ephemeral=True)
                return
            last = _last_check.get(duel["id"], 0)
            if time.monotonic() - last < CHECK_COOLDOWN_SEC:
                await interaction.followup.send(f"⏱️ Checked recently — try again in {CHECK_COOLDOWN_SEC}s.", ephemeral=True)
                return
            _last_check[duel["id"]] = time.monotonic()
            finalized = await self.cog._resolve_game(conn, duel, prob, interaction.channel)
        await interaction.followup.send(
            "✅ Checked — result posted above." if finalized else "⏳ Checked — no verified solve yet.",
            ephemeral=True,
        )


class BotFallbackView(discord.ui.View):
    """'Play vs Bot' fallback after decline/timeout."""
    def __init__(self, cog, challenger: discord.Member, mode: str, timeout_s: int = 180):
        super().__init__(timeout=timeout_s)
        self.cog, self.challenger, self.mode = cog, challenger, mode

    @discord.ui.button(label="🤖 Play vs Bot", style=discord.ButtonStyle.primary)  # ✅ Better emoji
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
        self.auto_check.start()

    def cog_unload(self):
        self.auto_check.cancel()

    # ── Duel Commands ─────────────────────────────────────────────────────

    @commands.command(name="duel", help="Challenge a player or bot to a duel.")
    async def duel_cmd(self, ctx, opponent_str: str, mode: str, format_or_rating: str = None, rating_str: str = None):
        """
        !duel @user cp_blitz              Challenge a player (3-problem format)
        !duel @user cp_blitz 2            Challenge a player (2-problem format)
        !duel @user cp_blitz 3            Challenge a player (3-problem format)
        !duel bot cp_blitz                Challenge the bot (at your rating, 3 problems)
        !duel bot cp_blitz 2              Challenge the bot (2-problem format)
        !duel bot cp_blitz 3              Challenge the bot (3-problem format)
        !duel bot cp_blitz 1600           Challenge the bot at specific rating (3 problems)
        !duel bot cp_blitz 2 1600         Challenge the bot (2 problems, 1600 rating)
        """
        mode = mode.lower()
        if mode not in MODES:
            modes_list = ", ".join(MODES.keys())
            await ctx.send(f"Unknown mode. Available: {modes_list}")
            return

        # Parse format_or_rating and rating_str
        format_num = 3  # Default
        bot_rating_override = None
        
        if format_or_rating:
            if format_or_rating in ("2", "3"):
                format_num = int(format_or_rating)
                # If there's a rating_str, use it as bot rating
                bot_rating_override = rating_str
            else:
                # Assume it's a rating (like 1600)
                bot_rating_override = format_or_rating

        # Validate format
        if format_num not in (2, 3):
            await ctx.send("Invalid format. Use 2 or 3 problems per match.")
            return

        pool = get_pool()
        async with pool.acquire() as conn:
            cfg = await dq.get_duel_config(conn, str(ctx.guild.id))

        if opponent_str.lower() == "bot":
            # vs bot
            fallback_rating = await self._get_or_create_duel_rating(ctx.author, ctx.guild, mode)
            bot_rating = botengine.resolve_bot_rating(bot_rating_override, fallback_rating["rating"])
            await self.begin_bot_match(ctx, ctx.author, None, mode, bot_rating, format_num)
        else:
            # vs player
            try:
                opponent = await commands.MemberConverter().convert(ctx, opponent_str)
            except commands.BadArgument:
                await ctx.send(f"Couldn't find player {opponent_str}.")
                return

            if opponent.id == ctx.author.id:
                await ctx.send("You can't duel yourself.")
                return

            challenge_timeout = int(cfg.get("challenge_timeout", 90))
            
            # ✅ PROFESSIONAL CATCHY CHALLENGE EMBED
            em = discord.Embed(
                title="⚔️ Duel Challenge Incoming",
                color=COLOR_CYAN,
            )
            
            em.add_field(
                name="🎯 Match Details",
                value=f"{MODES[mode]['label']} • {format_num}-Problem Format",
                inline=False,
            )
            
            em.add_field(
                name="👤 Challenger",
                value=f"{ctx.author.mention}",
                inline=True,
            )
            
            em.add_field(
                name="🛡️ Opponent",
                value=f"{opponent.mention}",
                inline=True,
            )
            
            # Format description
            if format_num == 2:
                format_desc = "**Medium + Medium** (Bo2)\nQuick, balanced challenge"
            else:
                format_desc = "**Easy + Medium + Hard** (Bo3)\nProgressive difficulty"
            
            em.add_field(
                name="📋 Format",
                value=format_desc,
                inline=False,
            )
            
            em.set_footer(text=f"Only {opponent.name} can accept/decline • Expires in {challenge_timeout}s")
            em.color = COLOR_CYAN
            view = ChallengeView(self, ctx, ctx.author, opponent, mode, challenge_timeout, format_num)  # ✅ Pass format_num
            view.message = await ctx.send(embed=em, view=view)

    @commands.command(name="duelprofile", help="View your or someone's duel ratings.")
    async def duel_profile(self, ctx, user: discord.User = None):
        """!duelprofile or !duelprofile @user"""
        target = user or ctx.author

        pool = get_pool()
        async with pool.acquire() as conn:
            profile = await dq.get_profile(conn, str(target.id), str(ctx.guild.id))

        if not profile:
            await ctx.send(f"{target.name} hasn't participated in any duels yet.")
            return

        em = discord.Embed(
            title=f"Duel Profile: {target.name}",
            description=f"Ratings and statistics across all modes",
            color=COLOR_CYAN,
            timestamp=datetime.now(timezone.utc),
        )

        for row in profile:
            mode = row["mode"]
            rating = row["rating"]
            rank_info = duel_ranks.get_rank(rating)
            w, l, d = row["wins"], row["losses"], row["draws"]
            total = w + l + d
            record = f"{w}W {l}L {d}D" if total > 0 else "No matches"

            badge_text = f"{rank_info['name']} — {rating} points"
            field_value = f"**{badge_text}**\n{record}"
            if row["streak"] != 0:
                streak_type = "Win" if row["streak"] > 0 else "Loss"
                field_value += f"\nStreak: {streak_type} ×{abs(row['streak'])}"

            em.add_field(
                name=f"{MODES[mode]['label']}",
                value=field_value,
                inline=True,
            )

        await ctx.send(embed=em)

    @commands.command(name="duelrank", help="Check your rank/tier in a mode.")
    async def duel_rank(self, ctx, mode: str = None):
        """!duelrank or !duelrank cp_blitz"""
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

        em = discord.Embed(
            title=f"{ctx.author.name}'s Duel Ranks",
            color=COLOR_CYAN,
            timestamp=datetime.now(timezone.utc),
        )

        for row in profile:
            if row["mode"] not in modes_to_check:
                continue
            rating = row["rating"]
            rank_info = duel_ranks.get_rank(rating)
            em.add_field(
                name=f"{MODES[row['mode']]['label']}",
                value=f"**{rank_info['name']}** ({rating} rating)",
                inline=True,
            )

        await ctx.send(embed=em)

    @commands.command(name="duelleaderboard", help="Top duel players in a mode.")
    async def duel_leaderboard(self, ctx, mode: str = "cp_duel"):
        """!duelleaderboard or !duelleaderboard cp_blitz"""
        mode = mode.lower()
        if mode not in MODES:
            await ctx.send(f"Invalid mode: {mode}")
            return

        pool = get_pool()
        async with pool.acquire() as conn:
            rows = await dq.get_leaderboard(conn, str(ctx.guild.id), mode, limit=10)

        em = discord.Embed(
            title=f"Duel Leaderboard: {MODES[mode]['label']}",
            color=COLOR_CYAN,
            timestamp=datetime.now(timezone.utc),
        )

        if not rows:
            em.description = "No players yet."
            await ctx.send(embed=em)
            return

        for i, row in enumerate(rows, 1):
            user = self.bot.get_user(int(row["discord_id"]))
            name = user.display_name if user else f"User {row['discord_id']}"
            rank_info = duel_ranks.get_rank(row["rating"])
            record = f"{row['wins']}W {row['losses']}L {row['draws']}D"
            em.add_field(
                name=f"#{i} {name}",
                value=f"**{rank_info['name']}** ({row['rating']})\n{record}",
                inline=False,
            )

        await ctx.send(embed=em)

    async def _get_or_create_duel_rating(self, user: discord.User, guild: discord.Guild, mode: str):
        """Get or create a duel rating for a user in a mode."""
        pool = get_pool()
        async with pool.acquire() as conn:
            return await dq.get_or_create_rating(conn, str(user.id), str(guild.id), mode)

    async def begin_human_match(self, ctx, challenger: discord.Member, opponent: discord.Member, mode: str, format_num: int = 3):  # ✅ Add format_num
        """Start a duel between two players."""
        try:
            pool = get_pool()
            async with pool.acquire() as conn:
                cfg = await dq.get_duel_config(conn, str(ctx.guild.id))
                p1_rating = await dq.get_or_create_rating(conn, str(challenger.id), str(ctx.guild.id), mode)
                p2_rating = await dq.get_or_create_rating(conn, str(opponent.id), str(ctx.guild.id), mode)

            # Create duel record
            total_games = format_num  # ✅ Use format_num instead of MODES[mode]["games"]
            async with pool.acquire() as conn:
                duel_id = await dq.create_duel(
                    conn,
                    str(ctx.guild.id),
                    mode,
                    str(challenger.id),
                    str(opponent.id),
                    is_bot_match=False,
                    bot_rating=None,
                    total_games=total_games,
                )
                
                # Allocate duel number (reusable slot system)
                used = await dq.get_active_duel_numbers(conn, str(ctx.guild.id))
                duel_num = dq.next_free_number(used)
                
                # Create private channel
                channel = await self._create_duel_channel(ctx, challenger, opponent, mode, duel_num)
                
                # Activate
                await dq.activate_duel(conn, duel_id, str(channel.id))

                # Fetch problems
                await self._setup_problems(conn, duel_id, mode, p1_rating["rating"], p2_rating["rating"], cfg, format_num=format_num)  # ✅ Pass format_num

            # Start first game
            await self._start_game(channel, duel_id, mode, cfg)
        except Exception as e:
            print(f"[DUEL ERROR] begin_human_match failed: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            try:
                await ctx.channel.send(f"❌ Match setup failed: {str(e)}")
            except Exception:
                pass

    async def begin_bot_match(self, ctx, challenger: discord.Member, opponent: discord.Member | None, mode: str, bot_rating: int = None, format_num: int = 3):
        """Start a duel between a player and the bot."""
        pool = get_pool()
        async with pool.acquire() as conn:
            cfg = await dq.get_duel_config(conn, str(ctx.guild.id))
            p1_rating = await dq.get_or_create_rating(conn, str(challenger.id), str(ctx.guild.id), mode)

        if bot_rating is None:
            bot_rating = p1_rating["rating"]

        # Gating for ICPC
        if mode.startswith("icpc"):
            # ICPC mode requires CF rating check
            # Note: Simplified check without requiring get_handles function
            # In production, you would fetch user's CF handle and check their rating
            # For now, we'll allow users to proceed and the bot will warn if needed
            pass

        # Create duel (use format_num instead of default MODES[mode]["games"])
        total_games = format_num
        async with pool.acquire() as conn:
            duel_id = await dq.create_duel(
                conn,
                str(ctx.guild.id),
                mode,
                str(challenger.id),
                None,
                is_bot_match=True,
                bot_rating=bot_rating,
                total_games=total_games,
            )

            # Allocate duel number
            used = await dq.get_active_duel_numbers(conn, str(ctx.guild.id))
            duel_num = dq.next_free_number(used)

            # Create channel
            channel = await self._create_duel_channel(ctx, challenger, None, mode, duel_num, is_bot=True)

            # Activate
            await dq.activate_duel(conn, duel_id, str(channel.id))

            # Fetch problems (bot as opponent for difficulty)
            await self._setup_problems(conn, duel_id, mode, p1_rating["rating"], bot_rating, cfg, is_bot=True, format_num=format_num)  # ✅ Pass format_num

        # Start first game
        await self._start_game(channel, duel_id, mode, cfg, is_bot=True)

    async def _create_duel_channel(self, ctx, player1: discord.Member, player2: discord.Member | None, mode: str, duel_number: int, is_bot: bool = False) -> discord.TextChannel:
        """Create a private duel channel with proper naming and permissions."""
        family = MODES[mode]["family"]
        if is_bot:
            channel_name = f"{family}-duel-{duel_number}-{player1.name}-vs-bot"
        else:
            channel_name = f"{family}-duel-{duel_number}-{player1.name}-vs-{player2.name}"

        channel_name = channel_name.lower().replace(" ", "-")[:100]

        # Find or create Duels category
        guild = ctx.guild
        category = discord.utils.get(guild.categories, name="Duels")
        if not category:
            # ✅ Create category with proper permissions
            category = await guild.create_category("Duels")
            print(f"[DUEL] ✅ Created 'Duels' category")

        # Get fresh member objects
        p1 = guild.get_member(player1.id) or player1
        p2 = guild.get_member(player2.id) if player2 else None

        # ✅ Build complete permission overwrites BEFORE creating channel
        # STRICT: Only the 2 players can view this channel
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                read_messages=False,
                send_messages=False,
                view_channel=False
            ),
            p1: discord.PermissionOverwrite(
                read_messages=True,
                send_messages=True,
                view_channel=True
            ),
            self.bot.user: discord.PermissionOverwrite(
                read_messages=True,
                send_messages=True,
                view_channel=True,
                embed_links=True,
                attach_files=True,
                manage_messages=True
            ),
        }
        
        if p2:
            overwrites[p2] = discord.PermissionOverwrite(
                read_messages=True,
                send_messages=True,
                view_channel=True
            )
        
        # ✅ NO admin/mod access - completely private to the 2 players
        # Remove this section to keep it private:
        # admin_role = discord.utils.get(guild.roles, name=ADMIN_ROLE)
        # if admin_role:
        #     overwrites[admin_role] = discord.PermissionOverwrite(...)


        try:
            # Create channel with complete permissions at once
            channel = await guild.create_text_channel(
                channel_name,
                category=category,
                overwrites=overwrites,
                reason=f"Duel: {p1.name} vs {p2.name if p2 else 'Bot'}"
            )
            
            # ✅ Set professional channel topic
            mode_label = MODES.get(mode, {}).get('label', mode.upper())
            if is_bot:
                topic = f"🤖 {mode_label} • {p1.name} vs Bot"
            else:
                topic = f"⚔️ {mode_label} • {p1.name} vs {p2.name if p2 else 'Bot'}"
            
            await channel.edit(topic=topic)
            
            # Small delay to ensure Discord processes permissions
            await asyncio.sleep(0.5)
            
            print(f"[DUEL] ✅ Channel created: {channel.name} | Topic: {topic}")
            return channel
        except discord.errors.Forbidden as e:
            print(f"[DUEL ERROR] Bot cannot create channel - missing permissions: {e}")
            raise
        except Exception as e:
            print(f"[DUEL ERROR] Failed to create channel: {e}")
            raise

    async def _setup_problems(self, conn, duel_id: int, mode: str, p1_rating: int, p2_rating: int, cfg: dict, is_bot: bool = False, format_num: int = 3):  # ✅ Add format_num
        """Fetch all problems for the duel."""
        duel = await dq.get_duel(conn, duel_id)
        games_needed = format_num  # ✅ Use format_num instead of MODES[mode]["games"]
        platform = MODES[mode]["platform"]
        icpc = MODES[mode]["icpc"]

        pair_key = dq.pair_key(duel["player1_id"], duel["player2_id"])
        pair_history = await dq.get_pair_history(conn, pair_key, platform)

        if platform == "cf":
            if mode.startswith("icpc"):
                min_rating = int(cfg.get("icpc_min", 1600))
                max_rating = int(cfg.get("icpc_max", 3500))
            else:
                offset = int(cfg.get("cp_offset", 150))
                avg = (p1_rating + p2_rating) // 2
                min_rating = max(800, avg - offset)
                max_rating = min(3500, avg + offset)

            problems = await pick_many_cf_problems(min_rating, max_rating, pair_history, games_needed, icpc=icpc)
        else:  # LC
            if mode.endswith("blitz"):
                problems = [await pick_lc_problem("medium", pair_history)]
            else:
                problems = await pick_sequence(games_needed, pair_history)  # ✅ Pass format_num to pick_sequence

        time_limit_key = "duel_game_time_limit" if games_needed > 1 else "blitz_time_limit"
        time_limit = int(cfg.get(time_limit_key, 1200))

        for i, prob in enumerate(problems, 1):
            await dq.add_duel_problem(
                conn,
                duel_id,
                i,
                platform,
                prob["problem_id"],
                prob["title"],
                prob.get("difficulty_label") or prob.get("difficulty", "medium"),
                prob.get("rating"),
                prob["url"],
                datetime.now(timezone.utc) + timedelta(seconds=time_limit),
                topic=prob.get("topic"),
            )
            await dq.add_pair_history(conn, pair_key, platform, prob["problem_id"])

    async def _start_game(self, channel: discord.TextChannel, duel_id: int, mode: str, cfg: dict, is_bot: bool = False):
        """Start a game in the duel."""
        pool = get_pool()
        async with pool.acquire() as conn:
            duel = await dq.get_duel(conn, duel_id)
            prob = await dq.get_current_problem(conn, duel_id, duel["current_game"])

        if not prob:
            await channel.send("Error: No problem found for this game.")
            return

        p1_id = duel["player1_id"]
        p2_id = duel["player2_id"]

        # Get user objects and ratings
        p1 = self.bot.get_user(int(p1_id))
        p2 = self.bot.get_user(int(p2_id)) if p2_id else None

        p1_rating = await self._get_or_create_duel_rating(p1, channel.guild, mode)
        p2_rating = await self._get_or_create_duel_rating(p2, channel.guild, mode) if p2 else {"rating": duel["bot_rating"]}

        p1_name = p1.display_name if p1 else f"Player 1"
        p2_name = p2.display_name if p2 else "Binary Bot"

        p1_rank = duel_ranks.get_rank(p1_rating["rating"])
        p2_rank = duel_ranks.get_rank(p2_rating["rating"])

        # Round intro embed - PROFESSIONAL DESIGN
        em = discord.Embed(
            title=f"⏱️ Game {duel['current_game']}/{duel['total_games']}",
            description=f"**{MODES[mode]['label']}** Match",
            color=COLOR_CYAN,
        )
        
        # Match info
        em.add_field(
            name="🎯 Players",
            value=f"{p1_name} vs {p2_name}",
            inline=False,
        )
        
        # Ratings side by side
        em.add_field(
            name="📊 Ratings",
            value=f"**{p1_rank['name']}** ({p1_rating['rating']}) vs **{p2_rank['name']}** ({p2_rating['rating']})",
            inline=False,
        )
        
        # Problem section
        em.add_field(
            name="📌 Problem",
            value=f"[{prob['title']}]({prob['url']})",
            inline=False,
        )
        
        # Metadata side by side
        difficulty = prob.get("difficulty_label") or str(prob.get("rating"))
        em.add_field(
            name="🔹 Difficulty",
            value=difficulty,
            inline=True,
        )
        
        time_limit = int(cfg.get("duel_game_time_limit" if MODES[mode]["games"] > 1 else "blitz_time_limit", 1200))
        em.add_field(
            name="⏱️ Time Limit",
            value=_fmt_secs(time_limit),
            inline=True,
        )
        
        if prob.get("topic"):
            em.add_field(
                name="🏷️ Tags",
                value=prob["topic"],
                inline=False,
            )
        
        em.set_footer(text="Click 'Check Submissions' when you're done to verify your solve")
        em.color = COLOR_CYAN

        # Post embed with checker button AND forfeit button
        msg = await channel.send(embed=em, view=SubmissionCheckerView(self, duel_id))
        
        # Also send a message with forfeit option
        forfeit_msg = await channel.send(
            "⚠️ You can forfeit at any time using the button below (you'll lose 32 rating points):",
            view=ForfeitView(self, duel_id)
        )

        # Countdown
        countdown_secs = int(cfg.get("countdown_seconds", 3))
        for i in range(countdown_secs, 0, -1):
            await asyncio.sleep(1)
            try:
                await msg.edit(content=f"**{i}**")
            except Exception:
                pass

        # GO!
        try:
            await msg.edit(content="**GO!**")
        except Exception:
            pass

        # Update deadline to now + time_limit
        async with pool.acquire() as conn:
            deadline = datetime.now(timezone.utc) + timedelta(seconds=time_limit)
            await conn.execute(
                "UPDATE duel_problems SET deadline_at = $1 WHERE id = $2",
                deadline, prob["id"],
            )

        # If bot match, simulate bot's solve
        if is_bot:
            await self._simulate_bot_solve(channel, duel_id, prob, time_limit, duel["bot_rating"])

    async def _simulate_bot_solve(self, channel: discord.TextChannel, duel_id: int, prob: dict, time_limit: int, bot_rating: int):
        """Simulate bot's solve in background."""
        platform = prob["platform"]
        if platform == "cf":
            prob_rating = prob.get("rating") or 1500
            solved, solve_time = botengine.simulate_attempt(prob_rating, bot_rating, time_limit)
        else:  # LC
            difficulty = prob.get("difficulty_label", "medium").lower()
            solved, solve_time = botengine.simulate_lc_game(difficulty, bot_rating, time_limit)

        if solve_time is not None:
            await asyncio.sleep(solve_time)

            pool = get_pool()
            async with pool.acquire() as conn:
                await dq.mark_solved(conn, prob["id"], "p2", datetime.now(timezone.utc))
                await channel.send(f"Bot solved in {_fmt_secs(solve_time)}")

    async def offer_bot_fallback(self, channel: discord.TextChannel, challenger: discord.Member, mode: str):
        """Show 'Play vs Bot' option after decline/timeout."""
        em = discord.Embed(
            title="🤖 No Opponent Available",
            description="The Binary Bot is always ready to duel!",
            color=COLOR_CYAN,
        )
        em.add_field(
            name="📌 Challenge Status",
            value="Declined or expired",
            inline=False,
        )
        em.add_field(
            name="🎯 Mode",
            value=MODES.get(mode, {}).get('label', mode.upper()),
            inline=True,
        )
        em.set_footer(text="Click below to challenge the bot instead")
        await channel.send(embed=em, view=BotFallbackView(self, challenger, mode))

    async def _resolve_game(self, conn, duel: dict, prob: dict, channel: discord.TextChannel) -> bool:
        """Check submissions and determine game winner. Returns True if match ends."""
        p1_id = duel["player1_id"]
        p2_id = duel["player2_id"]
        is_bot = duel["is_bot_match"]

        # Check submissions
        p1_solved = await self._check_side(conn, p1_id, prob)
        p2_solved = None if not p2_id and not is_bot else await self._check_side(conn, p2_id or "BOT", prob)

        # Determine winner
        if p1_solved and (not p2_solved):
            winner = "p1"
        elif p2_solved and (not p1_solved):
            winner = "p2"
        elif p1_solved and p2_solved:
            # ✅ Safe comparison with None checks
            p1_time = prob.get("p1_solved_at")
            p2_time = prob.get("p2_solved_at")
            if p1_time and p2_time and p1_time < p2_time:
                winner = "p1"
            elif p2_time and p1_time and p2_time < p1_time:
                winner = "p2"
            else:
                winner = "draw"  # Fallback if times are same or None
        else:
            winner = "draw"

        # Record
        await dq.set_game_winner(conn, prob["id"], winner)
        await dq.bump_game_score(conn, duel["id"], winner)

        # Check if match is over
        updated_duel = await dq.get_duel(conn, duel["id"])
        match_over = updated_duel["p1_games_won"] >= (duel["total_games"] + 1) // 2 or updated_duel["p2_games_won"] >= (duel["total_games"] + 1) // 2

        if match_over:
            if updated_duel["p1_games_won"] > updated_duel["p2_games_won"]:
                final_winner = p1_id if not is_bot else p1_id
            elif updated_duel["p2_games_won"] > updated_duel["p1_games_won"]:
                final_winner = p2_id if not is_bot else "BOT"
            else:
                final_winner = None

            await dq.finish_duel(conn, duel["id"], final_winner)
            await self._finish_match(channel, duel, updated_duel)
            return True
        else:
            await channel.send(f"Game {duel['current_game']} finished — next game starts shortly.")
            return False

    async def _check_side(self, conn, user_or_bot: str, prob: dict) -> bool:
        """Check if a user/bot solved the problem."""
        if user_or_bot == "BOT":
            return prob.get("p2_solved_at") is not None

        # Get user's handle for the platform
        platform = prob["platform"]
        handle = await dq.get_handle_for_user(conn, user_or_bot, platform)
        if not handle:
            return False

        problem_id = prob["problem_id"]

        if platform == "cf":
            adapter = P.CodeforcesAdapter()
            subs = await adapter.fetch_all_submissions(handle)
            return any(s.get("verdict") == "OK" and s.get("problem", {}).get("index") in problem_id for s in subs)
        else:  # LC
            adapter = P.LeetCodeAdapter()
            subs = await adapter.get_recent_submissions(handle)
            return any(s["titleSlug"] == problem_id for s in subs)

    async def _finish_match(self, channel: discord.TextChannel, duel: dict, updated_duel: dict):
        """Post final result embed with complete stats and rating changes."""
        pool = get_pool()
        
        # Get player info and ratings
        p1_id = duel["player1_id"]
        p2_id = duel["player2_id"]
        mode = duel["mode"]
        
        p1 = self.bot.get_user(int(p1_id))
        p2 = self.bot.get_user(int(p2_id)) if p2_id else None
        
        p1_name = p1.display_name if p1 else f"Player {p1_id}"
        p2_name = p2.display_name if p2 else "Binary Bot"
        
        # Fetch updated ratings from database
        async with pool.acquire() as conn:
            p1_new_rating = await dq.get_or_create_rating(conn, str(p1_id), str(channel.guild.id), mode)
            p2_new_rating = await dq.get_or_create_rating(conn, str(p2_id) if p2_id else "BOT", str(channel.guild.id), mode) if p2_id else None
        
        # Calculate rating changes
        p1_old_rating = duel["p1_rating"]
        p1_delta = p1_new_rating["rating"] - p1_old_rating
        
        if p2_new_rating:
            p2_old_rating = duel["p2_rating"]
            p2_delta = p2_new_rating["rating"] - p2_old_rating
        else:
            p2_delta = 0
        
        # Determine winner
        if updated_duel['p1_games_won'] > updated_duel['p2_games_won']:
            winner_name = p1_name
            winner_color = 0x00FF00  # Green for winner
        elif updated_duel['p2_games_won'] > updated_duel['p1_games_won']:
            winner_name = p2_name
            winner_color = 0x00FF00
        else:
            winner_name = "Draw"
            winner_color = COLOR_CYAN
        
        # ✅ Main results embed
        em = discord.Embed(
            title="🏆 Match Complete",
            description=f"{MODES[mode]['label']} • Match #{duel.get('duel_number', '?')}",
            color=winner_color if winner_name != "Draw" else COLOR_CYAN,
        )

        # Final score
        em.add_field(
            name="📊 Final Score",
            value=f"**{p1_name}** {updated_duel['p1_games_won']} — {updated_duel['p2_games_won']} **{p2_name}**",
            inline=False,
        )

        # Winner
        if winner_name != "Draw":
            em.add_field(
                name="🥇 Winner",
                value=f"**{winner_name}**",
                inline=True,
            )
        else:
            em.add_field(
                name="🤝 Result",
                value="**Draw**",
                inline=True,
            )

        em.add_field(
            name="📋 Format",
            value=f"{duel['total_games']}-Problem Match",
            inline=True,
        )

        # ✅ RATINGS SECTION
        em.add_field(
            name=f"📈 {p1_name}'s Rating",
            value=f"**{p1_old_rating}** → **{p1_new_rating['rating']}** ({p1_delta:+d})",
            inline=True,
        )

        if p2_new_rating:
            em.add_field(
                name=f"📈 {p2_name}'s Rating",
                value=f"**{p2_old_rating}** → **{p2_new_rating['rating']}** ({p2_delta:+d})",
                inline=True,
            )

        # Match statistics
        em.add_field(
            name="⏰ Duration",
            value=f"{duel['started_at']} to {datetime.now(timezone.utc).strftime('%H:%M:%S')}",
            inline=False,
        )

        em.add_field(
            name="🎮 Mode",
            value=MODES[mode]['label'],
            inline=True,
        )

        # Rank badges
        p1_rank = duel_ranks.get_rank(p1_new_rating["rating"])
        em.add_field(
            name="🏅 Rank",
            value=f"{p1_rank['name']}",
            inline=True,
        )

        em.set_footer(text="Channel will be archived in 30 seconds")
        em.color = winner_color if winner_name != "Draw" else COLOR_CYAN

        await channel.send(embed=em)
        
        # ✅ SEND STATS TO PUBLIC MODE CHANNEL (cp-blitz, dsa-duel, etc.)
        try:
            mode_channel_name = f"{duel['mode']}"  # cp-blitz, dsa-duel, etc.
            guild = channel.guild
            mode_channel = discord.utils.get(guild.text_channels, name=mode_channel_name)
            
            if mode_channel:
                # Public stats announcement
                public_stats = discord.Embed(
                    title="🏆 Match Result",
                    description=f"{MODES[mode]['label']} • Match #{duel.get('duel_number', '?')}",
                    color=winner_color if winner_name != "Draw" else COLOR_CYAN,
                )
                
                public_stats.add_field(
                    name="👥 Players",
                    value=f"{p1_name} vs {p2_name}",
                    inline=False,
                )
                
                public_stats.add_field(
                    name="📊 Result",
                    value=f"**{winner_name}** wins {updated_duel['p1_games_won']}-{updated_duel['p2_games_won']}" if winner_name != "Draw" else f"**Draw** {updated_duel['p1_games_won']}-{updated_duel['p2_games_won']}",
                    inline=False,
                )
                
                public_stats.add_field(
                    name="📈 Rating Changes",
                    value=f"{p1_name}: {p1_delta:+d} ({p1_old_rating}→{p1_new_rating['rating']})\n{p2_name}: {p2_delta:+d} ({p2_old_rating}→{p2_new_rating['rating']})" if p2_new_rating else f"{p1_name}: {p1_delta:+d} ({p1_old_rating}→{p1_new_rating['rating']})",
                    inline=False,
                )
                
                public_stats.set_footer(text=f"Match completed at {datetime.now(timezone.utc).strftime('%H:%M:%S')}")
                
                await mode_channel.send(embed=public_stats)
                print(f"[DUEL] ✅ Stats posted to {mode_channel_name}")
        except Exception as e:
            print(f"[DUEL] Could not post stats to mode channel: {e}")
        
        # ✅ Send brief summary to general channel
        try:
            guild = channel.guild
            main_channel = discord.utils.get(guild.text_channels, name="general") or guild.text_channels[0]
            
            summary = discord.Embed(
                title="✅ Duel Match Finished",
                description=f"{p1_name} vs {p2_name}",
                color=winner_color if winner_name != "Draw" else COLOR_CYAN,
            )
            summary.add_field(name="Result", value=f"**{winner_name}** wins {updated_duel['p1_games_won']}-{updated_duel['p2_games_won']}" if winner_name != "Draw" else f"**Draw** {updated_duel['p1_games_won']}-{updated_duel['p2_games_won']}", inline=False)
            summary.add_field(name="Mode", value=MODES[mode]['label'], inline=True)
            summary.add_field(name="Rating Change", value=f"{p1_name}: {p1_delta:+d} | {p2_name}: {p2_delta:+d}" if p2_new_rating else f"{p1_name}: {p1_delta:+d}", inline=True)
            
            await main_channel.send(embed=summary)
        except Exception as e:
            print(f"[DUEL] Could not send summary to main channel: {e}")
        
        await asyncio.sleep(30)
        await channel.delete(reason="Duel finished")

    @tasks.loop(minutes=0.75)
    async def auto_check(self):
        """Background loop: check active duels every ~45 seconds."""
        pool = get_pool()
        async with pool.acquire() as conn:
            duels = await dq.get_all_active_duels(conn)

        for duel in duels:
            try:
                channel = self.bot.get_channel(int(duel["channel_id"]))
                if not channel:
                    continue

                async with pool.acquire() as conn:
                    prob = await dq.get_current_problem(conn, duel["id"], duel["current_game"])
                    if prob and datetime.now(timezone.utc) > prob["deadline_at"]:
                        await self._resolve_game(conn, duel, prob, channel)
            except Exception as e:
                print(f"Auto-check error: {e}")

    @auto_check.before_loop
    async def before_auto_check(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Duels(bot))