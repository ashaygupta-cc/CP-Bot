"""
cogs/admin.py
Commands: !setweek, !setmonth, !currentweek, !setpoints, !points
"""

import discord
from discord.ext import commands
from datetime import date, datetime, timezone, timedelta
from database.connection import get_pool
from database import queries as q
from database import duel_queries as dq
from config import (COLOR_SUCCESS, COLOR_INFO, COLOR_ERROR, COLOR_WARN,
                    COLOR_PURPLE, ADMIN_ROLE, DEFAULT_DIFFICULTY_POINTS)


def is_admin():
    async def predicate(ctx):
        return (
            any(r.name == ADMIN_ROLE for r in ctx.author.roles)
            or ctx.author.guild_permissions.administrator
        )
    return commands.check(predicate)


class Admin(commands.Cog):
    """Admin-only server configuration commands."""

    def __init__(self, bot):
        self.bot = bot

    # ── /setweek ────────────────────────────────────────────────────────────

    @commands.command(name="setweek")
    @is_admin()
    async def set_week(self, ctx, label: str = None, start: str = None, end: str = None):
        """
        Create a new active week (deactivates previous).
        !setweek "Week 1" 2026-06-23 2026-06-29
        """
        if not label or not start or not end:
            embed = discord.Embed(title="📅  Set Week  —  Usage", color=COLOR_INFO)
            embed.add_field(
                name="Command",
                value='`!setweek "<label>" <start> <end>`',
                inline=False,
            )
            embed.add_field(
                name="Example",
                value='`!setweek "Week 1" 2026-06-23 2026-06-29`',
                inline=False,
            )
            embed.set_footer(text="Dates: YYYY-MM-DD  ·  Previous week auto-deactivated")
            await ctx.send(embed=embed)
            return

        try:
            start_d = date.fromisoformat(start)
            end_d   = date.fromisoformat(end)
        except ValueError:
            await ctx.send("❌  Invalid date format. Use `YYYY-MM-DD`.")
            return

        if end_d < start_d:
            await ctx.send("❌  End date must be after start date.")
            return

        pool = get_pool()
        async with pool.acquire() as conn:
            week_id = await q.create_week(conn, str(ctx.guild.id), label, start_d, end_d)

        embed = discord.Embed(
            title="✅  New Week Activated",
            color=COLOR_SUCCESS,
        )
        embed.add_field(name="Label",   value=f"**{label}**",     inline=True)
        embed.add_field(name="Week ID", value=f"`#{week_id}`",    inline=True)
        embed.add_field(name="‎",        value="‎",                 inline=True)
        embed.add_field(name="Starts",  value=f"`{start_d}`",     inline=True)
        embed.add_field(name="Ends",    value=f"`{end_d}`",       inline=True)
        embed.add_field(name="‎",        value="‎",                 inline=True)
        embed.set_footer(text=f"Set by {ctx.author.display_name}  ·  Add problems with /addproblem")
        await ctx.send(embed=embed)

    # ── /setmonth ───────────────────────────────────────────────────────────

    @commands.command(name="setmonth")
    @is_admin()
    async def set_month(self, ctx, label: str = None, start: str = None, end: str = None):
        """
        Create a new active month for monthly leaderboard tracking.
        !setmonth "June 2026" 2026-06-01 2026-06-30
        """
        if not label or not start or not end:
            embed = discord.Embed(title="📆  Set Month  —  Usage", color=COLOR_INFO)
            embed.add_field(
                name="Command",
                value='`!setmonth "<label>" <start> <end>`',
                inline=False,
            )
            embed.add_field(
                name="Example",
                value='`!setmonth "June 2026" 2026-06-01 2026-06-30`',
                inline=False,
            )
            await ctx.send(embed=embed)
            return

        try:
            start_d = date.fromisoformat(start)
            end_d   = date.fromisoformat(end)
        except ValueError:
            await ctx.send("❌  Invalid date format. Use `YYYY-MM-DD`.")
            return

        if end_d < start_d:
            await ctx.send("❌  End date must be after start date.")
            return

        pool = get_pool()
        async with pool.acquire() as conn:
            month_id = await q.create_month(conn, str(ctx.guild.id), label, start_d, end_d)

        embed = discord.Embed(
            title="✅  New Month Activated",
            color=COLOR_PURPLE,
        )
        embed.add_field(name="Label",    value=f"**{label}**",    inline=True)
        embed.add_field(name="Month ID", value=f"`#{month_id}`",  inline=True)
        embed.add_field(name="‎",         value="‎",                inline=True)
        embed.add_field(name="Starts",   value=f"`{start_d}`",    inline=True)
        embed.add_field(name="Ends",     value=f"`{end_d}`",      inline=True)
        embed.add_field(name="‎",         value="‎",                inline=True)
        embed.set_footer(text=f"Set by {ctx.author.display_name}  ·  Problems auto-linked when added via /addproblem")
        await ctx.send(embed=embed)

    # ── /currentweek ────────────────────────────────────────────────────────

    @commands.command(name="currentweek", aliases=["week"])
    async def current_week(self, ctx):
        """Show the currently active week and month."""
        pool = get_pool()
        async with pool.acquire() as conn:
            week  = await q.get_active_week(conn, str(ctx.guild.id))
            month = await q.get_active_month(conn, str(ctx.guild.id))

        if not week and not month:
            await ctx.send("❌  No active week or month. Admins: `!setweek` and `!setmonth`")
            return

        embed = discord.Embed(title="📅  Active Periods", color=COLOR_INFO)

        if week:
            today = q.today_ist()
            days_left = (week["end_date"] - today).days
            status = f"Ends in **{days_left} day(s)**" if days_left >= 0 else "⚠️ Ended"
            embed.add_field(
                name="📅  Current Week",
                value=(
                    f"**{week['label']}**  `#{week['id']}`\n"
                    f"`{week['start_date']}` → `{week['end_date']}`\n"
                    f"{status}"
                ),
                inline=False,
            )
        else:
            embed.add_field(name="📅  Week", value="No active week", inline=False)

        if month:
            embed.add_field(
                name="📆  Current Month",
                value=(
                    f"**{month['label']}**  `#{month['id']}`\n"
                    f"`{month['start_date']}` → `{month['end_date']}`"
                ),
                inline=False,
            )

        embed.set_footer(text=f"Today (IST): {q.today_ist()}")
        await ctx.send(embed=embed)

    # ── /setpoints ──────────────────────────────────────────────────────────

    @commands.command(name="setpoints")
    @is_admin()
    async def set_points(self, ctx, difficulty: str = None, points: int = None):
        """
        Set how many points a difficulty level is worth.
        !setpoints easy 8
        """
        if not difficulty or points is None:
            embed = discord.Embed(title="⚙️  Set Points  —  Usage", color=COLOR_INFO)
            embed.add_field(name="Command", value="`!setpoints <difficulty> <points>`", inline=False)
            embed.add_field(name="Example", value="`!setpoints hard 25`", inline=False)
            embed.add_field(
                name="Built-in Difficulties",
                value="`easy` · `medium` · `hard` · `expert` · `master`\nCustom allowed: `!setpoints legendary 100`",
                inline=False,
            )
            await ctx.send(embed=embed)
            return

        if points < 0:
            await ctx.send("❌  Points must be ≥ 0.")
            return

        pool = get_pool()
        async with pool.acquire() as conn:
            await q.set_difficulty_points(conn, str(ctx.guild.id), difficulty, points)

        await ctx.send(
            f"✅  **{difficulty.capitalize()}** is now worth **{points} pts**."
        )

    # ── /points ─────────────────────────────────────────────────────────────

    @commands.command(name="points")
    async def show_points(self, ctx):
        """Show the current difficulty → points mapping."""
        pool = get_pool()
        async with pool.acquire() as conn:
            cfg = await q.get_difficulty_points(conn, str(ctx.guild.id))

        DIFF_EMOJI = {"easy": "🟢", "medium": "🟡", "hard": "🔴", "expert": "🟣", "master": "⚫"}
        lines = [
            f"{DIFF_EMOJI.get(d, '⚪')}  **{d.capitalize()}** — `{p} pts`"
            for d, p in sorted(cfg.items())
        ]
        embed = discord.Embed(
            title="⚙️  Difficulty Points Table",
            description="\n".join(lines),
            color=COLOR_INFO,
        )
        embed.set_footer(text="Change with /setpoints <difficulty> <points>")
        await ctx.send(embed=embed)

    @commands.command(name="updatestats")
    @is_admin()
    async def update_stats(self, ctx, team: int = 11, contests: int = 2, linkedin: int = 450):
        """
        Update website live stats (Team count, Contests held, LinkedIn followers).
        !updatestats 11 2 500
        """
        from api_server import _CUSTOM_STATS
        _CUSTOM_STATS["team_members"] = team
        _CUSTOM_STATS["contests_held"] = contests
        _CUSTOM_STATS["linkedin_followers"] = linkedin

        embed = discord.Embed(title="📊  Website Stats Updated", color=COLOR_SUCCESS)
        embed.add_field(name="Team Members", value=str(team), inline=True)
        embed.add_field(name="Contests Held", value=str(contests), inline=True)
        embed.add_field(name="LinkedIn Followers", value=str(linkedin), inline=True)
        embed.set_footer(text=f"Updated by {ctx.author.display_name}")
        await ctx.send(embed=embed)

    @commands.command(name="addcontest", aliases=["newcontest", "newContest"])
    @is_admin()
    async def add_contest(self, ctx, title: str = None, cf_url: str = None, start_time: str = None):
        """
        Register a new contest, persisted to the DB so it actually shows up
        on the site (and survives bot restarts — the old version just
        appended to an in-memory list nothing ever read).
        !newcontest "Binary Beats Contest 1" https://codeforces.com/contest/1234 2026-08-20T18:00
        Date accepts "YYYY-MM-DD", "YYYY-MM-DD HH:MM", or full ISO 8601.
        """
        if not title or not cf_url:
            embed = discord.Embed(title="🏆  New Contest  —  Usage", color=COLOR_INFO)
            embed.add_field(
                name="Command",
                value='`!newContest "<name>" <link> [date]`',
                inline=False,
            )
            embed.add_field(
                name="Example",
                value='`!newContest "Binary Beats Weekly 1" https://codeforces.com/contest/1234 2026-08-20T18:00`',
                inline=False,
            )
            embed.set_footer(text='Date defaults to 7 days from now if omitted. Aliases: !addcontest')
            await ctx.send(embed=embed)
            return

        start_dt = None
        if start_time:
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    start_dt = datetime.strptime(start_time, fmt).replace(tzinfo=timezone.utc)
                    break
                except ValueError:
                    continue
            if start_dt is None:
                await ctx.send("❌  Couldn't parse that date. Try `YYYY-MM-DD` or `YYYY-MM-DDTHH:MM`.")
                return
        else:
            start_dt = datetime.now(timezone.utc) + timedelta(days=7)

        pool = get_pool()
        async with pool.acquire() as conn:
            await q.add_custom_contest(
                conn, str(ctx.guild.id), title, cf_url,
                start_dt.timestamp(), str(ctx.author.id),
            )

        embed = discord.Embed(title="🚀  Contest Added", color=COLOR_SUCCESS)
        embed.add_field(name="Name", value=title, inline=False)
        embed.add_field(name="Link", value=cf_url, inline=False)
        embed.add_field(name="Starts", value=f"<t:{int(start_dt.timestamp())}:F>", inline=False)
        embed.set_footer(text=f"Added by {ctx.author.display_name} · live on the site now")
        await ctx.send(embed=embed)

    # ── !team ───────────────────────────────────────────────────────────────

    @commands.command(name="team")
    @is_admin()
    async def team_add(
        self, ctx, name: str = None, role: str = None,
        linkedin_url: str = None, github_url: str = None,
    ):
        """
        Add (or update, if the name already exists) a member's card on the
        site's Team page.
        !team "Jane Doe" "Dev Lead" https://linkedin.com/in/janedoe https://github.com/janedoe
        GitHub is optional.
        """
        if not name or not role or not linkedin_url:
            embed = discord.Embed(title="👥  Team  —  Usage", color=COLOR_INFO)
            embed.add_field(
                name="Command",
                value='`!team "<name>" "<role>" <linkedinURL> [githubURL]`',
                inline=False,
            )
            embed.add_field(
                name="Example",
                value='`!team "Jane Doe" "Dev Lead" https://linkedin.com/in/janedoe https://github.com/janedoe`',
                inline=False,
            )
            embed.set_footer(text="Running this again for the same name updates their card instead of duplicating it.")
            await ctx.send(embed=embed)
            return

        if "linkedin.com" not in linkedin_url.lower():
            await ctx.send("❌  That doesn't look like a LinkedIn URL. Usage: `!team \"<name>\" \"<role>\" <linkedinURL> [githubURL]`")
            return
        if github_url and "github.com" not in github_url.lower():
            await ctx.send("❌  That doesn't look like a GitHub URL.")
            return

        pool = get_pool()
        async with pool.acquire() as conn:
            await q.add_team_member(conn, str(ctx.guild.id), name, role, linkedin_url, github_url, str(ctx.author.id))

        embed = discord.Embed(title="👥  Team Member Saved", color=COLOR_SUCCESS)
        embed.add_field(name="Name", value=name, inline=True)
        embed.add_field(name="Role", value=role, inline=True)
        embed.add_field(name="LinkedIn", value=linkedin_url, inline=False)
        if github_url:
            embed.add_field(name="GitHub", value=github_url, inline=False)
        embed.set_footer(text=f"Added by {ctx.author.display_name} · live on the site now")
        await ctx.send(embed=embed)

    # ── !endduel ────────────────────────────────────────────────────────────

    @commands.command(name="endduel", aliases=["forceendduel"])
    @is_admin()
    async def end_duel(self, ctx, duel_id: int = None):
        """
        Force-end a stuck active duel/blitz match (admin escape hatch for
        glitches — e.g. someone's client crashed mid-match and never
        forfeited). Marks it cancelled without awarding a winner or applying
        a rating change either way.
        !endduel 128
        """
        if duel_id is None:
            pool = get_pool()
            async with pool.acquire() as conn:
                active = await dq.get_all_active_duels(conn)
            if not active:
                await ctx.send("✅  No active duels right now.")
                return
            lines = [f"`#{d['id']}` — <@{d['p1_id']}> vs <@{d.get('p2_id') or 'bot'}> · {d['mode']}" for d in active[:15]]
            embed = discord.Embed(title="⚔️  Active Duels", description="\n".join(lines), color=COLOR_INFO)
            embed.set_footer(text="!endduel <id> to force-end one")
            await ctx.send(embed=embed)
            return

        pool = get_pool()
        async with pool.acquire() as conn:
            duel = await dq.get_duel(conn, duel_id)
            if not duel:
                await ctx.send(f"❌  No duel with id `{duel_id}`.")
                return
            if duel["status"] != "active":
                await ctx.send(f"⚠️  Duel `{duel_id}` isn't active (status: `{duel['status']}`) — nothing to end.")
                return
            await dq.set_duel_status(conn, duel_id, "cancelled")

        embed = discord.Embed(
            title="🛑  Duel Force-Ended",
            description=f"Duel `#{duel_id}` was cancelled by an admin. No rating change was applied to either side.",
            color=COLOR_WARN,
        )
        embed.set_footer(text=f"Ended by {ctx.author.display_name}")
        await ctx.send(embed=embed)
        try:
            channel = ctx.guild.get_channel(int(duel["channel_id"])) if duel.get("channel_id") else None
            if channel and channel.id != ctx.channel.id:
                await channel.send(embed=embed)
        except Exception:
            pass

    # ── !prunedaily / !pruneold ──────────────────────────────────────────

    @commands.command(name="prunedaily", aliases=["pruneold", "prune30d"])
    @is_admin()
    async def prune_old_daily_data(self, ctx):
        """
        Admin-only manual command to prune out-of-window daily problem assignments
        and thread submissions older than 30 days (1 month).
        
        SAFE: User solve history (solves), member points, streaks, and
        leaderboard counts are 100% preserved and never touched.
        """
        status_msg = await ctx.send("⏳  Checking database for daily problems & submissions older than 30 days…")
        pool = get_pool()
        
        async with pool.acquire() as conn:
            # 1. Delete out-of-window messages in daily_problems and daily_editorials (> 30 days)
            del_msgs_res = await conn.execute(
                """DELETE FROM discord_messages 
                   WHERE channel_key IN ('daily_problems', 'daily_editorials') 
                   AND created_at < NOW() - INTERVAL '30 days'"""
            )
            # 2. Delete out-of-window threads (> 30 days)
            del_threads_res = await conn.execute(
                """DELETE FROM discord_threads 
                   WHERE channel_key IN ('daily_problems', 'daily_editorials') 
                   AND created_at < NOW() - INTERVAL '30 days'"""
            )
            # 3. Delete out-of-window daily_problems (> 30 days)
            del_probs_res = await conn.execute(
                """DELETE FROM daily_problems 
                   WHERE assigned_date < CURRENT_DATE - INTERVAL '30 days'"""
            )

        # Parse counts from postgres tag (e.g. "DELETE 5")
        def parse_count(res_str):
            try:
                return res_str.split()[-1] if res_str else "0"
            except Exception:
                return "0"

        c_msgs = parse_count(del_msgs_res)
        c_threads = parse_count(del_threads_res)
        c_probs = parse_count(del_probs_res)

        embed = discord.Embed(
            title="🧹  30-Day Retention Prune Complete",
            description="Cleaned out-of-window daily problem records and submissions older than 30 days.",
            color=COLOR_SUCCESS if (c_msgs != "0" or c_threads != "0" or c_probs != "0") else COLOR_INFO
        )
        embed.add_field(name="Daily Problems Removed", value=f"`{c_probs}` rows", inline=True)
        embed.add_field(name="Thread Submissions Removed", value=f"`{c_msgs}` messages", inline=True)
        embed.add_field(name="Threads Removed", value=f"`{c_threads}` threads", inline=True)
        embed.add_field(
            name="🛡️ Data Safety Guarantee",
            value="User solve history, points, streaks, and leaderboard standings remain **100% safe & intact**.",
            inline=False
        )
        embed.set_footer(text=f"Executed manually by {ctx.author.display_name} · No auto-deletion")
        
        await status_msg.edit(content=None, embed=embed)

    @set_week.error
    @set_month.error
    @set_points.error
    @update_stats.error
    @add_contest.error
    @team_add.error
    @end_duel.error
    @prune_old_daily_data.error
    async def admin_error(self, ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.send(f"❌  You need the **{ADMIN_ROLE}** role or Administrator permission.")
        elif isinstance(error, commands.BadArgument):
            await ctx.send(f"❌  {error}")


async def setup(bot):
    await bot.add_cog(Admin(bot))
