# utils/__init__.py
# -*- coding: utf-8 -*-
"""Shared utilities for the Cosmos Validator Monitor bot."""

from discord import app_commands
import discord


async def chain_autocomplete(interaction: discord.Interaction, current: str):
    """Autocomplete callback for chain_name parameters across all commands."""
    chains = list(interaction.client.supported_chains.keys())
    return [
        app_commands.Choice(name=c.upper(), value=c)
        for c in chains if current.lower() in c.lower()
    ][:25]


async def user_validator_autocomplete(interaction: discord.Interaction, current: str):
    """Autocomplete callback for validator_address based on user's registered validators."""
    import db_manager
    validators = await db_manager.get_user_validators(interaction.user.id)
    results = []
    for chain, addr, moniker, status, _ in validators:
        label = f"{chain.upper()}: {moniker or addr[:20]}" 
        if current.lower() in addr.lower() or current.lower() in (moniker or '').lower():
            results.append(app_commands.Choice(name=label[:100], value=addr))
    return results[:25]
