"""
cogs/website_sync.py — mirrors configured Discord channels into Postgres for
the website to read.

This cog is additive. It registers listeners and its own commands; it does not
touch, wrap or override anything in the existing cogs. Removing it from the
COGS list returns the bot to exactly its previous behaviour.

Discord remains the source of truth. This is a cache: every row is written from
a real Discord event, edits update it, deletions remove it, and `!syncchannel`
rebuilds it from scratch if the bot was offline.

Admin commands:
  !syncchannel <key|all> [limit]  — backfill a channel from Discord history
  !syncstatus                     — per-channel row counts and last sync time
"""

import re
import asyncio
import json
from datetime import datetime, timezone, date

import discord
from discord.ext import commands, tasks

import config
from database.connection import get_pool

# Snapshot guild stats this often. Member counts drive the community page
# counters; a minute of staleness is invisible to a visitor.
SNAPSHOT_INTERVAL_MIN = 5

# Thread names like "27 July 2026", "2026-07-27", "27/07/2026" become an
# editorial date so the website can join editorials to daily problems.
_DATE_PATTERNS = [
    (re.compile(r"(\d{4})-(\d{2})-(\d{2})"), lambda m: date(int(m[1]), int(m[2]), int(m[3]))),
    (re.compile(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})"), lambda m: date(int(m[3]), int(m[2]), int(m[1]))),
]
_MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}
_TEXT_DATE = re.compile(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})")


def parse_thread_date(name: str):
    """Best-effort date out of a thread title. Returns None when it doesn't
    look like a date — the website then falls back to creation time."""
    for pattern, build in _DATE_PATTERNS:
        m = pattern.search(name)
        if m:
            try:
                return build(m)
            except ValueError:
                pass
    m = _TEXT_DATE.search(name)
    if m:
        month = _MONTHS.get(m[2].lower()[:3] and
                            next((k for k in _MONTHS if k.startswith(m[2].lower()[:3])), ""))
        if month:
            try:
                return date(int(m[3]), month, int(m[1]))
            except ValueError:
                pass
    return None


def serialise_embed(e: discord.Embed) -> dict:
    """Keep the whole embed — the website renders titles, fields, colours,
    thumbnails and footers, and dropping any of it would flatten the contest
    reminder cards into plain text."""
    d = {
        "title": e.title,
        "description": e.description,
        "url": e.url,
        "color": e.colour.value if e.colour else None,
        "timestamp": e.timestamp.isoformat() if e.timestamp else None,
        "fields": [{"name": f.name, "value": f.value, "inline": f.inline} for f in e.fields],
    }
    if e.author:
        d["author"] = {"name": e.author.name, "icon_url": e.author.icon_url, "url": e.author.url}
    if e.footer:
        d["footer"] = {"text": e.footer.text, "icon_url": e.footer.icon_url}
    if e.thumbnail:
        d["thumbnail"] = {"url": e.thumbnail.url}
    if e.image:
        d["image"] = {"url": e.image.url}
    return {k: v for k, v in d.items() if v is not None}


def serialise_attachment(a: discord.Attachment) -> dict:
    return {
        "id": str(a.id),
        "filename": a.filename,
        "url": a.url,
        "proxy_url": a.proxy_url,
        "size": a.size,
        "content_type": a.content_type,
        "width": a.width,
        "height": a.height,
        "is_image": bool(a.content_type and a.content_type.startswith("image/")),
        "is_pdf": bool(a.content_type and "pdf" in a.content_type)
                  or a.filename.lower().endswith(".pdf"),
    }


