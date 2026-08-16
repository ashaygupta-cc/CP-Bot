"""
database/duel_queries.py
All DB access for the duel system. Mirrors the style of database/queries.py
(asyncpg, plain `conn` passed in, guild-scoped where relevant).
"""

from datetime import datetime, timezone

# ── Config ────────────────────────────────────────────────────────────────
# Per-guild key/value settings. Defaults live here so the bot works even
# before any admin runs !duelconfig.

DEFAULT_CONFIG = {
    "cp_blitz_k":        "24",
    "cp_duel_k":         "32",
    "icpc_k":            "40",
    "icpc_min":          "1600",
    "icpc_max":          "3500",
    "cp_offset":         "150",     # problem rating = avg(player ratings) ± this band
    "lc_blitz_easy_win": "8",   "lc_blitz_easy_loss": "4",
    "lc_blitz_med_win":  "12",  "lc_blitz_med_loss":  "6",
    "lc_blitz_hard_win": "20",  "lc_blitz_hard_loss": "10",
    "lc_duel_win":       "22",  "lc_duel_loss":       "12",   # Bo3: easy+medium+hard
    "bot_affects_rating": "false",   # practice-vs-bot matches don't move ranked rating by default
    "challenge_timeout":  "90",      # seconds to accept/decline before "play vs bot" offer appears
    "blitz_time_limit":   "1200",    # 20 min
    "duel_game_time_limit": "1500",  # 25 min per game
    "icpc_time_limit":     "2100",   # 35 min
    "min_icpc_cp_rating":  "1400",
    "countdown_seconds":   "3",      # 3-2-1-GO countdown length before each round's timer starts
    "spectator_role":      "",       # optional role name allowed to VIEW (not play) active duel channels
}


async def get_handle_for_user(conn, discord_id: str, platform: str) -> str | None:
    """Get a user's handle for a specific platform (cf, lc, cc, atcoder)."""
    row = await conn.fetchrow(
        "SELECT handle FROM handles WHERE discord_id = $1 AND platform = $2 LIMIT 1",
        str(discord_id), platform
    )
    return row["handle"] if row else None


async def get_duel_config(conn, guild_id: str) -> dict:
    rows = await conn.fetch("SELECT key, value FROM duel_config WHERE guild_id = $1", guild_id)
    cfg = dict(DEFAULT_CONFIG)
    cfg.update({r["key"]: r["value"] for r in rows})
    return cfg


async def set_duel_config(conn, guild_id: str, key: str, value: str, updated_by: str):
    await conn.execute(
        """
        INSERT INTO duel_config (guild_id, key, value, updated_by, updated_at)
        VALUES ($1, $2, $3, $4, NOW())
        ON CONFLICT (guild_id, key) DO UPDATE
            SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by, updated_at = NOW()
        """,
        guild_id, key, str(value), updated_by,
    )


# ── Ratings ──────────────────────────────────────────────────────────────

async def get_or_create_rating(conn, discord_id: str, guild_id: str, mode: str, default: int = 800) -> dict:
    """Get or create a duel rating for a player. Default rating is 800 (Newbie)."""
    if not discord_id:
        return {"discord_id": "", "guild_id": guild_id, "mode": mode, "rating": default, "wins": 0, "losses": 0, "draws": 0}
    discord_id = str(discord_id)
    await ensure_user_exists(conn, discord_id)

    row = await conn.fetchrow(
        "SELECT * FROM duel_ratings WHERE discord_id=$1 AND guild_id=$2 AND mode=$3",
        discord_id, guild_id, mode,
    )
    if row:
        return dict(row)
    await conn.execute(
        """
        INSERT INTO duel_ratings (discord_id, guild_id, mode, rating)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (discord_id, guild_id, mode) DO NOTHING
        """,
        discord_id, guild_id, mode, default,
    )
    row = await conn.fetchrow(
        "SELECT * FROM duel_ratings WHERE discord_id=$1 AND guild_id=$2 AND mode=$3",
        discord_id, guild_id, mode,
    )
    return dict(row)


async def set_rating(conn, discord_id: str, guild_id: str, mode: str, new_rating: int):
    """Admin override: set a player's duel rating directly."""
    await conn.execute(
        """
        UPDATE duel_ratings
        SET rating = $1, updated_at = NOW()
        WHERE discord_id=$2 AND guild_id=$3 AND mode=$4
        """,
        new_rating, discord_id, guild_id, mode,
    )


