"""
platforms/__init__.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
To add a new platform:
  1. Create platforms/myplatform.py subclassing PlatformAdapter
  2. Import it here and add an instance to ADAPTERS
  That's literally all — every cog picks it up automatically.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from platforms.codeforces import CodeforcesAdapter
from platforms.leetcode   import LeetCodeAdapter
from platforms.codechef   import CodeChefAdapter
from platforms.atcoder    import AtCoderAdapter
from platforms.base       import PlatformAdapter, Submission

# ── Registry ──────────────────────────────────────────────────────────────────
ADAPTERS: dict[str, PlatformAdapter] = {
    "cf":      CodeforcesAdapter(),
    "lc":      LeetCodeAdapter(),
    "cc":      CodeChefAdapter(),
    "atcoder": AtCoderAdapter(),
}

# Convenience helpers
def get(key: str) -> PlatformAdapter | None:
    return ADAPTERS.get(key.lower())

def keys() -> list[str]:
    return list(ADAPTERS.keys())

def names() -> dict[str, str]:
    return {k: a.NAME for k, a in ADAPTERS.items()}

def choices_str() -> str:
    return " · ".join(f"`{k}` ({a.NAME})" for k, a in ADAPTERS.items())
