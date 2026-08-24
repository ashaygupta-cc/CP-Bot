"""
cogs/ai_agent.py — Personalised AI Competitive Programming Coach Agent.

Uses free Google Gemini API (GEMINI_API_KEY) to provide:
  • !coach [@user]      Personalised training roadmap based on user's stats & ratings
  • !hint <problem_id>  Progressive, non-spoiling hints for CP/DSA problems
  • !explain <topic>    CP algorithm explanations with C++17 templates & problem patterns
  • !review <code>      Code review for time/space complexity, TLE, MLE & bug detection
"""

import os
import re
import asyncio
import aiohttp
import discord
from discord.ext import commands
from datetime import datetime, timezone

import config
from database.connection import get_pool
from database import queries as q
from database import duel_queries as dq

COLOR_AI      = 0x00D9FF   # Cyan branding
COLOR_SUCCESS = 0x57F287
COLOR_WARN    = 0xFEE75C
COLOR_ERROR   = 0xED4245

BOT_LOGO = "https://raw.githubusercontent.com/ashaygupta-cc/ashaygupta-cc/main/Binary%20Beats.webp"
BRAND    = "Binary Beats AI Coach"


def _get_gemini_api_key() -> str | None:
    return os.getenv("GEMINI_API_KEY") or getattr(config, "GEMINI_API_KEY", None)


async def _call_gemini_api(prompt: str, system_instruction: str = None) -> str | None:
    """Call Google Gemini REST API using free tier endpoints."""
    api_key = _get_gemini_api_key()
    if not api_key:
        return None

    # Try gemini-2.5-flash first, fallback to gemini-1.5-flash
    models = ["gemini-2.5-flash", "gemini-1.5-flash"]
    
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        headers = {"Content-Type": "application/json"}
        
        contents = []
        if system_instruction:
            contents.append({"role": "user", "parts": [{"text": f"System Instruction: {system_instruction}"}]})
            contents.append({"role": "model", "parts": [{"text": "Understood. I will act strictly as specified."}]})
        
        contents.append({"role": "user", "parts": [{"text": prompt}]})
        
        payload = {
            "contents": contents,
            "generationConfig": {
                "temperature": 0.4,
                "maxOutputTokens": 1500,
            }
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=25)) as r:
                    if r.status == 200:
                        data = await r.json()
                        candidates = data.get("candidates") or []
                        if candidates:
                            parts = candidates[0].get("content", {}).get("parts", [])
                            if parts:
                                return parts[0].get("text", "").strip()
                    else:
                        err_txt = await r.text()
                        print(f"[AI_AGENT] Model {model} returned HTTP {r.status}: {err_txt[:200]}", flush=True)
        except Exception as e:
            print(f"[AI_AGENT] Model {model} request failed: {e}", flush=True)
            
    return None