class WebsiteSync(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.snapshot_loop.start()
        self.hourly_sync_loop.start()

    def cog_unload(self):
        self.snapshot_loop.cancel()
        self.hourly_sync_loop.cancel()

    # ── helpers ─────────────────────────────────────────────────────────────

    def _key_for(self, channel) -> str | None:
        """Maps a Discord channel (or a thread's parent) to its config key.
        Returns None for channels the website doesn't mirror — those are
        ignored entirely, so normal server chat is never stored."""
        cid = None
        parent = getattr(channel, "parent", None)
        if parent is not None:
            cid = str(parent.id)
        else:
            cid = str(getattr(channel, "id", ""))
        return config.SYNCED_CHANNELS.get(cid)

    async def _upsert(self, msg: discord.Message, key: str) -> None:
        thread = msg.channel if isinstance(msg.channel, discord.Thread) else None
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO discord_messages (
                    guild_id, channel_id, message_id, channel_key,
                    author_id, author_name, author_avatar, author_is_bot,
                    content, embeds, attachments,
                    thread_id, thread_name, thread_parent_id,
                    is_pinned, reply_to_id, created_at, edited_at, synced_at
                ) VALUES (
                    $1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11::jsonb,
                    $12,$13,$14,$15,$16,$17,$18, NOW()
                )
                ON CONFLICT (message_id) DO UPDATE SET
                    content     = EXCLUDED.content,
                    embeds      = EXCLUDED.embeds,
                    attachments = EXCLUDED.attachments,
                    is_pinned   = EXCLUDED.is_pinned,
                    edited_at   = EXCLUDED.edited_at,
                    synced_at   = NOW()
                """,
                str(msg.guild.id) if msg.guild else "",
                str(msg.channel.id),
                str(msg.id),
                key,
                str(msg.author.id),
                msg.author.display_name,
                str(msg.author.display_avatar.url) if msg.author.display_avatar else None,
                msg.author.bot,
                msg.content or "",
                json.dumps([serialise_embed(e) for e in msg.embeds]),
                json.dumps([serialise_attachment(a) for a in msg.attachments]),
                str(thread.id) if thread else None,
                thread.name if thread else None,
                str(thread.parent_id) if thread else None,
                msg.pinned,
                str(msg.reference.message_id) if msg.reference and msg.reference.message_id else None,
                msg.created_at,
                msg.edited_at,
            )

            if thread is not None:
                has_pdf = any(
                    a.filename.lower().endswith(".pdf") for a in msg.attachments
                )
                await conn.execute(
                    """
                    INSERT INTO discord_threads (
                        thread_id, guild_id, parent_id, channel_key, name,
                        editorial_date, message_count, has_pdf, is_archived,
                        created_at, synced_at
                    ) VALUES ($1,$2,$3,$4,$5,$6,1,$7,$8,$9, NOW())
                    ON CONFLICT (thread_id) DO UPDATE SET
                        name          = EXCLUDED.name,
                        message_count = discord_threads.message_count + 1,
                        -- once a PDF is seen it stays true; a later text-only
                        -- reply must not flip 'Editorial Available' back off
                        has_pdf       = discord_threads.has_pdf OR EXCLUDED.has_pdf,
                        is_archived   = EXCLUDED.is_archived,
                        synced_at     = NOW()
                    """,
                    str(thread.id),
                    str(msg.guild.id) if msg.guild else "",
                    str(thread.parent_id),
                    key,
                    thread.name,
                    parse_thread_date(thread.name),
                    has_pdf,
                    thread.archived,
                    thread.created_at or msg.created_at,
                )

    # ── listeners ───────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message):
        key = self._key_for(msg.channel)
        if key:
            try:
                await self._upsert(msg, key)
            except Exception as e:
                print(f"[sync] on_message failed ({key}): {e}")

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent):
        # Raw variant so edits to messages older than the cache still sync.
        channel = self.bot.get_channel(payload.channel_id)
        key = self._key_for(channel) if channel else None
        if not key:
            return
        try:
            msg = await channel.fetch_message(payload.message_id)
            await self._upsert(msg, key)
        except Exception as e:
            print(f"[sync] edit failed ({key}): {e}")

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        try:
            async with get_pool().acquire() as conn:
                await conn.execute(
                    "DELETE FROM discord_messages WHERE message_id = $1",
                    str(payload.message_id),
                )
        except Exception as e:
            print(f"[sync] delete failed: {e}")

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        key = self._key_for(thread)
        if not key:
            return
        try:
            async with get_pool().acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO discord_threads (
                        thread_id, guild_id, parent_id, channel_key, name,
                        editorial_date, message_count, has_pdf, is_archived,
                        created_at, synced_at
                    ) VALUES ($1,$2,$3,$4,$5,$6,0,FALSE,$7,$8, NOW())
                    ON CONFLICT (thread_id) DO UPDATE SET
                        name = EXCLUDED.name, synced_at = NOW()
                    """,
                    str(thread.id), str(thread.guild.id), str(thread.parent_id),
                    key, thread.name, parse_thread_date(thread.name),
                    thread.archived, thread.created_at or datetime.now(timezone.utc),
                )
        except Exception as e:
            print(f"[sync] thread_create failed: {e}")

    # ── guild snapshot ──────────────────────────────────────────────────────

    @tasks.loop(minutes=SNAPSHOT_INTERVAL_MIN)
    async def snapshot_loop(self):
        for guild in self.bot.guilds:
            try:
                online = sum(
                    1 for m in guild.members
                    if m.status is not discord.Status.offline
                ) if guild.chunked else 0
                async with get_pool().acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO guild_snapshot (
                            guild_id, name, icon_url, member_count, online_count,
                            boost_count, boost_tier, channel_count, role_count,
                            created_at, updated_at
                        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10, NOW())
                        ON CONFLICT (guild_id) DO UPDATE SET
                            name = EXCLUDED.name,
                            icon_url = EXCLUDED.icon_url,
                            member_count = EXCLUDED.member_count,
                            online_count = EXCLUDED.online_count,
                            boost_count = EXCLUDED.boost_count,
                            boost_tier = EXCLUDED.boost_tier,
                            channel_count = EXCLUDED.channel_count,
                            role_count = EXCLUDED.role_count,
                            updated_at = NOW()
                        """,
                        str(guild.id), guild.name,
                        str(guild.icon.url) if guild.icon else None,
                        guild.member_count or 0, online,
                        guild.premium_subscription_count or 0,
                        guild.premium_tier or 0,
                        len(guild.channels), len(guild.roles),
                        guild.created_at,
                    )
            except Exception as e:
                print(f"[sync] snapshot failed for {guild.id}: {e}")

    @snapshot_loop.before_loop
    async def _before_snapshot(self):
        await self.bot.wait_until_ready()

    # ── hourly background sync loop ─────────────────────────────────────────

    @tasks.loop(hours=1)
    async def hourly_sync_loop(self):
        """Automated hourly sync to fetch the last 10 messages across all configured channels."""
        print("[sync/hourly] Starting automated background sync (last 10 messages)...", flush=True)
        total, failed = await self._perform_sync("all", limit=10)
        print(f"[sync/hourly] Complete. Synced {total} messages. Failed/skipped: {failed}", flush=True)

    @hourly_sync_loop.before_loop
    async def _before_hourly_sync(self):
        await self.bot.wait_until_ready()

    # ── sync core logic ──────────────────────────────────────────────────────

    async def _perform_sync(self, key: str = "all", limit: int = 10, status_msg=None) -> tuple[int, list[str]]:
        targets = (
            list(config.SYNCED_CHANNELS.items())
            if key == "all"
            else [(cid, k) for cid, k in config.SYNCED_CHANNELS.items() if k == key]
        )
        if not targets:
            return 0, [f"unknown key {key}"]

        total, failed = 0, []

        for cid, ckey in targets:
            channel = self.bot.get_channel(int(cid))
            if channel is None:
                failed.append(f"{ckey} (not found / no access)")
                continue
            try:
                count = 0
                # Forum channels do not have direct history; check if hasattr(channel, "history")
                if hasattr(channel, "history"):
                    async for msg in channel.history(limit=limit):
                        await self._upsert(msg, ckey)
                        count += 1

                # Forum and text channels can both carry threads
                threads = list(getattr(channel, "threads", []))
                if hasattr(channel, "archived_threads"):
                    try:
                        async for t in channel.archived_threads(limit=50):
                            threads.append(t)
                    except Exception:
                        pass

                for t in threads:
                    async for msg in t.history(limit=limit):
                        await self._upsert(msg, ckey)
                        count += 1

                total += count
                if status_msg:
                    await status_msg.edit(content=f"🔄  {ckey}: {count} messages…")
            except discord.Forbidden:
                failed.append(f"{ckey} (missing Read Message History)")
            except Exception as e:
                failed.append(f"{ckey} ({type(e).__name__})")

        return total, failed

    # ── commands ────────────────────────────────────────────────────────────

    @commands.command(name="syncchannel")
    @commands.has_permissions(administrator=True)
    async def sync_channel(self, ctx, key: str = "all", limit: int = 200):
        """Backfill synced channels from Discord history.

        Needed once after enabling this cog, and any time the bot was offline
        while messages were posted. Safe to re-run — upserts, never duplicates.
        """
        status = await ctx.send(f"🔄  Syncing channels, up to {limit} messages each…")
        total, failed = await self._perform_sync(key, limit, status)
        note = f"\n⚠️  Skipped: {', '.join(failed)}" if failed else ""
        await status.edit(content=f"✅  Synced {total} messages.{note}")

    @commands.command(name="syncultimate")
    @commands.has_permissions(administrator=True)
    async def sync_ultimate(self, ctx):
        """Sync entire history (all messages) across all configured channels."""
        status = await ctx.send("🔄  Starting syncultimate (complete history fetch - this may take a while)…")
        total, failed = await self._perform_sync("all", limit=None, status_msg=status)
        note = f"\n⚠️  Skipped: {', '.join(failed)}" if failed else ""
        await status.edit(content=f"✅  syncultimate completed. Synced {total} messages.{note}")

    @commands.command(name="syncall")
    @commands.has_permissions(administrator=True)
    async def sync_all(self, ctx):
        """Sync the last 10 messages across all configured channels."""
        status = await ctx.send("🔄  Starting syncall (last 10 messages of all channels)…")
        total, failed = await self._perform_sync("all", limit=10, status_msg=status)
        note = f"\n⚠️  Skipped: {', '.join(failed)}" if failed else ""
        await status.edit(content=f"✅  syncall completed. Synced {total} messages.{note}")

    @commands.command(name="sync")
    @commands.has_permissions(administrator=True)
    async def sync_single(self, ctx, key: str = None):
        """Sync the last 10 messages of a particular configured channel."""
        if not key:
            known = ", ".join(sorted(set(config.SYNCED_CHANNELS.values())))
            await ctx.send(f"❌  Usage: `!sync <channel_key>`\nKnown: {known}")
            return
        status = await ctx.send(f"🔄  Syncing channel `{key}` (last 10 messages)…")
        total, failed = await self._perform_sync(key, limit=10, status_msg=status)
        note = f"\n⚠️  Skipped: {', '.join(failed)}" if failed else ""
        await status.edit(content=f"✅  Sync completed for `{key}`. Synced {total} messages.{note}")

    @commands.command(name="syncstatus")
    @commands.has_permissions(administrator=True)
    async def sync_status(self, ctx):
        """Row counts and last sync time per channel."""
        async with get_pool().acquire() as conn:
            rows = await conn.fetch(
                """SELECT channel_key, COUNT(*) AS n, MAX(synced_at) AS last
                   FROM discord_messages GROUP BY channel_key ORDER BY channel_key"""
            )
            threads = await conn.fetchval("SELECT COUNT(*) FROM discord_threads")

        configured = set(config.SYNCED_CHANNELS.values())
        seen = {r["channel_key"] for r in rows}
        lines = [f"`{r['channel_key']:<20}` {r['n']:>5} msgs" for r in rows]
        for missing in sorted(configured - seen):
            lines.append(f"`{missing:<20}`     0 msgs  ⚠️ never synced")

        embed = discord.Embed(
            title="Website sync status",
            description="\n".join(lines) or "Nothing synced yet.",
            colour=discord.Colour.blurple(),
        )
        embed.set_footer(text=f"{threads} threads tracked · !syncall / !syncultimate to sync")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(WebsiteSync(bot))
