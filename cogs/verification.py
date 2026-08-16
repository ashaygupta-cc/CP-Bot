"""
cogs/verification.py
Verification flow for new members.

Flow:
  1. Member joins -> bot posts in #verification with two buttons.
  2. Member clicks "Follow on LinkedIn":
       - Timestamp recorded in memory.
       - Ephemeral reply with a direct clickable LinkedIn link (dismissed automatically).
  3. Member clicks "I Have Followed":
       - Not clicked "Follow on LinkedIn" at all -> rejected.
       - Clicked but less than 10 seconds ago -> rejected with remaining wait.
       - Clicked 10+ seconds ago -> verified, role granted.

Admin commands:
  !sendverification   -> Manually post verification prompt in current channel.
  !reverify @user     -> Reset a user back to verification state.
  !verifyall          -> DM all unverified members with instructions.
  !verificationstatus -> Show pending verifications.

Config (add to .env):
  VERIFICATION_CHANNEL = verification
  WELCOME_CHANNEL      = general
  LINKEDIN_URL         = https://linkedin.com/company/yourpage
  VERIFICATION_ROLE    = Verification
  MEMBER_ROLE          = Member
"""

import discord
from discord.ext import commands
import asyncio
import time

from config import (
    ADMIN_ROLE,
    COLOR_SUCCESS, COLOR_INFO, COLOR_ERROR, COLOR_WARN,
    VERIFICATION_CHANNEL, WELCOME_CHANNEL, LINKEDIN_URL,
    VERIFICATION_ROLE_NAME, MEMBER_ROLE_NAME,
)

# Seconds user must wait after clicking "Follow on LinkedIn"
# before "I Have Followed" is accepted.
FOLLOW_COOLDOWN = 10

# { user_id: timestamp_of_follow_click }
_follow_clicked_at: dict[int, float] = {}


