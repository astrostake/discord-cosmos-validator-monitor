# cogs/general_commands.py
# -*- coding: utf-8 -*-
"""General-purpose slash commands for the bot."""

import datetime
import logging
from typing import Union

import discord
from discord import app_commands
from discord.ext import commands

import db_manager
from utils import chain_autocomplete
from utils.embed_factory import create_validator_status_embed
from utils.governance_helpers import (
    extract_proposal_title, fetch_tally, format_tally_inline
)
from utils.retry import api_get_with_retry

logger = logging.getLogger(__name__)


class GeneralCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="help", description="Shows information about the bot and its commands.")
    async def help(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="Cosmos Validator Monitoring Bot",
            description="This bot provides real-time monitoring and alerting for Cosmos-based network validators.",
            color=discord.Color.blue(),
        )
        embed.add_field(name="Core Features", value="""
- **Multi-Chain Support**: Monitor validators across various chains.
- **Real-time Alerting**: Jailing, missed blocks, governance, upgrades, stake changes.
- **On-demand Status Checks**: Instantly check any validator's status.
- **Admin Controls**: Configure settings via Discord commands.
        """, inline=False)
        embed.add_field(name="Available Commands", value="""
- `/register` — Add a validator for monitoring.
- `/unregister` — Remove a validator from your list.
- `/myvalidators` — List all validators you are monitoring.
- `/validator_status` — Get an instant status report.
- `/set_chain_notifications` — Configure governance/upgrade alerts.
- `/active_proposals` — View active governance proposals.
- `/list_chains` — View all supported networks.
- `/test_notification` — Send a sample alert.
- `/admin status` — Bot health dashboard (admin only).
- `/admin set` — Change bot settings (admin only).
- `/admin reload` — Reload config.yaml (admin only).
        """, inline=False)
        embed.set_footer(text=f"Monitored by {self.bot.user.name}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="list_chains", description="Displays a list of all supported chains.")
    async def list_chains(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="Supported Networks",
            description="The following networks are currently configured.",
            color=discord.Color.green()
        )
        for chain_name, config in self.bot.supported_chains.items():
            details = (
                f"**Token:** {config.token_symbol}\n"
                f"**Monitoring:** {'Enabled' if config.missed_blocks_supported else 'Disabled'}"
            )
            embed.add_field(name=chain_name.upper(), value=details, inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="set_chain_notifications",
        description="Configure gov/upgrade notifications for this channel."
    )
    @app_commands.describe(
        chain_name="Name of the chain",
        enable_gov="Enable governance alerts (True/False)",
        enable_upgrade="Enable upgrade alerts (True/False)",
        mention="Optional: User or Role to mention"
    )
    @app_commands.autocomplete(chain_name=chain_autocomplete)
    async def set_chain_notifications(
        self, interaction: discord.Interaction,
        chain_name: str,
        enable_gov: bool,
        enable_upgrade: bool,
        mention: Union[discord.Role, discord.User] = None
    ):
        await interaction.response.defer(ephemeral=True)
        chain_name = chain_name.lower()
        if chain_name not in self.bot.supported_chains:
            await interaction.followup.send(f"❌ Error: Chain `{chain_name}` is not supported.")
            return

        mention_value = mention.mention if mention else "none"
        await db_manager.set_chain_notification_preference(
            interaction.channel_id, chain_name, enable_gov, enable_upgrade, mention_value
        )
        await interaction.followup.send(
            f"✅ Success: Notification preferences for **{chain_name.upper()}** "
            f"have been updated in this channel."
        )

    @app_commands.command(name="test_notification", description="Sends a sample notification to this channel.")
    async def test_notification(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        test_status = {
            'success': True,
            'moniker': 'TestValidator',
            'status': 'JAILED',
            'jailed': True,
            'missed_blocks': 120,
            'total_stake': '1,234,567.89 TST',
            'estimated_uptime': '98.80%',
            'estimated_uptime_percentage': 98.80
        }

        embed = await create_validator_status_embed(
            self.bot.user, "EXAMPLE-CHAIN", "examplevaloper1test...", test_status
        )
        embed.title = "🔴 Critical Alert: Validator Jailed (Test)"
        embed.description = "This is a test notification to confirm alerts are configured correctly."

        try:
            await interaction.channel.send(
                content=f"This is a test message for {interaction.user.mention}.",
                embed=embed
            )
            await interaction.followup.send("A test notification has been sent to this channel.")
        except discord.errors.Forbidden:
            await interaction.followup.send(
                "Error: The bot lacks permission to send messages in this channel."
            )
        except Exception as e:
            await interaction.followup.send(f"An unexpected error occurred: {e}")

    @app_commands.command(
        name="active_proposals",
        description="Displays active governance proposals and their tally."
    )
    @app_commands.describe(chain_name="Name of the chain")
    @app_commands.autocomplete(chain_name=chain_autocomplete)
    async def active_proposals(self, interaction: discord.Interaction, chain_name: str):
        await interaction.response.defer(ephemeral=False)

        chain_name = chain_name.lower()
        chain_config = self.bot.supported_chains.get(chain_name)

        if not chain_config:
            await interaction.followup.send(
                f"❌ Error: Chain `{chain_name.upper()}` is not supported."
            )
            return

        gov_api_url = f"{chain_config.rest_api_url}{chain_config.gov_proposals_endpoint}"

        try:
            response = await api_get_with_retry(
                self.bot.async_client, gov_api_url,
                max_retries=self.bot.settings.api_max_retries
            )

            proposals = [
                p for p in response.json().get('proposals', [])
                if p.get('status') == "PROPOSAL_STATUS_VOTING_PERIOD"
            ]

            if not proposals:
                await interaction.followup.send(
                    f"✅ There are currently no active governance proposals "
                    f"for **{chain_name.upper()}**."
                )
                return

            embed = discord.Embed(
                title=f"🗳️ Active Governance Proposals for {chain_name.upper()}",
                color=discord.Color.orange(),
                timestamp=datetime.datetime.now(datetime.timezone.utc)
            )

            for prop in proposals[:10]:
                prop_id = prop.get('id') or prop.get('proposal_id', 'N/A')
                prop_title = extract_proposal_title(prop)

                # Fetch tally using shared helper
                tally_url = chain_config.get_tally_endpoint(str(prop_id))
                tally = await fetch_tally(self.bot.async_client, tally_url)
                tally_text = format_tally_inline(tally)

                voting_end_time_str = prop.get('voting_end_time')
                voting_ends_text = ""
                if voting_end_time_str:
                    try:
                        end_dt = datetime.datetime.fromisoformat(
                            voting_end_time_str.replace('Z', '+00:00')
                        )
                        voting_ends_text = f" • Ends <t:{int(end_dt.timestamp())}:R>"
                    except ValueError:
                        pass

                embed.add_field(
                    name=f"#{prop_id}: {prop_title}",
                    value=f"**Tally:** `{tally_text}`{voting_ends_text}",
                    inline=False
                )

            embed.set_footer(text=f"Monitored by {self.bot.user.name}")
            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Error fetching active proposals for {chain_name}: {e}")
            await interaction.followup.send(
                f"An error occurred while fetching proposals for **{chain_name.upper()}**. "
                f"Please try again later."
            )


async def setup(bot: commands.Bot):
    """Required function to load the Cog."""
    await bot.add_cog(GeneralCommands(bot))