async def apply_rating_delta(
    conn, discord_id: str, guild_id: str, mode: str,
    delta: int, result: str,  # 'win' | 'loss' | 'draw'
    is_bot_match: bool = False,
):
    """Applies a rating delta and updates W/L/D + streak in one row."""
    row = await get_or_create_rating(conn, discord_id, guild_id, mode)
    streak = row["streak"]
    if result == "win":
        streak = streak + 1 if streak >= 0 else 1
    elif result == "loss":
        streak = streak - 1 if streak <= 0 else -1
    else:
        streak = 0

    await conn.execute(
        f"""
        UPDATE duel_ratings
        SET rating = rating + $1,
            wins = wins + {1 if result == "win" else 0},
            losses = losses + {1 if result == "loss" else 0},
            draws = draws + {1 if result == "draw" else 0},
            streak = $2,
            bot_matches = bot_matches + {1 if is_bot_match else 0},
            updated_at = NOW()
        WHERE discord_id=$3 AND guild_id=$4 AND mode=$5
        """,
        delta, streak, discord_id, guild_id, mode,
    )


async def get_profile(conn, discord_id: str, guild_id: str) -> list:
    """Get all duel mode ratings for a player."""
    rows = await conn.fetch(
        "SELECT * FROM duel_ratings WHERE discord_id=$1 AND guild_id=$2 ORDER BY mode",
        discord_id, guild_id,
    )
    return [dict(r) for r in rows]


async def get_leaderboard(conn, guild_id: str, mode: str, limit: int = 10) -> list:
    """Get top N players by rating in a mode."""
    rows = await conn.fetch(
        """
        SELECT discord_id, rating, wins, losses, draws, streak
        FROM duel_ratings
        WHERE guild_id=$1 AND mode=$2
        ORDER BY rating DESC
        LIMIT $3
        """,
        guild_id, mode, limit,
    )
    return [dict(r) for r in rows]


# ── Duels (match lifecycle) ─────────────────────────────────────────────

async def create_duel(
    conn, guild_id: str, mode: str, player1_id: str,
    player2_id: str | None, is_bot_match: bool, bot_rating: int | None,
    total_games: int, duel_number: int | None = None,
) -> int:
    await ensure_user_exists(conn, player1_id)
    if player2_id and not is_bot_match:
        await ensure_user_exists(conn, player2_id)

    return await conn.fetchval(
        """
        INSERT INTO duels (guild_id, mode, player1_id, player2_id, is_bot_match,
                            bot_rating, total_games, duel_number, status)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'pending')
        RETURNING id
        """,
        guild_id, mode, player1_id, player2_id, is_bot_match, bot_rating, total_games, duel_number,
    )


async def get_active_duel_numbers(conn, guild_id: str) -> set:
    """Numbers currently 'in use' (pending or active) for this guild — used
    to compute the lowest free slot instead of a monotonically increasing
    counter, so numbers get reused as soon as a match finishes/cancels."""
    rows = await conn.fetch(
        "SELECT duel_number FROM duels WHERE guild_id=$1 AND status IN ('pending','active') AND duel_number IS NOT NULL",
        guild_id,
    )
    return {r["duel_number"] for r in rows}


def next_free_number(used: set) -> int:
    """Find the smallest positive integer not in the used set."""
    n = 1
    while n in used:
        n += 1
    return n


async def get_duel(conn, duel_id: int) -> dict | None:
    row = await conn.fetchrow("SELECT * FROM duels WHERE id=$1", duel_id)
    return dict(row) if row else None


async def get_active_duel_for_channel(conn, channel_id: str) -> dict | None:
    row = await conn.fetchrow(
        "SELECT * FROM duels WHERE channel_id=$1 AND status='active'", str(channel_id)
    )
    return dict(row) if row else None


async def get_all_active_duels(conn) -> list:
    """Get all currently active duels across the entire bot."""
    rows = await conn.fetch("SELECT * FROM duels WHERE status='active'")
    return [dict(r) for r in rows]


async def activate_duel(conn, duel_id: int, channel_id: str):
    await conn.execute(
        "UPDATE duels SET status='active', channel_id=$1, started_at=NOW() WHERE id=$2",
        str(channel_id), duel_id,
    )