class VerificationView(discord.ui.View):
    """
    Persistent view (timeout=None) — survives bot restarts.

    Button 1 — "Follow on LinkedIn" (primary, non-URL):
      Records click timestamp, sends ephemeral with direct link.

    Button 2 — "I Have Followed" (success):
      Checks timestamp; enforces FOLLOW_COOLDOWN seconds gap.
    """

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Follow on LinkedIn",
        style=discord.ButtonStyle.primary,
        custom_id="verification:track_follow",
        row=0,
    )
    async def track_follow_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        _follow_clicked_at[interaction.user.id] = time.monotonic()
        await interaction.response.send_message(
            embed=discord.Embed(
                description=f"[Open LinkedIn page]({LINKEDIN_URL})\n\nFollow the page, then click **I Have Followed**.",
                color=COLOR_INFO,
            ),
            ephemeral=True,
        )

    @discord.ui.button(
        label="I Have Followed",
        style=discord.ButtonStyle.success,
        custom_id="verification:followed",
        row=0,
    )
    async def followed_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        guild  = interaction.guild
        member = interaction.user

        verif_role  = discord.utils.get(guild.roles, name=VERIFICATION_ROLE_NAME)
        member_role = discord.utils.get(guild.roles, name=MEMBER_ROLE_NAME)

        if not member_role:
            await interaction.response.send_message(
                f"Setup error: role **{MEMBER_ROLE_NAME}** not found. Contact an admin.",
                ephemeral=True,
            )
            return

        if member_role in member.roles:
            await interaction.response.send_message(
                "You are already verified and have full server access.",
                ephemeral=True,
            )
            return

        clicked_at = _follow_clicked_at.get(member.id)

        # Never clicked "Follow on LinkedIn"
        if clicked_at is None:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Visit LinkedIn first",
                    description=(
                        "Click **Follow on LinkedIn** and follow our page before confirming."
                    ),
                    color=COLOR_ERROR,
                ),
                ephemeral=True,
            )
            return

        # Silent simulation gate — user clicked too fast, meaning they likely
        # never visited the LinkedIn page. Don't reveal the timer; just prompt
        # them to actually follow first. Legitimate followers who spent time
        # on the page will naturally pass this check on their next click.
        elapsed = time.monotonic() - clicked_at
        if elapsed < FOLLOW_COOLDOWN:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="One more step",
                    description=(
                        f"Please visit and **follow** our LinkedIn page first:\n"
                        f"[Open LinkedIn page]({LINKEDIN_URL})\n\n"
                        f"Once you've followed, come back and click **I Have Followed**."
                    ),
                    color=COLOR_WARN,
                ),
                ephemeral=True,
            )
            return

        # -- All checks passed — grant access ----------------------------------
        try:
            if verif_role and verif_role in member.roles:
                await member.remove_roles(verif_role, reason="Completed LinkedIn verification")
            await member.add_roles(member_role, reason="Completed LinkedIn verification")
        except discord.Forbidden:
            await interaction.response.send_message(
                "The bot does not have permission to manage roles. Contact an admin.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(
                f"Role update failed: `{e}`. Try again or contact an admin.",
                ephemeral=True,
            )
            return

        _follow_clicked_at.pop(member.id, None)

        await interaction.response.send_message(
            embed=discord.Embed(
                title="Verified",
                description=(
                    f"You now have full access to **{guild.name}**. Welcome."
                ),
                color=COLOR_SUCCESS,
            ),
            ephemeral=True,
        )

        welcome_ch = discord.utils.get(guild.text_channels, name=WELCOME_CHANNEL)
        if welcome_ch:
            embed = discord.Embed(
                title="New member",
                description=(
                    f"{member.mention} has joined **{guild.name}**.\n\n"
                    f"Use `!register` to link your competitive programming handles "
                    f"and start tracking your progress."
                ),
                color=COLOR_SUCCESS,
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_footer(text="Binary Beats — Competitive Programming Community")
            await welcome_ch.send(embed=embed)


def _make_verification_embed(guild: discord.Guild) -> discord.Embed:
    embed = discord.Embed(
        title="Verification required",
        description=(
            f"Welcome to **{guild.name}**.\n\n"
            "**Step 1** — Click **Follow on LinkedIn** and follow our page.\n"
            "**Step 2** — Return here and click **I Have Followed**.\n\n"
            "*Both steps are required.*"
        ),
        color=COLOR_INFO,
    )
    embed.set_footer(text="Need help? DM an admin.")
    return embed


class Verification(commands.Cog):
    """Member verification — LinkedIn follow with cooldown enforcement."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.add_view(VerificationView())

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild

        verif_role = discord.utils.get(guild.roles, name=VERIFICATION_ROLE_NAME)
        if verif_role:
            try:
                await member.add_roles(verif_role, reason="New member — pending verification")
            except (discord.Forbidden, discord.HTTPException) as e:
                print(f"[verification] Could not assign {VERIFICATION_ROLE_NAME} to {member}: {e}")

        verif_ch = discord.utils.get(guild.text_channels, name=VERIFICATION_CHANNEL)
        if verif_ch:
            embed = _make_verification_embed(guild)
            embed.description = f"{member.mention} just joined.\n\n" + (embed.description or "")
            await verif_ch.send(content=member.mention, embed=embed, view=VerificationView())

    @commands.command(name="sendverification", aliases=["sendverify"])
    async def send_verification(self, ctx):
        """(Admin) Manually post the verification prompt in the current channel."""
        if not (
            any(r.name == ADMIN_ROLE for r in ctx.author.roles)
            or ctx.author.guild_permissions.administrator
        ):
            await ctx.send(f"You need the **{ADMIN_ROLE}** role.", delete_after=5)
            return
        await ctx.send(embed=_make_verification_embed(ctx.guild), view=VerificationView())

    @commands.command(name="reverify")
    async def reverify(self, ctx, member: discord.Member = None):
        """(Admin) Reset a member back to verification state."""
        if not (
            any(r.name == ADMIN_ROLE for r in ctx.author.roles)
            or ctx.author.guild_permissions.administrator
        ):
            await ctx.send(f"You need the **{ADMIN_ROLE}** role.", delete_after=5)
            return

        if not member:
            await ctx.send("Usage: `!reverify @user`")
            return

        guild       = ctx.guild
        verif_role  = discord.utils.get(guild.roles, name=VERIFICATION_ROLE_NAME)
        member_role = discord.utils.get(guild.roles, name=MEMBER_ROLE_NAME)

        try:
            if member_role and member_role in member.roles:
                await member.remove_roles(member_role, reason=f"Re-verification by {ctx.author}")
            if verif_role and verif_role not in member.roles:
                await member.add_roles(verif_role, reason=f"Re-verification by {ctx.author}")
        except discord.Forbidden:
            await ctx.send("Bot lacks permission to manage roles.")
            return

        _follow_clicked_at.pop(member.id, None)

        verif_ch = discord.utils.get(guild.text_channels, name=VERIFICATION_CHANNEL)
        if verif_ch:
            embed = _make_verification_embed(guild)
            embed.description = (
                f"{member.mention}, please re-complete verification below.\n\n"
                + (embed.description or "")
            )
            await verif_ch.send(content=member.mention, embed=embed, view=VerificationView())

        await ctx.send(f"{member.mention} has been returned to verification.", delete_after=10)

    @commands.command(name="verifyall")
    async def verify_all(self, ctx):
        """(Admin) DM all unverified members with instructions."""
        if not (
            any(r.name == ADMIN_ROLE for r in ctx.author.roles)
            or ctx.author.guild_permissions.administrator
        ):
            await ctx.send(f"You need the **{ADMIN_ROLE}** role.", delete_after=5)
            return

        guild       = ctx.guild
        verif_role  = discord.utils.get(guild.roles, name=VERIFICATION_ROLE_NAME)
        member_role = discord.utils.get(guild.roles, name=MEMBER_ROLE_NAME)

        if not member_role:
            await ctx.send(f"Role **{MEMBER_ROLE_NAME}** not found. Create it first.")
            return

        unverified = [m for m in guild.members if not m.bot and member_role not in m.roles]

        if not unverified:
            await ctx.send("All members are already verified.")
            return

        status_msg = await ctx.send(
            f"Sending verification DMs to **{len(unverified)}** member(s). This may take a moment."
        )

        dm_sent = 0
        dm_failed = 0

        for m in unverified:
            if verif_role and verif_role not in m.roles:
                try:
                    await m.add_roles(verif_role, reason="verifyall — pending verification")
                except (discord.Forbidden, discord.HTTPException):
                    pass
            try:
                embed = discord.Embed(
                    title="Action required — verification",
                    description=(
                        f"**{guild.name}** requires all members to verify their LinkedIn follow.\n\n"
                        f"Head to **#verification** in the server and complete both steps."
                    ),
                    color=COLOR_INFO,
                )
                embed.set_footer(text=f"{guild.name} — Binary Beats")
                await m.send(embed=embed)
                dm_sent += 1
            except (discord.Forbidden, discord.HTTPException):
                dm_failed += 1
            await asyncio.sleep(0.8)

        verif_ch = discord.utils.get(guild.text_channels, name=VERIFICATION_CHANNEL)
        if verif_ch:
            embed = _make_verification_embed(guild)
            embed.description = (
                "**All members — please complete verification below.**\n\n"
                + (embed.description or "")
            )
            await verif_ch.send(embed=embed, view=VerificationView())

        report = discord.Embed(title="Verify all — complete", color=COLOR_SUCCESS)
        report.add_field(name="Total unverified",    value=str(len(unverified)), inline=True)
        report.add_field(name="DMs sent",            value=str(dm_sent),         inline=True)
        report.add_field(name="DMs failed (closed)", value=str(dm_failed),       inline=True)
        report.add_field(
            name="Next step",
            value=(
                f"Members with closed DMs will see the prompt in <#{verif_ch.id}>."
                if verif_ch else "Run `!sendverification` in your verification channel."
            ),
            inline=False,
        )
        report.set_footer(text=f"Run by {ctx.author.display_name}")
        await status_msg.edit(content=None, embed=report)

    @commands.command(name="verificationstatus", aliases=["vstatus"])
    async def verification_status(self, ctx):
        """(Admin) List all members currently pending verification."""
        if not (
            any(r.name == ADMIN_ROLE for r in ctx.author.roles)
            or ctx.author.guild_permissions.administrator
        ):
            await ctx.send(f"You need the **{ADMIN_ROLE}** role.", delete_after=5)
            return

        verif_role  = discord.utils.get(ctx.guild.roles, name=VERIFICATION_ROLE_NAME)
        member_role = discord.utils.get(ctx.guild.roles, name=MEMBER_ROLE_NAME)

        if not verif_role:
            await ctx.send(f"Role **{VERIFICATION_ROLE_NAME}** not found.")
            return

        pending  = [m for m in ctx.guild.members if verif_role in m.roles and not m.bot]
        verified = [m for m in ctx.guild.members if member_role and member_role in m.roles and not m.bot]

        embed = discord.Embed(title="Verification status", color=COLOR_INFO)
        embed.add_field(
            name=f"Pending ({len(pending)})",
            value="\n".join(m.mention for m in pending[:15]) or "*None*",
            inline=False,
        )
        embed.add_field(
            name=f"Verified ({len(verified)})",
            value=f"*{len(verified)} member(s) with {MEMBER_ROLE_NAME} role*",
            inline=False,
        )
        embed.set_footer(text=f"Total server members: {ctx.guild.member_count}")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Verification(bot))