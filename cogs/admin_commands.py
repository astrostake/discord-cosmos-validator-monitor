# cogs/admin_commands.py
# -*- coding: utf-8 -*-
"""Admin-only commands for bot configuration and health monitoring.

Access control: Requires either 'Manage Server' Discord permission
or inclusion in the admin_user_ids configuration list.
"""

import datetime
import logging

import discord
from discord import app_commands
from discord.ext import commands

import db_manager

logger = logging.getLogger(__name__)


def is_bot_admin():
    """Check decorator: requires Manage Server permission OR admin_user_ids membership."""
    async def predicate(interaction: discord.Interaction) -> bool:
        bot = interaction.client
        is_admin_user = bot.is_admin(interaction.user.id)

        if not is_admin_user:
            await interaction.response.send_message(
                "❌ You must be a registered **Bot Admin** to use this command.",
                ephemeral=True
            )
            return False
        return True

    return app_commands.check(predicate)


class AdminCommands(commands.Cog):
    """Admin commands grouped under /admin."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    admin_group = app_commands.Group(
        name="admin",
        description="Bot administration commands (requires Manage Server permission)."
    )

    @admin_group.command(name="status", description="Show bot health status and statistics.")
    @is_bot_admin()
    async def bot_status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        stats = await db_manager.get_monitoring_stats()
        uptime = self.bot.uptime
        hours, remainder = divmod(int(uptime.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)

        embed = discord.Embed(
            title="🤖 Bot Status Dashboard",
            color=discord.Color.blue(),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )

        # Uptime & basic stats
        embed.add_field(name="⏱️ Uptime", value=f"`{hours}h {minutes}m {seconds}s`", inline=True)
        embed.add_field(
            name="📡 Chains", value=f"`{len(self.bot.supported_chains)}`", inline=True
        )
        embed.add_field(name="👥 Users", value=f"`{stats['unique_users']}`", inline=True)
        embed.add_field(
            name="✅ Active Monitors", value=f"`{stats['active_validators']}`", inline=True
        )
        embed.add_field(
            name="🔴 Jailed", value=f"`{stats['jailed_validators']}`", inline=True
        )
        embed.add_field(
            name="⚠️ API Errors", value=f"`{stats['api_error_validators']}`", inline=True
        )

        # Current settings
        s = self.bot.settings
        embed.add_field(
            name="⚙️ Current Settings",
            value=(
                f"Monitor Interval: `{s.monitor_interval_seconds}s`\n"
                f"Gov Check Interval: `{s.governance_check_interval_seconds}s`\n"
                f"Upgrade Check Interval: `{s.upgrade_check_interval_seconds}s`\n"
                f"Missed Blocks Threshold: `{s.missed_blocks_threshold}`\n"
                f"Min Stake Change: `{s.min_stake_change_amount}`\n"
                f"API Timeout: `{s.api_timeout}s` | Retries: `{s.api_max_retries}`"
            ),
            inline=False
        )

        # Per-chain API health
        monitoring_cog = self.bot.get_cog('MonitoringTasks')
        if monitoring_cog:
            chain_lines = []
            for chain_name in self.bot.supported_chains:
                error_info = monitoring_cog._chain_api_error_status.get(chain_name, {})
                icon = "🔴" if error_info.get("is_error") else "🟢"
                chain_lines.append(f"{icon} {chain_name.upper()}")
            if chain_lines:
                embed.add_field(
                    name="🔗 Chain API Status",
                    value="\n".join(chain_lines),
                    inline=False
                )

        embed.set_footer(text=f"Monitored by {self.bot.user.name}")
        await interaction.followup.send(embed=embed)

    @admin_group.command(name="set", description="Update a bot setting at runtime.")
    @app_commands.describe(key="Setting name", value="New value")
    @app_commands.choices(key=[
        app_commands.Choice(name="Monitor Interval (seconds)", value="monitor_interval_seconds"),
        app_commands.Choice(name="Gov Check Interval (seconds)", value="governance_check_interval_seconds"),
        app_commands.Choice(name="Upgrade Check Interval (seconds)", value="upgrade_check_interval_seconds"),
        app_commands.Choice(name="Missed Blocks Threshold", value="missed_blocks_threshold"),
        app_commands.Choice(name="Min Stake Change Amount", value="min_stake_change_amount"),
        app_commands.Choice(name="API Timeout (seconds)", value="api_timeout"),
        app_commands.Choice(name="API Max Retries", value="api_max_retries"),
        app_commands.Choice(name="Log Level", value="log_level"),
    ])
    @is_bot_admin()
    async def config_set(
        self, interaction: discord.Interaction,
        key: app_commands.Choice[str], value: str
    ):
        await interaction.response.defer(ephemeral=True)

        old_value = getattr(self.bot.settings, key.value, None)

        if self.bot.settings.update(key.value, value):
            # Persist to database for survival across restarts
            await db_manager.set_runtime_setting(key.value, value)

            # Restart task loop if interval changed
            monitoring_cog = self.bot.get_cog('MonitoringTasks')
            if monitoring_cog:
                await monitoring_cog.restart_task_if_interval_changed(key.value)

            await interaction.followup.send(
                f"✅ Setting **{key.name}** updated: `{old_value}` → `{value}`"
            )
            logger.info(
                f"Admin {interaction.user} changed {key.value}: {old_value} -> {value}"
            )
        else:
            await interaction.followup.send(
                f"❌ Failed to update setting `{key.name}`. Invalid value: `{value}`"
            )

    @admin_group.command(
        name="reload",
        description="Reload chain configurations from config.yaml without restarting."
    )
    @is_bot_admin()
    async def reload_config(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            from settings import load_config
            _, new_chains = load_config()
            self.bot.supported_chains = new_chains

            # Update chain API error tracking for new chains
            monitoring_cog = self.bot.get_cog('MonitoringTasks')
            if monitoring_cog:
                for chain_name in new_chains:
                    if chain_name not in monitoring_cog._chain_api_error_status:
                        monitoring_cog._chain_api_error_status[chain_name] = {
                            "is_error": False, "last_error": None
                        }

            await interaction.followup.send(
                f"✅ Reloaded **{len(new_chains)}** chain configurations from `config.yaml`.\n"
                f"Chains: {', '.join(c.upper() for c in new_chains)}"
            )
            logger.info(f"Admin {interaction.user} reloaded config: {len(new_chains)} chains.")
        except Exception as e:
            logger.error(f"Config reload failed: {e}")
            await interaction.followup.send(f"❌ Failed to reload config: `{e}`")

    @admin_group.command(
        name="list_settings",
        description="Show all current bot settings with their values."
    )
    @is_bot_admin()
    async def list_settings(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        s = self.bot.settings
        lines = []
        for field_name in s.__dataclass_fields__:
            if field_name == 'RUNTIME_MUTABLE_KEYS':
                continue
            val = getattr(s, field_name)
            mutable = "✏️" if field_name in s.RUNTIME_MUTABLE_KEYS else "🔒"
            lines.append(f"{mutable} **{field_name}**: `{val}`")

        embed = discord.Embed(
            title="⚙️ All Bot Settings",
            description="\n".join(lines) + "\n\n✏️ = Editable via `/admin set` | 🔒 = Config file only",
            color=discord.Color.blue()
        )
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    """Required function to load the Cog."""
    await bot.add_cog(AdminCommands(bot))
