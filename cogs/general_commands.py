# cogs/general_commands.py
# -*- coding: utf-8 -*-

import discord
from discord import app_commands
from discord.ext import commands
import datetime

import db_manager
from utils.embed_factory import create_validator_status_embed # Diperlukan untuk test_notification

class GeneralCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="help", description="Shows information about the bot and its commands.")
    async def help(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="Cosmos Validator Monitoring Bot",
            description="This bot provides monitoring and alerting for Cosmos-based network validators.",
            color=discord.Color.blue(),
        )
        embed.add_field(name="Core Features", value="""
- **Multi-Chain Support**: Monitor validators across various supported networks.
- **Real-time Alerting**: Get notified for jailing, status changes, and missed blocks.
- **Governance & Upgrade Tracking**: Stay informed about proposals and network upgrades.
- **On-demand Status Checks**: Instantly check any validator's status.
        """, inline=False)
        embed.add_field(name="Available Commands", value="""
- `/register`: Add a validator for monitoring.
- `/unregister`: Remove a validator from your list.
- `/myvalidators`: List all validators you are monitoring.
- `/validator_status`: Get an instant status report for a validator.
- `/set_chain_notifications`: Configure governance/upgrade notifications for a chain in this channel.
- `/list_chains`: View all supported networks.
- `/test_notification`: Send a sample alert to this channel.
        """, inline=False)
        embed.set_footer(text="Your reliable Cosmos companion.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="list_chains", description="Displays a list of all supported chains.")
    async def list_chains(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="Supported Networks",
            description="The following networks are supported by the monitoring service.",
            color=discord.Color.green()
        )
        for chain_id, config in self.bot.supported_chains.items():
            details = (
                f"**Token:** {config['token_symbol']}\n"
                f"**Monitoring:** {'Enabled' if config['missed_blocks_supported'] else 'Disabled'}"
            )
            embed.add_field(name=chain_id.upper(), value=details, inline=True)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="set_chain_notifications", description="Configure gov/upgrade notifications for this channel.")
    @app_commands.describe(chain_name="Name of the chain", enable_gov="Enable governance alerts", enable_upgrade="Enable upgrade alerts")
    async def set_chain_notifications(self, interaction: discord.Interaction, chain_name: str, enable_gov: bool, enable_upgrade: bool):
        await interaction.response.defer(ephemeral=True)
        chain_name = chain_name.lower()
        if chain_name not in self.bot.supported_chains:
            await interaction.followup.send(f"Error: Chain `{chain_name}` is not supported.")
            return

        db_manager.set_chain_notification_preference(interaction.channel_id, chain_name, enable_gov, enable_upgrade, None) # Mention type bisa ditambahkan lagi
        await interaction.followup.send(f"✅ Success: Notification preferences for **{chain_name.upper()}** have been updated in this channel.")

    @app_commands.command(name="test_notification", description="Sends a sample notification to this channel.")
    async def test_notification(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        # Kita bisa memanggil embed factory untuk membuat embed tes yang realistis
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
        
        embed = await create_validator_status_embed(self.bot.user, "EXAMPLE-CHAIN", "examplevaloper1test...", test_status)
        embed.title = "🔴 Critical Alert: Validator Jailed (Test)"
        embed.description = "This is a test notification to confirm alerts are configured correctly."

        try:
            await interaction.channel.send(content=f"This is a test message for {interaction.user.mention}.", embed=embed)
            await interaction.followup.send("A test notification has been sent to this channel.")
        except discord.errors.Forbidden:
            await interaction.followup.send("Error: The bot lacks permission to send messages in this channel.")
        except Exception as e:
            await interaction.followup.send(f"An unexpected error occurred: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(GeneralCommands(bot))