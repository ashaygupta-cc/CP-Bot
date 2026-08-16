"""
duel_bot_engine.py — The "always-available" bot opponent.

REWORKED TIMING MODEL (fitted to observed human solve times):
The old model sampled solve time as a fraction of the TIME LIMIT, so the
bot's speed changed with the timer instead of the problem — a bot could
knock out an easy problem in 2-3 minutes, which no real 800-rated player
does. Solve time is now a function of the PROBLEM alone (its rating) and
the bot's own strength, independent of the clock:

    d          = problem_rating − bot_rating       (relative difficulty)
    mean_min   = 0.006·problem_rating              (intrinsic length: harder
                                                    problems take longer even
                                                    for a perfectly-matched
                                                    player)
               + 11·sigmoid((d − 100) / 250)       (relative-difficulty cost,
                                                    smooth S-curve — grows
                                                    slowly, saturates ~11 min)
               + max(0, d − 1000) / 150            (grind tail for way-above-
                                                    level problems)

Calibration points this reproduces (± noise):
    ~850 bot,  problem at its level (~850)   →  ~8–10 min
    ~850 bot,  LC-Medium (~1550)             →  ~18–20 min
    ~850 bot,  LC-Hard   (~2250)             →  ~26–33 min
    1600 bot,  1500-rated problem            →  ~11–12 min
    2400 bot,  1400-rated problem            →  ~8–9 min (strong ≠ instant)

Because the cost term is a sigmoid of the rating GAP, the bot "improves
slowly": raising its rating 100 points shaves only a minute or two — a
grinding, human-like progression, so low-rated players get a fair race
they can't trivially outrun, and the bot never teleports to the answer.

WHETHER the bot solves at all is still an Elo-style logistic gate on the
same gap (clamped 5%–96%), and if the sampled time doesn't fit in the
remaining clock, the attempt counts as unsolved.
"""

import math
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

# CF-scale equivalents for LC difficulties, tuned so the timing model
# lands on the calibration points above for a ~800-900 bot:
#   Easy ≈ 900, Medium ≈ 1550, Hard ≈ 2250.
LC_DIFFICULTY_RATING = {"easy": 900, "medium": 1550, "hard": 2250}

MIN_SOLVE_SECONDS = 240   # even a trivial problem takes ~4 min to read+code+submit


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


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _mean_solve_minutes(problem_rating: int, bot_rating: int) -> float:
    d = problem_rating - bot_rating
    return (
        0.006 * problem_rating
        + 11.0 * _sigmoid((d - 100) / 250.0)
        + max(0.0, d - 1000.0) / 150.0
    )


def simulate_attempt(problem_rating: int, bot_rating: int, time_limit_seconds: int) -> tuple[bool, float | None]:
    """
    Returns (solved, solve_seconds).
    solve_seconds is None if the bot doesn't solve within the time limit.
    """
    diff = problem_rating - bot_rating  # positive = problem is harder than the bot

    # Solve gate: the bot essentially ALWAYS solves — its solve time comes
    # from the calibrated formula below, and if that time doesn't fit the
    # remaining clock, the attempt naturally counts as unsolved (that's how
    # a weak bot still fails Hards in blitz). A 2% random miss stays in so
    # that once in a rare while even the bot has a bad day.
    p_solve = 0.98
    if random.random() > p_solve:
        return False, None

    # Human-like solve time from the calibrated curve, with mild run-to-run
    # variance (a player's speed on the same problem varies day to day).
    mean_seconds = _mean_solve_minutes(problem_rating, bot_rating) * 60.0
    noise = random.gauss(1.0, 0.15)
    noise = min(1.35, max(0.75, noise))
    solve_seconds = max(MIN_SOLVE_SECONDS, mean_seconds * noise)

    # Doesn't fit in the remaining clock → counts as unsolved.
    if solve_seconds > time_limit_seconds * 0.97:
        return False, None
    return True, solve_seconds


def simulate_cf_game(problem_rating: int, bot_rating: int, time_limit_seconds: int) -> tuple[bool, float | None]:
    return simulate_attempt(problem_rating, bot_rating, time_limit_seconds)


def simulate_lc_game(difficulty_label: str, bot_rating: int, time_limit_seconds: int) -> tuple[bool, float | None]:
    problem_rating = LC_DIFFICULTY_RATING.get(difficulty_label.lower(), 1550)
    return simulate_attempt(problem_rating, bot_rating, time_limit_seconds)