class AIAgent(commands.Cog):
    """Personalised AI Competitive Programming Coach."""

    def __init__(self, bot):
        self.bot = bot

    def _brand(self, title: str, desc: str = None, color: int = COLOR_AI) -> discord.Embed:
        em = discord.Embed(title=title, description=desc, color=color)
        em.set_author(name=BRAND, icon_url=BOT_LOGO)
        em.set_thumbnail(url=BOT_LOGO)
        return em

    # ── !coach ─────────────────────────────────────────────────────────────

    @commands.command(name="coach", aliases=["aicoach"])
    async def coach_cmd(self, ctx, target: discord.Member = None):
        """
        !coach [@user]
        Generate a personalised CP training roadmap by analyzing user's ratings & solves.
        """
        user = target or ctx.author
        api_key = _get_gemini_api_key()

        if not api_key:
            em = self._brand("⚠️  GEMINI_API_KEY Required", color=COLOR_WARN)
            em.description = (
                "The AI Coach requires a free **Google Gemini API Key**.\n\n"
                "**Setup Instructions:**\n"
                "1. Get a free API key at [Google AI Studio](https://aistudio.google.com/)\n"
                "2. Add `GEMINI_API_KEY=your_key_here` to your `.env` file\n"
                "3. Restart the bot!"
            )
            await ctx.send(embed=em)
            return

        msg = await ctx.send(f"🤖  *Analyzing competitive programming profile for **{user.display_name}**…*")

        pool = get_pool()
        async with pool.acquire() as conn:
            user_data = await conn.fetchrow(
                "SELECT discord_id, discord_username FROM users WHERE discord_id = $1",
                str(user.id),
            )
            handles = await conn.fetch(
                "SELECT platform, handle FROM handles WHERE discord_id = $1",
                str(user.id),
            )
            ratings = await dq.get_profile(conn, str(user.id), str(ctx.guild.id))
            solves_count = await conn.fetchval(
                "SELECT COUNT(*) FROM solves WHERE discord_id = $1 AND guild_id = $2",
                str(user.id), str(ctx.guild.id)
            )

        handles_str = ", ".join([f"{h['platform'].upper()}: {h['handle']}" for h in handles]) if handles else "None registered"
        ratings_str = ", ".join([f"{r['mode']}: {r['rating']} (W:{r['wins']} L:{r['losses']})" for r in ratings]) if ratings else "Default 800"

        prompt = (
            f"Act as a World Finalist Competitive Programming Coach. Analyze the following coder profile:\n"
            f"• Coder Name: {user.display_name} ({user_data['discord_username'] if user_data else user.name})\n"
            f"• Platform Handles: {handles_str}\n"
            f"• Duel Ratings & Match Stats: {ratings_str}\n"
            f"• Platform Solves Recorded: {solves_count or 0}\n\n"
            f"Provide a structured, highly actionable coaching evaluation in markdown:\n"
            f"1. **Current Skill Level Assessment**: Rank/tier evaluation (e.g. Newbie, Pupil, Specialist).\n"
            f"2. **Target Problem Rating Range**: Exact Codeforces / LeetCode difficulty range to practice.\n"
            f"3. **Top 3 Recommended Topics**: Specific algorithms/data structures to master next.\n"
            f"4. **4-Week Actionable Practice Plan**: Weekly bullet points for rapid rating improvement."
        )

        sys_inst = "You are a master Competitive Programming coach giving precise, encouraging, professional advice."
        analysis = await _call_gemini_api(prompt, sys_inst)

        if not analysis:
            await msg.edit(content="❌  Failed to generate coaching analysis from Gemini API. Check API key quota or network connectivity.")
            return

        embed = self._brand(f"🧠  Personalised CP Coach Evaluation — {user.display_name}")
        if len(analysis) > 3800:
            analysis = analysis[:3800] + "…"
        embed.description = analysis
        embed.set_footer(text=f"Requested by {ctx.author.display_name} • Powered by Gemini AI", icon_url=BOT_LOGO)

        await msg.edit(content=None, embed=embed)

    # ── !hint ──────────────────────────────────────────────────────────────

    @commands.command(name="hint", aliases=["aihint"])
    async def hint_cmd(self, ctx, problem_id: str = None, *, question: str = None):
        """
        !hint <problem_id/url>
        Get progressive, non-spoiling hints for a competitive programming problem.
        """
        if not problem_id:
            em = self._brand("💡  AI Hint  —  Usage", color=COLOR_WARN)
            em.description = (
                "Get progressive hints without spoiling full solution code.\n\n"
                "`!hint <problem_id>` — e.g. `!hint 1547A` or `!hint LC-1` or `!hint Two Sum`"
            )
            await ctx.send(embed=em)
            return

        api_key = _get_gemini_api_key()
        if not api_key:
            await ctx.send("⚠️  `GEMINI_API_KEY` is not set in `.env`.")
            return

        msg = await ctx.send(f"💡  *Generating progressive hints for **{problem_id}**…*")

        prompt = (
            f"A competitive programming student is working on problem: '{problem_id}'.\n"
            f"Provide 3 progressive, non-spoiling hints:\n"
            f"• **Hint 1 (Key Observation)**: Conceptual intuition or mathematical property to look for.\n"
            f"• **Hint 2 (Algorithmic Technique)**: Data structure or paradigm to use (e.g. DP, Segment Tree, Binary Search, Two Pointers).\n"
            f"• **Hint 3 (High-Level Outline)**: Step-by-step logic without writing complete executable code.\n"
            f"Do NOT output complete C++/Python code. Keep it brief and encouraging."
        )

        hints = await _call_gemini_api(prompt, "You are a helpful CP tutor who gives hints without spoiling solutions.")

        if not hints:
            await msg.edit(content="❌  Failed to fetch hints from AI model.")
            return

        embed = self._brand(f"💡  Progressive Hints — {problem_id}")
        embed.description = hints
        embed.set_footer(text="Tip: Try implementing the solution on your own first!", icon_url=BOT_LOGO)
        await msg.edit(content=None, embed=embed)

    # ── !explain ───────────────────────────────────────────────────────────

    @commands.command(name="explain", aliases=["aiexplain"])
    async def explain_cmd(self, ctx, *, topic: str = None):
        """
        !explain <topic/algorithm>
        Explains a CP topic with intuition, time complexity, and a clean C++ template.
        """
        if not topic:
            em = self._brand("📚  AI Topic Explanation  —  Usage", color=COLOR_WARN)
            em.description = "`!explain <topic>` — e.g. `!explain Segment Tree` or `!explain Binary Search on Answer`"
            await ctx.send(embed=em)
            return

        api_key = _get_gemini_api_key()
        if not api_key:
            await ctx.send("⚠️  `GEMINI_API_KEY` is not set in `.env`.")
            return

        msg = await ctx.send(f"📚  *Generating CP guide for **{topic}**…*")

        prompt = (
            f"Explain the Competitive Programming topic: '{topic}'.\n"
            f"Structure the response clearly:\n"
            f"1. **Core Concept & Intuition**: What problem type does it solve?\n"
            f"2. **Time & Space Complexity**: Best, average, worst case.\n"
            f"3. **C++17 Implementation Template**: Clean, production-ready competitive programming code snippet.\n"
            f"4. **Common Pitfalls & Tricks**: 1-based indexing, integer overflow, edge cases."
        )

        guide = await _call_gemini_api(prompt, "You are a expert CP algorithms instructor.")

        if not guide:
            await msg.edit(content="❌  Failed to fetch guide from AI model.")
            return

        embed = self._brand(f"📚  CP Guide — {topic.title()}")
        if len(guide) > 3800:
            guide = guide[:3800] + "…"
        embed.description = guide
        embed.set_footer(text=f"Requested by {ctx.author.display_name}", icon_url=BOT_LOGO)
        await msg.edit(content=None, embed=embed)

    # ── !review ────────────────────────────────────────────────────────────

    @commands.command(name="review", aliases=["aireview"])
    async def review_cmd(self, ctx, *, code: str = None):
        """
        !review <code>
        Code review for complexity bottlenecks, TLE/MLE risks, and bug detection.
        """
        if not code:
            em = self._brand("🔬  AI Code Review  —  Usage", color=COLOR_WARN)
            em.description = "Paste code snippet inside codeblocks:\n```cpp\n!review\n#include <bits/stdc++.h>...\n```"
            await ctx.send(embed=em)
            return

        api_key = _get_gemini_api_key()
        if not api_key:
            await ctx.send("⚠️  `GEMINI_API_KEY` is not set in `.env`.")
            return

        # Strip codeblock markers
        clean_code = re.sub(r"^```[a-zA-Z]*\n?", "", code.strip())
        clean_code = re.sub(r"```$", "", clean_code).strip()

        msg = await ctx.send("🔬  *Reviewing code for complexity, TLE/MLE risks, and bugs…*")

        prompt = (
            f"Review this Competitive Programming code snippet:\n```cpp\n{clean_code[:2000]}\n```\n"
            f"Evaluate:\n"
            f"1. **Time & Space Complexity**: Big-O notation.\n"
            f"2. **Correctness & Edge Cases**: Integer overflow (`int` vs `long long`), out-of-bounds, uninitialized variables.\n"
            f"3. **Optimization Suggestions**: Faster I/O (`cin.tie(NULL)`), vector pre-allocation, algorithmic improvements."
        )

        review_res = await _call_gemini_api(prompt, "You are a code reviewer specialized in competitive programming.")

        if not review_res:
            await msg.edit(content="❌  Failed to analyze code snippet.")
            return

        embed = self._brand("🔬  AI Code Review Results")
        if len(review_res) > 3800:
            review_res = review_res[:3800] + "…"
        embed.description = review_res
        embed.set_footer(text=f"Reviewed for {ctx.author.display_name}", icon_url=BOT_LOGO)
        await msg.edit(content=None, embed=embed)


async def setup(bot):
    await bot.add_cog(AIAgent(bot))
