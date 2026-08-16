"""
duel_bot_engine.py — The "always-available" bot opponent.

Goal: if no human opponent is around, the bot fills in at ANY rating range
(newbie → legendary grandmaster) and behaves like a real player of that
rating would — not a coin flip. Two things are modeled per problem:

  1. WHETHER the bot solves it at all — an Elo-style logistic curve
     against the rating gap between the problem and the bot, same shape
     CF/Lichess-style ratings use for win probability.
  2. WHEN it solves it (if it does) — sampled from a Beta distribution
     whose skew depends on that same gap: comfortably-rated problems get
     solved early, problems near/above the bot's level get solved late
     (close to the time limit) or not at all.

This makes a "1400 bot" plausible against a 1400 problem (~coin-flip,
solves around the middle of the clock) and a "2400 bot" look strong
against a 1400 problem (near-certain, solved fast) — same as a real
player would.
"""

import random

# CF-style rating tiers used both to label the bot ("Expert bot") and to
# resolve a typed tier name ("expert") into a numeric rating.
TIERS: dict[str, int] = {
    "newbie": 1000,
    "pupil": 1300,
    "specialist": 1500,
    "expert": 1700,
    "candidate_master": 1950,
    "master": 2150,
    "international_master": 2300,
    "grandmaster": 2450,
    "international_grandmaster": 2650,
    "legendary_grandmaster": 3000,
}

# Approximate CF-scale equivalents for LC difficulties, used only to drive
# the same logistic/solve-time model for dsa_* modes. Not a claim that LC
# and CF ratings are equivalent — just a shared internal scale for the sim.
LC_DIFFICULTY_RATING = {"easy": 1250, "medium": 1700, "hard": 2200}


def resolve_bot_rating(token: str | None, fallback: int) -> int:
    """Turns '1800', 'expert', 'Grandmaster', None, etc. into a clamped int rating."""
    if not token:
        return fallback
    key = token.strip().lower().replace(" ", "_")
    if key in TIERS:
        return TIERS[key]
    try:
        return max(600, min(3500, int(token)))
    except ValueError:
        return fallback


def tier_name_for(rating: int) -> str:
    best = "newbie"
    for name, r in TIERS.items():
        if rating >= r:
            best = name
    return best.replace("_", " ").title()


def simulate_attempt(problem_rating: int, bot_rating: int, time_limit_seconds: int) -> tuple[bool, float | None]:
    """
    Returns (solved, solve_seconds).
    solve_seconds is None if the bot doesn't solve within the time limit.
    """
    diff = problem_rating - bot_rating  # positive = problem is harder than the bot

    # Elo-style solve probability: same curve as expected-score, reinterpreted
    # as "probability of a correct, timely solve" instead of "win probability".
    p_solve = 1 / (1 + 10 ** (diff / 400))
    p_solve = min(0.97, max(0.03, p_solve))

    if random.random() > p_solve:
        return False, None

    # difficulty_frac: 0 = trivially easy for the bot, 1 = right at/above its edge.
    difficulty_frac = 1 / (1 + 10 ** (-diff / 400))
    alpha = 2 + difficulty_frac * 6       # skews mass toward 1.0 (late) as it grows
    beta_ = 2 + (1 - difficulty_frac) * 6  # skews mass toward 0.0 (early) as it grows

    frac = random.betavariate(alpha, beta_)
    frac = min(0.98, max(0.02, frac))
    return True, frac * time_limit_seconds


def simulate_cf_game(problem_rating: int, bot_rating: int, time_limit_seconds: int) -> tuple[bool, float | None]:
    return simulate_attempt(problem_rating, bot_rating, time_limit_seconds)


def simulate_lc_game(difficulty_label: str, bot_rating: int, time_limit_seconds: int) -> tuple[bool, float | None]:
    problem_rating = LC_DIFFICULTY_RATING.get(difficulty_label.lower(), 1500)
    return simulate_attempt(problem_rating, bot_rating, time_limit_seconds)
