"""
platforms/base.py — Abstract interface every platform adapter must implement.

To add a new platform:
  1. Create platforms/myplatform.py
  2. Subclass PlatformAdapter and implement the three abstract methods
  3. Register it in platforms/__init__.py  ← that's all
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Submission:
    problem_id: str       # platform-native ID
    title:      str | None
    verdict:    str       # 'AC', 'WA', 'TLE', etc.
    timestamp:  float     # Unix epoch (UTC)
    language:   str | None = None
    url:        str | None = None


class PlatformAdapter(ABC):
    """One subclass per competitive programming platform."""

    KEY:  str   # short key used in DB and commands:  'cf', 'lc', 'cc', 'atcoder'
    NAME: str   # human-readable name: 'Codeforces', 'LeetCode', …

    # ── Must implement ──────────────────────────────────────────────────────

    @abstractmethod
    async def verify_handle(self, handle: str) -> tuple[bool, str]:
        """
        Check that the handle exists on the platform.
        Returns (valid: bool, message: str).
        """

    @abstractmethod
    async def get_recent_submissions(
        self, handle: str, limit: int = 20
    ) -> list[Submission]:
        """
        Fetch the most recent `limit` submissions for this handle.
        Raises RuntimeError on API failure.
        """

    @abstractmethod
    async def check_solved(
        self, handle: str, problem_id: str, since_ts: float
    ) -> tuple[bool, str]:
        """
        Return (True, '✅ Accepted') if the handle has an AC submission
        for `problem_id` at or after `since_ts` (Unix epoch, UTC).
        Otherwise return (False, reason).
        """

    # ── Optional helpers ────────────────────────────────────────────────────

    def problem_url(self, problem_id: str) -> str | None:
        """Return a direct URL to the problem, or None if not supported."""
        return None

    def format_problem_id(self, raw: str) -> str:
        """Normalise user input to canonical problem ID format."""
        return raw
