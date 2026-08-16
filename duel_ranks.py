"""
duel_ranks.py — Codeforces-style rating tiers + professional rank display.

Tier thresholds match real CF (newbie=800, pupil=1200, specialist=1400, etc.)
Badge colors (Discord embed field colors) for each tier.
"""

# Tier definitions: rating threshold → (name, color_int, emoji_indicator)
# Colors chosen for professional appearance (cyan/blue tones for higher ranks)
TIERS = [
    (3000, "Legendary Grandmaster", 0x00CED1, "🔴"),  # dark turquoise
    (2650, "International Grandmaster", 0x00B4D8, "🔴"),  # sky blue
    (2450, "Grandmaster", 0x0096FF, "🔴"),  # dodger blue
    (2300, "International Master", 0x00D9FF, "🔴"),  # cyan
    (2150, "Master", 0x1F7EC1, "🟠"),  # darker cyan
    (1950, "Candidate Master", 0xFF8C00, "🟠"),  # dark orange
    (1700, "Expert", 0xFF4500, "🟡"),  # orange red
    (1600, "Expert", 0xFF4500, "🟡"),  # orange red (same as 1700)
    (1500, "Specialist", 0x0077BE, "🟣"),  # cerulean
    (1400, "Specialist", 0x0077BE, "🟣"),  # cerulean (same as 1500)
    (1300, "Pupil", 0x00AA00, "🟢"),  # green
    (1200, "Pupil", 0x00AA00, "🟢"),  # green (same as 1300)
    (1100, "Pupil", 0x00AA00, "🟢"),  # green (same)
    (0, "Newbie", 0x808080, "⚫"),  # gray
]


def get_rank(rating: int) -> dict:
    """
    Returns {"name": "Specialist", "color": 0x0077BE, "badge": "badge text"}
    for a given duel rating. Used both for user profile display and round embeds.
    """
    for threshold, name, color, emoji in TIERS:
        if rating >= threshold:
            return {
                "name": name,
                "color": color,
                "threshold": threshold,
                "emoji": emoji,
            }
    return {
        "name": "Newbie",
        "color": 0x808080,
        "threshold": 0,
        "emoji": "⚫",
    }


def rank_badge(rating: int) -> str:
    """Returns a formatted rank badge string for embedding in text, e.g. 'Specialist (1400)'."""
    rank = get_rank(rating)
    return f"{rank['name']} ({rating})"


def color_for_rating(rating: int) -> int:
    """Returns the Discord embed color (int) for a given rating."""
    return get_rank(rating)["color"]
