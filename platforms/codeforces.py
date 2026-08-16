"""
platforms/codeforces.py — Codeforces adapter using the public REST API.
Problem ID format: '1234A'  (contest digits + problem letter, e.g. '1234A')
"""

import aiohttp
from platforms.base import PlatformAdapter, Submission

CF_API = "https://codeforces.com/api"


class CodeforcesAdapter(PlatformAdapter):
    KEY  = "cf"
    NAME = "Codeforces"

    def format_problem_id(self, raw: str) -> str:
        return raw.strip().upper()

    def problem_url(self, problem_id: str) -> str | None:
        contest = "".join(filter(str.isdigit, problem_id))
        index   = "".join(filter(str.isalpha, problem_id)).upper()
        if contest and index:
            return f"https://codeforces.com/problemset/problem/{contest}/{index}"
        return None

    async def verify_handle(self, handle: str) -> tuple[bool, str]:
        url = f"{CF_API}/user.info?handles={handle}"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                    data = await r.json()
        except Exception as e:
            return False, f"⚠️ Could not reach Codeforces: {e}"

        if data.get("status") == "OK":
            info   = data["result"][0]
            rating = info.get("rating", "unrated")
            rank   = info.get("rank", "unranked")
            return True, f"✅ Found `{info['handle']}` — {rank} (rating {rating})"
        return False, f"❌ Handle `{handle}` not found on Codeforces."

    async def get_recent_submissions(self, handle: str, limit: int = 20) -> list[Submission]:
        url = f"{CF_API}/user.status?handle={handle}&from=1&count={limit}"
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                data = await r.json()

        if data.get("status") != "OK":
            raise RuntimeError(data.get("comment", "Unknown CF API error"))

        results = []
        for sub in data["result"]:
            prob = sub.get("problem", {})
            cid  = prob.get("contestId", "")
            idx  = prob.get("index", "")
            results.append(Submission(
                problem_id = f"{cid}{idx}",
                title      = prob.get("name"),
                verdict    = sub.get("verdict", "?"),
                timestamp  = float(sub.get("creationTimeSeconds", 0)),
                language   = sub.get("programmingLanguage"),
                url        = f"https://codeforces.com/contest/{cid}/submission/{sub.get('id')}",
            ))
        return results

    async def check_solved(self, handle: str, problem_id: str, since_ts: float, until_ts: float = None) -> tuple[bool, str]:
        contest = "".join(filter(str.isdigit, problem_id))
        index   = "".join(filter(str.isalpha, problem_id)).upper()

        if not contest or not index:
            return False, f"❌ Invalid CF problem ID `{problem_id}`. Use format `1234A`."

        url = f"{CF_API}/user.status?handle={handle}&from=1&count=100"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    data = await r.json()
        except Exception as e:
            return False, f"⚠️ CF API error: {e}"

        if data.get("status") != "OK":
            comment = data.get("comment", "")
            if "not found" in comment.lower():
                return False, f"❌ CF handle `{handle}` not found."
            return False, f"⚠️ CF API error: {comment}"

        for sub in data["result"]:
            if sub.get("verdict") != "OK":
                continue
            prob     = sub.get("problem", {})
            sub_cid  = str(prob.get("contestId", ""))
            sub_idx  = prob.get("index", "").upper()
            sub_time = float(sub.get("creationTimeSeconds", 0))

            if sub_cid == contest and sub_idx == index and sub_time >= since_ts and (until_ts is None or sub_time <= until_ts):
                return True, "✅ Accepted"

        return False, "❌ No accepted submission found within the time window."