async def set_duel_status(conn, duel_id: int, status: str):
    await conn.execute("UPDATE duels SET status=$1 WHERE id=$2", status, duel_id)


async def finish_duel(conn, duel_id: int, winner_id: str | None):
    await conn.execute(
        "UPDATE duels SET status='finished', winner_id=$1, ended_at=NOW() WHERE id=$2",
        winner_id, duel_id,
    )


async def bump_game_score(conn, duel_id: int, winner_side: str):
    """winner_side: 'p1' | 'p2' | 'draw'"""
    if winner_side == "p1":
        await conn.execute("UPDATE duels SET p1_games_won = p1_games_won + 1, current_game = current_game + 1 WHERE id=$1", duel_id)
    elif winner_side == "p2":
        await conn.execute("UPDATE duels SET p2_games_won = p2_games_won + 1, current_game = current_game + 1 WHERE id=$1", duel_id)
    else:
        await conn.execute("UPDATE duels SET current_game = current_game + 1 WHERE id=$1", duel_id)


# ── Duel problems (per-game) ─────────────────────────────────────────────

async def add_duel_problem(
    conn, duel_id: int, game_number: int, platform: str, problem_id: str,
    title: str, difficulty_label: str, rating: int | None, url: str, deadline_at,
    topic: str | None = None,
) -> int:
    return await conn.fetchval(
        """
        INSERT INTO duel_problems (duel_id, game_number, platform, problem_id, title,
                                    difficulty_label, rating, url, deadline_at, topic)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
        RETURNING id
        """,
        duel_id, game_number, platform, problem_id, title, difficulty_label, rating, url, deadline_at, topic,
    )


async def get_current_problem(conn, duel_id: int, game_number: int) -> dict | None:
    row = await conn.fetchrow(
        "SELECT * FROM duel_problems WHERE duel_id=$1 AND game_number=$2",
        duel_id, game_number,
    )
    return dict(row) if row else None


async def mark_solved(conn, duel_problem_id: int, side: str, solved_at=None):
    """Mark one side as solved. side: 'p1' | 'p2' | 'bot'"""
    solved_at = solved_at or datetime.now(timezone.utc)
    col = "p1_solved_at" if side == "p1" else "p2_solved_at"
    await conn.execute(f"UPDATE duel_problems SET {col}=$1 WHERE id=$2", solved_at, duel_problem_id)


async def set_game_winner(conn, duel_problem_id: int, winner_side: str):
    """Set the winner of a game. winner_side: discord_id, 'BOT', 'DRAW'"""
    await conn.execute("UPDATE duel_problems SET game_winner=$1 WHERE id=$2", winner_side, duel_problem_id)


# ── Repeat-avoidance history ─────────────────────────────────────────────

def pair_key(id_a: str, id_b: str | None) -> str:
    """Generate a canonical pair key for repeat-avoidance tracking."""
    if id_b is None:
        return f"BOT_{id_a}"
    a, b = sorted([str(id_a), str(id_b)])
    return f"{a}_{b}"


async def get_pair_history(conn, pkey: str, platform: str) -> set:
    """Get the set of problems this pair has already played together."""
    rows = await conn.fetch(
        "SELECT problem_id FROM duel_problem_history WHERE pair_key=$1 AND platform=$2",
        pkey, platform,
    )
    return {r["problem_id"] for r in rows}


async def add_pair_history(conn, pkey: str, platform: str, problem_id: str):
    """Add a problem to the pair's history (weighted repeat-avoidance)."""
    await conn.execute(
        """
        INSERT INTO duel_problem_history (pair_key, platform, problem_id)
        VALUES ($1,$2,$3)
        ON CONFLICT (pair_key, platform, problem_id) DO UPDATE SET used_at = NOW()
        """,
        pkey, platform, problem_id,
    )


# ── History / profile listing ────────────────────────────────────────────

async def get_match_history(conn, discord_id: str, guild_id: str, limit: int = 10) -> list:
    """Get last N finished matches for a player."""
    rows = await conn.fetch(
        """
        SELECT * FROM duels
        WHERE guild_id=$1 AND status='finished'
          AND (player1_id=$2 OR player2_id=$2)
        ORDER BY ended_at DESC
        LIMIT $3
        """,
        guild_id, discord_id, limit,
    )
    return [dict(r) for r in rows]