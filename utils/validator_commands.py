# cogs/validator_commands.py
# -*- coding: utf-8 -*-

import discord
from discord import app_commands
from discord.ext import commands

import db_manager
from utils.api_helpers import get_validator_info
from utils.embed_factory import create_validator_status_embed

class ValidatorCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="register", description="Registers a validator for monitoring.")
    @app_commands.describe(chain_name="Name of the chain", validator_address="Validator address")
    async def register(self, interaction: discord.Interaction, chain_name: str, validator_address: str):
        await interaction.response.defer(ephemeral=True)
        chain_name = chain_name.lower()
        chain_config = self.bot.supported_chains.get(chain_name)

        if not chain_config:
            await interaction.followup.send(f"Error: Chain `{chain_name}` is not supported.")
            return

        if not validator_address.startswith(chain_config["valoper_prefix"]):
            await interaction.followup.send(f"Error: Invalid address format for `{chain_name.upper()}`.")
            return

        status_info = await get_validator_info(self.bot.async_client, chain_config, validator_address, {}, {})
        if not status_info.get('success'):
            await interaction.followup.send(f"Error: Could not find validator `{validator_address}` on `{chain_name.upper()}`.")
            return

        moniker = status_info['moniker']
        if db_manager.add_validator(interaction.user.id, interaction.channel.id, chain_name, validator_address, moniker):
            await interaction.followup.send(f"✅ Success: Validator `{moniker}` on **{chain_name.upper()}** is now being monitored in this channel.", ephemeral=False)
        else:
            await interaction.followup.send(f"ℹ️ Info: Validator `{validator_address}` is already registered for monitoring.")

    @app_commands.command(name="unregister", description="Removes a validator from your monitoring list.")
    @app_commands.describe(chain_name="Chain name", validator_address="Validator address")
    async def unregister(self, interaction: discord.Interaction, chain_name: str, validator_address: str):
        chain_name = chain_name.lower()
        if chain_name not in self.bot.supported_chains:
            await interaction.response.send_message(f"Error: Chain `{chain_name}` is not supported.", ephemeral=True)
            return

        if db_manager.remove_validator(interaction.user.id, chain_name, validator_address):
            await interaction.response.send_message(f"✅ Success: Validator `{validator_address}` on **{chain_name.upper()}** has been removed.", ephemeral=True)
        else:
            await interaction.response.send_message(f"ℹ️ Info: Validator `{validator_address}` was not found in your monitoring list.", ephemeral=True)

    @app_commands.command(name="myvalidators", description="Displays a list of all your registered validators.")
    async def myvalidators(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        validators = db_manager.get_user_validators(interaction.user.id)
        if not validators:
            await interaction.followup.send("You are not currently monitoring any validators.")
            return

        embeds = []
        for chain, val_addr, _, _, _ in validators:
            chain_config = self.bot.supported_chains.get(chain)
            status_info = await get_validator_info(self.bot.async_client, chain_config, val_addr, {}, {})
            embed = await create_validator_status_embed(self.bot.user, chain, val_addr, status_info)
            embeds.append(embed)
        
        # Kirim embed dalam batch 10 untuk menghindari limit Discord
        for i in range(0, len(embeds), 10):
            await interaction.followup.send(embeds=embeds[i:i+10])

    @app_commands.command(name="validator_status", description="Gets instant status for a specific validator.")
    @app_commands.describe(chain_name="Name of the chain", validator_address="Validator address")
    async def validator_status(self, interaction: discord.Interaction, chain_name: str, validator_address: str):
        await interaction.response.defer(ephemeral=False)
        chain_name = chain_name.lower()
        chain_config = self.bot.supported_chains.get(chain_name)
        if not chain_config:
            await interaction.followup.send(f"Error: Chain `{chain_name}` is not supported.")
            return

        status_info = await get_validator_info(self.bot.async_client, chain_config, validator_address, {}, {})
        embed = await create_validator_status_embed(self.bot.user, chain_name, validator_address, status_info)
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(ValidatorCommands(bot))