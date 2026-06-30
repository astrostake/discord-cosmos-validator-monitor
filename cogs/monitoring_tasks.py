# cogs/monitoring_tasks.py
# -*- coding: utf-8 -*-
"""Background monitoring loops for validators, governance, and upgrades.

Key design decisions:
- API recovery grace: When recovering from API_ERROR or initial UNKNOWN state,
  the bot silently updates the DB baseline without sending notifications.
  This prevents notification spam when an API goes down and comes back up.
- All intervals and thresholds are read from bot.settings at runtime.
- Governance and tally logic uses shared helpers to avoid duplication.
"""

import datetime
import logging

import discord
from discord.ext import commands, tasks

import db_manager
from utils.api_helpers import create_progress_bar, get_validator_info, get_latest_block_height
from utils.governance_helpers import (
    extract_proposal_title, fetch_tally, format_tally_block, get_mention_string
)
from utils.retry import api_get_with_retry

logger = logging.getLogger(__name__)


class MonitoringTasks(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # Caches for slashing data, governance proposals, and upgrade plans
        self._slashing_info_cache = {}
        self._slashing_params_cache = {}
        self._governance_proposals_cache = {}
        self._upgrade_plan_cache = {}

        # Per-chain API health tracking
        self._chain_api_error_status = {
            chain_name: {"is_error": False, "last_error": None}
            for chain_name in self.bot.supported_chains
        }

        # Start all monitoring loops
        self.monitor_validators.start()
        self.monitor_governance.start()
        self.monitor_upgrades.start()

    def cog_unload(self):
        """Stop all task loops when the cog is unloaded."""
        self.monitor_validators.cancel()
        self.monitor_governance.cancel()
        self.monitor_upgrades.cancel()

    async def restart_task_if_interval_changed(self, setting_key: str):
        """Restart the relevant task loop when its interval setting changes."""
        task_map = {
            'monitor_interval_seconds': self.monitor_validators,
            'governance_check_interval_seconds': self.monitor_governance,
            'upgrade_check_interval_seconds': self.monitor_upgrades,
        }
        task = task_map.get(setting_key)
        if task:
            new_interval = getattr(self.bot.settings, setting_key)
            task.change_interval(seconds=new_interval)
            logger.info(f"Task loop for '{setting_key}' interval changed to {new_interval}s.")

    # =========================================================================
    # Validator Monitoring
    # =========================================================================

    @tasks.loop(seconds=60)  # Default; overridden in before_loop via settings
    async def monitor_validators(self):
        """Main validator monitoring loop."""
        logger.info("Running validator monitoring loop...")

        # 1. Refresh slashing cache for all chains
        for chain_name, chain_config in self.bot.supported_chains.items():
            if not chain_config.missed_blocks_supported:
                continue

            try:
                params_url = f"{chain_config.rest_api_url}{chain_config.slashing_params_endpoint}"
                params_response = await api_get_with_retry(
                    self.bot.async_client, params_url,
                    max_retries=self.bot.settings.api_max_retries,
                    backoff_base=self.bot.settings.api_retry_backoff,
                )
                self._slashing_params_cache[chain_name] = params_response.json().get('params', {})

                slashing_url = f"{chain_config.rest_api_url}{chain_config.signing_infos_endpoint}"
                slashing_response = await api_get_with_retry(
                    self.bot.async_client, slashing_url,
                    max_retries=self.bot.settings.api_max_retries,
                    backoff_base=self.bot.settings.api_retry_backoff,
                )
                self._slashing_info_cache[chain_name] = {
                    item['address']: item
                    for item in slashing_response.json().get('info', [])
                }

                # Mark API as healthy
                if self._chain_api_error_status.get(chain_name, {}).get("is_error"):
                    logger.info(f"Chain API for {chain_name} has recovered.")
                    self._chain_api_error_status[chain_name] = {
                        "is_error": False, "last_error": None
                    }

            except Exception as e:
                logger.error(f"Failed to update slashing cache for {chain_name}: {e}")
                self._slashing_params_cache[chain_name] = {}
                self._slashing_info_cache[chain_name] = {}
                self._chain_api_error_status[chain_name] = {
                    "is_error": True, "last_error": str(e)
                }

        # 2. Check all registered validators
        validators_to_monitor = await db_manager.get_all_validators_to_monitor()
        for val_data in validators_to_monitor:
            await self._check_and_notify_validator(val_data)

    async def _check_and_notify_validator(self, val_data):
        """Check a single validator and send notifications if needed.

        CRITICAL FIX: When recovering from API_ERROR or initial UNKNOWN state,
        the bot silently updates the DB without sending notifications to prevent
        spam. Only JAILED alerts bypass the recovery grace period.
        """
        chain_name, val_addr, user_id, channel_id, old_moniker, old_status, old_missed, old_stake, mention_type = val_data

        chain_config = self.bot.supported_chains.get(chain_name)
        if not chain_config:
            return

        status_info = await get_validator_info(
            self.bot.async_client, chain_config, val_addr,
            self._slashing_info_cache.get(chain_name, {}),
            self._slashing_params_cache.get(chain_name, {}),
            max_retries=self.bot.settings.api_max_retries,
            backoff_base=self.bot.settings.api_retry_backoff,
        )

        # --- API FAILURE HANDLING ---
        # If API fails, mark as API_ERROR but KEEP the old missed_blocks value
        # to avoid a false diff when the API recovers.
        if not status_info['success']:
            if old_status != "API_ERROR":
                await db_manager.update_validator_status(
                    chain_name, val_addr, "API_ERROR",
                    old_missed,  # Preserve old missed_blocks!
                    datetime.datetime.now().isoformat(),
                    old_moniker
                    # Do NOT update stake — preserve old value
                )
            return

        new_status = status_info['status']
        new_jailed = status_info['jailed']
        new_missed = status_info['missed_blocks']
        new_stake_raw = status_info.get('raw_stake', 0.0)

        send_notification = False
        alert_title = ""
        embed_color = discord.Color.blue()
        db_status_to_save = new_status
        extra_description = ""

        # --- RECOVERY GRACE PERIOD ---
        # When recovering from API_ERROR or initial UNKNOWN state, silently
        # update the DB to establish a fresh baseline. Only alert for JAILED.
        if old_status in ("API_ERROR", "UNKNOWN"):
            if new_jailed:
                # Critical: always alert for jailing, even during recovery
                send_notification = True
                alert_title = "🔴 Critical Alert: Validator Jailed"
                embed_color = discord.Color.red()
                db_status_to_save = "JAILED"
            else:
                # Silently establish baseline — no notification
                db_status_to_save = new_status
                logger.info(
                    f"Validator {val_addr} recovered from {old_status}. "
                    f"Baseline updated silently (missed={new_missed}, stake={new_stake_raw})."
                )

            # Send notification only if jailed
            if send_notification:
                await self._send_validator_alert(
                    alert_title, embed_color, chain_name, val_addr,
                    status_info, extra_description, user_id, channel_id
                )

            # Update DB with fresh baseline and return
            await db_manager.update_validator_status(
                chain_name, val_addr, db_status_to_save,
                new_missed, datetime.datetime.now().isoformat(),
                status_info['moniker'], new_stake=new_stake_raw
            )
            return

        # --- NORMAL MONITORING (from known-good state) ---

        # 1. Check Jailed / Unjailed (highest priority)
        if new_jailed and old_status != "JAILED":
            send_notification = True
            alert_title = "🔴 Critical Alert: Validator Jailed"
            embed_color = discord.Color.red()
            db_status_to_save = "JAILED"

        elif not new_jailed and old_status == "JAILED":
            send_notification = True
            alert_title = "🟢 Notice: Validator Recovered"
            embed_color = discord.Color.green()

        # 2. Check Missed Blocks
        elif not new_jailed:
            threshold = self.bot.settings.missed_blocks_threshold

            if new_missed >= threshold:
                if old_status != "WARNING_MISSED_BLOCKS":
                    send_notification = True
                    alert_title = "🟠 Warning: Missed Blocks Threshold Reached"
                    embed_color = discord.Color.orange()
                db_status_to_save = "WARNING_MISSED_BLOCKS"

            elif new_missed < threshold and old_status == "WARNING_MISSED_BLOCKS":
                send_notification = True
                alert_title = "🟢 Notice: Validator Recovered (Missed Blocks)"
                embed_color = discord.Color.green()

            # 3. Check Stake Changes (only when no other critical issues)
            elif old_stake > 0 and new_stake_raw > 0:
                decimals = chain_config.decimals
                token_symbol = chain_config.token_symbol
                diff_raw = new_stake_raw - old_stake
                diff_human = diff_raw / (10 ** decimals)
                min_change = self.bot.settings.min_stake_change_amount

                if abs(diff_human) >= min_change:
                    send_notification = True
                    if diff_human > 0:
                        alert_title = "💰 New Delegation Detected"
                        embed_color = discord.Color.green()
                        extra_description = (
                            f"\n**Amount:** +{diff_human:,.2f} {token_symbol}"
                            f"\n**Total Stake:** {status_info['total_stake']}"
                        )
                    else:
                        alert_title = "💸 Undelegation / Slash Detected"
                        embed_color = discord.Color.dark_orange()
                        extra_description = (
                            f"\n**Amount:** {diff_human:,.2f} {token_symbol}"
                            f"\n**Total Stake:** {status_info['total_stake']}"
                        )

        # Send notification if needed
        if send_notification:
            await self._send_validator_alert(
                alert_title, embed_color, chain_name, val_addr,
                status_info, extra_description, user_id, channel_id, mention_type
            )

        # Always update DB with latest data
        await db_manager.update_validator_status(
            chain_name, val_addr, db_status_to_save,
            new_missed, datetime.datetime.now().isoformat(),
            status_info['moniker'], new_stake=new_stake_raw
        )

    async def _send_validator_alert(
        self, title, color, chain_name, val_addr,
        status_info, extra_description, user_id, channel_id, mention_type=None
    ):
        """Send a validator alert notification to the appropriate channel."""
        channel = self.bot.get_channel(channel_id)
        if not channel:
            return

        embed = self._create_alert_embed(title, color, chain_name, val_addr, status_info)
        if extra_description:
            embed.description += extra_description

        try:
            if mention_type:
                mention_str = mention_type
            else:
                user = await self.bot.fetch_user(user_id)
                mention_str = user.mention
                
            await channel.send(content=mention_str, embed=embed)
        except Exception as e:
            logger.error(f"Failed to send notification for {val_addr}: {e}")

    def _create_alert_embed(self, title, color, chain_name, val_addr, status_info):
        """Create a standardized alert embed for validator notifications."""
        embed = discord.Embed(
            title=title,
            description=f"An alert has been triggered for validator `{status_info['moniker']}`.",
            color=color,
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        embed.add_field(name="Chain", value=chain_name.upper(), inline=True)
        embed.add_field(name="Address", value=f"`{val_addr}`", inline=False)
        embed.add_field(name="Status", value=status_info['status'], inline=True)
        embed.add_field(
            name="Jailed",
            value="Yes" if status_info['jailed'] else "No",
            inline=True
        )
        embed.add_field(name="Missed Blocks", value=status_info['missed_blocks'], inline=True)

        uptime_bar = create_progress_bar(
            status_info.get('estimated_uptime_percentage', 0.0)
        )
        embed.add_field(
            name="Estimated Uptime",
            value=f"`{uptime_bar}` {status_info.get('estimated_uptime', 'N/A')}",
            inline=False
        )
        embed.set_footer(text=f"Monitored by {self.bot.user.name}")
        return embed

    # =========================================================================
    # Governance Monitoring
    # =========================================================================

    @tasks.loop(seconds=300)  # Default; overridden via settings
    async def monitor_governance(self):
        """Governance proposal monitoring loop."""
        logger.info("Running governance monitoring loop...")
        chains_to_monitor = await db_manager.get_all_chain_notification_chains()

        for chain_name in chains_to_monitor:
            chain_config = self.bot.supported_chains.get(chain_name)
            if not chain_config:
                continue

            gov_api_url = f"{chain_config.rest_api_url}{chain_config.gov_proposals_endpoint}"
            try:
                response = await api_get_with_retry(
                    self.bot.async_client, gov_api_url,
                    max_retries=self.bot.settings.api_max_retries,
                )
                data = response.json()

                old_proposals = self._governance_proposals_cache.get(chain_name, {})
                current_proposals = {
                    p.get('id') or p.get('proposal_id'): p
                    for p in data.get('proposals', [])
                }

                # First run: populate cache without sending notifications
                if not self._governance_proposals_cache.get(chain_name):
                    self._governance_proposals_cache[chain_name] = current_proposals
                    continue

                for prop_id, prop_data in current_proposals.items():
                    old_prop = old_proposals.get(prop_id)
                    new_status = prop_data.get('status')

                    if not old_prop:
                        if new_status == "PROPOSAL_STATUS_VOTING_PERIOD":
                            await self._send_governance_notification(
                                chain_name, chain_config, prop_data, "new_voting_period"
                            )
                        elif new_status == "PROPOSAL_STATUS_DEPOSIT_PERIOD":
                            await self._send_governance_notification(
                                chain_name, chain_config, prop_data, "new_deposit_period"
                            )
                    elif new_status != old_prop.get('status'):
                        if new_status == "PROPOSAL_STATUS_VOTING_PERIOD":
                            await self._send_governance_notification(
                                chain_name, chain_config, prop_data, "new_voting_period"
                            )
                        elif new_status in (
                            "PROPOSAL_STATUS_PASSED",
                            "PROPOSAL_STATUS_REJECTED",
                            "PROPOSAL_STATUS_FAILED",
                        ):
                            await self._send_governance_notification(
                                chain_name, chain_config, prop_data, "final_result"
                            )

                self._governance_proposals_cache[chain_name] = current_proposals

            except Exception as e:
                logger.error(f"Error processing governance for {chain_name}: {e}")

    async def _send_governance_notification(self, chain_name, chain_config, prop_data, notif_type):
        """Build and send a governance notification embed."""
        prop_id = prop_data.get('id') or prop_data.get('proposal_id', 'N/A')
        prop_title = extract_proposal_title(prop_data)
        prop_desc = (
            prop_data.get('summary')
            or prop_data.get('content', {}).get('description', 'No description.')
        )

        prop_status_raw = prop_data.get('status', 'UNKNOWN')
        prop_status_clean = (
            prop_status_raw.replace('PROPOSAL_STATUS_', '').replace('_', ' ').title()
        )

        title, color, suffix = "", discord.Color.blue(), ""

        if notif_type == "new_deposit_period":
            title = f"🆕 Proposal #{prop_id} in Deposit Period"
            color = discord.Color.blue()
            end_time_str = prop_data.get('deposit_end_time')
            if end_time_str:
                try:
                    end_dt = datetime.datetime.fromisoformat(
                        end_time_str.replace('Z', '+00:00')
                    )
                    suffix = f"\n\n**Deposit Ends:** <t:{int(end_dt.timestamp())}:R>"
                except ValueError:
                    pass

        elif notif_type == "new_voting_period":
            title = f"🗳️ Proposal #{prop_id} Enters Voting"
            color = discord.Color.orange()
            end_time_str = prop_data.get('voting_end_time')
            if end_time_str:
                try:
                    end_dt = datetime.datetime.fromisoformat(
                        end_time_str.replace('Z', '+00:00')
                    )
                    suffix = f"\n\n**Voting Ends:** <t:{int(end_dt.timestamp())}:R>"
                except ValueError:
                    pass

        elif notif_type == "final_result":
            status_map = {
                "PROPOSAL_STATUS_PASSED": (
                    f"✅ Proposal #{prop_id} Passed", discord.Color.green()
                ),
                "PROPOSAL_STATUS_REJECTED": (
                    f"❌ Proposal #{prop_id} Rejected", discord.Color.red()
                ),
                "PROPOSAL_STATUS_FAILED": (
                    f"🗑️ Proposal #{prop_id} Failed", discord.Color.dark_red()
                ),
            }
            title, color = status_map.get(
                prop_status_raw,
                (f"ℹ️ Proposal #{prop_id} Concluded", discord.Color.light_grey())
            )

            # Fetch final tally results
            tally_url = chain_config.get_tally_endpoint(str(prop_id))
            tally = await fetch_tally(self.bot.async_client, tally_url)
            tally_text = format_tally_block(tally)
            suffix = f"\n\n**Final Tally:**\n{tally_text}"

        embed = discord.Embed(
            title=title,
            description=f"**{prop_title}**\n\n{prop_desc}{suffix}",
            color=color,
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        embed.add_field(name="Chain", value=chain_name.upper())
        embed.add_field(name="Status", value=prop_status_clean)
        embed.set_footer(text=f"Monitored by {self.bot.user.name}")

        configs = await db_manager.get_chain_notification_preferences(chain_name)
        for config in configs:
            if config['notify_gov_enabled']:
                channel = self.bot.get_channel(config['channel_id'])
                if channel:
                    mention_str = get_mention_string(config.get('mention_type'))
                    try:
                        await channel.send(content=mention_str, embed=embed)
                    except Exception as e:
                        logger.warning(
                            f"Failed to send gov notification to {channel.id}: {e}"
                        )

    # =========================================================================
    # Upgrade Monitoring
    # =========================================================================

    @tasks.loop(seconds=3600)  # Default; overridden via settings
    async def monitor_upgrades(self):
        """Network upgrade monitoring loop."""
        logger.info("Running upgrade monitoring loop...")
        chains_to_monitor = await db_manager.get_all_chain_notification_chains()

        for chain_name in chains_to_monitor:
            chain_config = self.bot.supported_chains.get(chain_name)
            if not chain_config:
                continue

            upgrade_url = f"{chain_config.rest_api_url}{chain_config.current_plan_endpoint}"
            try:
                response = await api_get_with_retry(
                    self.bot.async_client, upgrade_url,
                    max_retries=self.bot.settings.api_max_retries,
                )
                current_plan = response.json().get('plan') if response.status_code == 200 else None
                old_plan = self._upgrade_plan_cache.get(chain_name)

                if current_plan and (
                    not old_plan or current_plan['name'] != old_plan['name']
                ):
                    await self._send_upgrade_notification(chain_name, chain_config, current_plan)

                self._upgrade_plan_cache[chain_name] = current_plan
            except Exception as e:
                logger.error(f"Error processing upgrades for {chain_name}: {e}")

    async def _send_upgrade_notification(self, chain_name, chain_config, plan_data):
        """Build and send an upgrade notification embed."""
        plan_name = plan_data.get('name', 'N/A')
        plan_height = int(plan_data.get('height', 0))
        plan_time_str = plan_data.get('time')
        plan_info = plan_data.get('info', 'No additional details provided.')

        embed = discord.Embed(
            title=f"🚀 System Notice: Upcoming Software Upgrade '{plan_name}'",
            description=(
                f"A software upgrade is scheduled for the "
                f"**{chain_name.upper()}** network."
            ),
            color=discord.Color.purple(),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )

        if plan_height > 0:
            current_height = await get_latest_block_height(
                self.bot.async_client, chain_config.rest_api_url
            )
            blocks_remaining = (
                f"{plan_height - current_height:,}"
                if current_height and plan_height > current_height
                else "Reached"
            )
            embed.add_field(
                name="Target Height", value=f"`{plan_height:,}`", inline=True
            )
            embed.add_field(
                name="Blocks Remaining", value=f"`{blocks_remaining}`", inline=True
            )

        if plan_time_str:
            try:
                plan_dt = datetime.datetime.fromisoformat(
                    plan_time_str.replace('Z', '+00:00')
                )
                embed.add_field(
                    name="Target Time (UTC)",
                    value=f"<t:{int(plan_dt.timestamp())}:F>",
                    inline=False
                )
            except ValueError:
                embed.add_field(
                    name="Target Time", value=f"`{plan_time_str}`", inline=False
                )

        if plan_info:
            info_text = plan_info if len(plan_info) <= 1000 else plan_info[:1000] + "..."
            embed.add_field(
                name="Details", value=f"```\n{info_text}\n```", inline=False
            )

        embed.set_footer(text=f"Monitored by {self.bot.user.name}")

        configs = await db_manager.get_chain_notification_preferences(chain_name)
        for config in configs:
            if config['notify_upgrade_enabled']:
                channel = self.bot.get_channel(config['channel_id'])
                if channel:
                    mention_str = get_mention_string(config.get('mention_type'))
                    try:
                        await channel.send(content=mention_str, embed=embed)
                    except Exception as e:
                        logger.warning(
                            f"Failed to send upgrade notification to {channel.id}: {e}"
                        )

    # =========================================================================
    # Before-Loop Hooks
    # =========================================================================

    @monitor_validators.before_loop
    async def before_monitor_validators(self):
        await self.bot.wait_until_ready()
        # Apply configured interval
        self.monitor_validators.change_interval(
            seconds=self.bot.settings.monitor_interval_seconds
        )

    @monitor_governance.before_loop
    async def before_monitor_governance(self):
        await self.bot.wait_until_ready()
        self.monitor_governance.change_interval(
            seconds=self.bot.settings.governance_check_interval_seconds
        )

    @monitor_upgrades.before_loop
    async def before_monitor_upgrades(self):
        await self.bot.wait_until_ready()
        self.monitor_upgrades.change_interval(
            seconds=self.bot.settings.upgrade_check_interval_seconds
        )


async def setup(bot: commands.Bot):
    """Required function to load the Cog."""
    await bot.add_cog(MonitoringTasks(bot))
