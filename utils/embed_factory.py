# utils/embed_factory.py
# -*- coding: utf-8 -*-

import datetime
import discord
from typing import Dict

# Impor helper dari file utilitas lainnya
from .api_helpers import create_progress_bar

async def create_validator_status_embed(bot_user: discord.User, chain_name: str, val_addr: str, status_info: Dict) -> discord.Embed:
    """Membuat discord.Embed dari dictionary informasi status validator."""
    if status_info.get('success'):
        color = discord.Color.red() if status_info.get('jailed') else discord.Color.blue()
        
        embed = discord.Embed(
            title=f"Validator Status: {status_info.get('moniker', 'N/A')}",
            description=f"Chain: **{chain_name.upper()}**\nAddress: `{val_addr}`",
            color=color,
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        embed.add_field(name="Status", value=status_info.get('status', 'N/A'), inline=True)
        embed.add_field(name="Jailed", value="Yes" if status_info.get('jailed') else "No", inline=True)
        
        missed_blocks_val = "N/A"
        if (mb := status_info.get('missed_blocks', -1)) != -1:
            missed_blocks_val = str(mb)
        embed.add_field(name="Missed Blocks", value=missed_blocks_val, inline=True)
        
        embed.add_field(name="Total Stake", value=status_info.get('total_stake', 'N/A'), inline=True)
        
        uptime_perc = status_info.get('estimated_uptime_percentage', 0.0)
        uptime_bar = create_progress_bar(uptime_perc)
        embed.add_field(
            name="Estimated Uptime",
            value=f"`{uptime_bar}` **{status_info.get('estimated_uptime', 'N/A')}**",
            inline=False
        )
    else:
        embed = discord.Embed(
            title=f"🔴 Error: Validator Data Retrieval Failed",
            description=f"Could not retrieve status for `{val_addr}` on **{chain_name.upper()}**.\n**Reason:** `{status_info.get('error', 'Unknown error')}`",
            color=discord.Color.dark_red(),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
    
    embed.set_footer(text=f"Monitored by {bot_user.name}")
    return embed