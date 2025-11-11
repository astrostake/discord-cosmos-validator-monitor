# cogs/monitoring_tasks.py
# -*- coding: utf-8 -*-

import asyncio
import datetime
import logging
import base64
import json

import discord
import httpx
from discord.ext import commands, tasks

import db_manager
from utils.api_helpers import (create_progress_bar, get_latest_block_height,
                               get_validator_info)

# --- Variabel Konfigurasi untuk Cog ini ---
MONITOR_INTERVAL_SECONDS = 60
GOVERNANCE_CHECK_INTERVAL_SECONDS = 300
UPGRADE_CHECK_INTERVAL_SECONDS = 3600
MISSED_BLOCKS_THRESHOLD = 10

class MonitoringTasks(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        
        # Inisialisasi semua cache di dalam instance Cog
        self._slashing_info_cache = {}
        self._slashing_params_cache = {}
        self._governance_proposals_cache = {}
        self._upgrade_plan_cache = {}
        self._chain_api_error_status = {
            chain_name: {"is_error": False, "alert_channel_id": None}
            for chain_name in self.bot.supported_chains
        }
        
        # Mulai semua task loop
        self.monitor_validators.start()
        self.monitor_governance.start()
        self.monitor_upgrades.start()

    def cog_unload(self):
        # Hentikan semua task jika Cog di-unload
        self.monitor_validators.cancel()
        self.monitor_governance.cancel()
        self.monitor_upgrades.cancel()

    # --- Validator Monitoring ---
    @tasks.loop(seconds=MONITOR_INTERVAL_SECONDS)
    async def monitor_validators(self):
        logging.info("Running validator monitoring loop...")
        # 1. Perbarui cache data slashing untuk semua chain
        for chain_name, chain_config in self.bot.supported_chains.items():
            if not chain_config.get("missed_blocks_supported", False):
                continue
            
            try:
                params_url = f"{chain_config['rest_api_url']}{chain_config['slashing_params_endpoint']}"
                params_response = await self.bot.async_client.get(params_url)
                params_response.raise_for_status()
                self._slashing_params_cache[chain_name] = params_response.json().get('params', {})

                slashing_url = f"{chain_config['rest_api_url']}{chain_config['signing_infos_endpoint']}"
                slashing_response = await self.bot.async_client.get(slashing_url)
                slashing_response.raise_for_status()
                self._slashing_info_cache[chain_name] = {
                    item['address']: item for item in slashing_response.json().get('info', [])
                }
            except Exception as e:
                logging.error(f"Failed to update slashing cache for {chain_name}: {e}")
                self._slashing_params_cache[chain_name] = {}
                self._slashing_info_cache[chain_name] = {}
        
        # 2. Ambil semua validator dari DB dan periksa satu per satu
        validators_to_monitor = db_manager.get_all_validators_to_monitor()
        for val_data in validators_to_monitor:
            await self.check_and_notify_validator_status(val_data)

    async def check_and_notify_validator_status(self, val_data):
        chain_name, val_addr, user_id, channel_id, old_moniker, old_status, old_missed = val_data
        
        chain_config = self.bot.supported_chains.get(chain_name)
        if not chain_config: return

        status_info = await get_validator_info(
            self.bot.async_client, chain_config, val_addr,
            self._slashing_info_cache.get(chain_name, {}),
            self._slashing_params_cache.get(chain_name, {})
        )

        if not status_info['success']:
            # Handle API error
            if old_status != "API_ERROR":
                db_manager.update_validator_status(chain_name, val_addr, "API_ERROR", old_missed, datetime.datetime.now().isoformat(), old_moniker)
            return

        send_notification = False
        alert_title = "Validator Status Update"
        embed_color = discord.Color.blue()
        
        new_status = status_info['status']
        new_jailed = status_info['jailed']
        new_missed = status_info['missed_blocks']

        if new_jailed and not old_status == "JAILED":
            send_notification = True
            alert_title = "🔴 Critical Alert: Validator Jailed"
            embed_color = discord.Color.red()
        elif not new_jailed and old_status == "JAILED":
            send_notification = True
            alert_title = "🟢 Notice: Validator Recovered"
            embed_color = discord.Color.green()
        elif new_missed > old_missed and new_missed >= MISSED_BLOCKS_THRESHOLD and old_missed < MISSED_BLOCKS_THRESHOLD:
            send_notification = True
            alert_title = "🟠 Warning: Missed Blocks Threshold Reached"
            embed_color = discord.Color.orange()

        if send_notification:
            channel = self.bot.get_channel(channel_id)
            if not channel: return

            embed = await self.create_alert_embed(alert_title, embed_color, chain_name, val_addr, status_info)
            try:
                user = await self.bot.fetch_user(user_id)
                await channel.send(content=user.mention, embed=embed)
            except Exception as e:
                logging.error(f"Failed to send notification to channel {channel_id}: {e}")

        db_manager.update_validator_status(chain_name, val_addr, new_status, new_missed, datetime.datetime.now().isoformat(), status_info['moniker'])

    async def create_alert_embed(self, title, color, chain_name, val_addr, status_info):
        embed = discord.Embed(
            title=title,
            description=f"An alert has been triggered for validator `{status_info['moniker']}`.",
            color=color,
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        embed.add_field(name="Chain", value=chain_name.upper(), inline=True)
        embed.add_field(name="Address", value=f"`{val_addr}`", inline=False)
        embed.add_field(name="Status", value=status_info['status'], inline=True)
        embed.add_field(name="Jailed", value="Yes" if status_info['jailed'] else "No", inline=True)
        embed.add_field(name="Missed Blocks", value=status_info['missed_blocks'], inline=True)
        uptime_bar = create_progress_bar(status_info.get('estimated_uptime_percentage', 0.0))
        embed.add_field(name="Estimated Uptime", value=f"`{uptime_bar}` {status_info.get('estimated_uptime', 'N/A')}", inline=False)
        embed.set_footer(text=f"Monitored by {self.bot.user.name}")
        return embed

    # --- Governance Monitoring ---
    @tasks.loop(seconds=GOVERNANCE_CHECK_INTERVAL_SECONDS)
    async def monitor_governance(self):
        logging.info("Running governance monitoring loop...")
        chains_to_monitor = db_manager.get_all_chain_notification_chains()

        for chain_name in chains_to_monitor:
            chain_config = self.bot.supported_chains.get(chain_name)
            if not chain_config or "gov_proposals_endpoint" not in chain_config:
                continue

            gov_api_url = f"{chain_config['rest_api_url']}{chain_config['gov_proposals_endpoint']}"
            try:
                response = await self.bot.async_client.get(gov_api_url)
                response.raise_for_status()
                data = response.json()
                
                current_proposals = {p.get('id') or p.get('proposal_id'): p for p in data.get('proposals', [])}

                if chain_name not in self._governance_proposals_cache:
                    self._governance_proposals_cache[chain_name] = current_proposals
                    continue

                old_proposals = self._governance_proposals_cache[chain_name]
                for prop_id, prop_data in current_proposals.items():
                    old_prop = old_proposals.get(prop_id)
                    new_status = prop_data.get('status')
                    
                    if not old_prop and new_status == "PROPOSAL_STATUS_VOTING_PERIOD":
                        await self.send_governance_notification(chain_name, prop_data, "new_voting_period")
                    elif old_prop and new_status != old_prop.get('status'):
                        if new_status == "PROPOSAL_STATUS_VOTING_PERIOD":
                             await self.send_governance_notification(chain_name, prop_data, "new_voting_period")
                        elif new_status in ["PROPOSAL_STATUS_PASSED", "PROPOSAL_STATUS_REJECTED", "PROPOSAL_STATUS_FAILED"]:
                            await self.send_governance_notification(chain_name, prop_data, "final_result")

                self._governance_proposals_cache[chain_name] = current_proposals
            except Exception as e:
                logging.error(f"Error processing governance for {chain_name}: {e}")

    async def send_governance_notification(self, chain_name, prop_data, notif_type):
        prop_id = prop_data.get('id') or prop_data.get('proposal_id', 'N/A')
        
        prop_title = prop_data.get('title')
        if not prop_title:
            prop_title = prop_data.get('content', {}).get('title')
        if not prop_title and 'metadata' in prop_data:
            try:
                metadata_json = json.loads(base64.b64decode(prop_data['metadata']))
                prop_title = metadata_json.get('title')
            except Exception: pass
        if not prop_title:
            prop_title = f"Proposal #{prop_id}"

        prop_desc = prop_data.get('summary') or prop_data.get('content', {}).get('description', 'No description.')

        prop_status_raw = prop_data.get('status', 'UNKNOWN')
        prop_status_clean = prop_status_raw.replace('PROPOSAL_STATUS_', '').replace('_', ' ').title()

        title, color, suffix = "", discord.Color.blue(), ""

        if notif_type == "new_voting_period":
            title = f"🗳️ Proposal #{prop_id} Enters Voting"
            color = discord.Color.orange()
            end_time_str = prop_data.get('voting_end_time')
            if end_time_str:
                try:
                    end_dt = datetime.datetime.fromisoformat(end_time_str.replace('Z', '+00:00'))
                    suffix = f"\n\n**Voting Ends:** <t:{int(end_dt.timestamp())}:R>"
                except ValueError: pass
        
        elif notif_type == "final_result":
            status_map = {
                "PROPOSAL_STATUS_PASSED": (f"✅ Proposal #{prop_id} Passed", discord.Color.green()),
                "PROPOSAL_STATUS_REJECTED": (f"❌ Proposal #{prop_id} Rejected", discord.Color.red()),
                "PROPOSAL_STATUS_FAILED": (f"🗑️ Proposal #{prop_id} Failed", discord.Color.dark_red()),
            }
            title, color = status_map.get(prop_status_raw, (f"ℹ️ Proposal #{prop_id} Concluded", discord.Color.light_grey()))

            # --- LOGIKA PENGAMBILAN TALLY DIMULAI DI SINI ---
            tally_text = "Could not fetch tally results."
            chain_config = self.bot.supported_chains.get(chain_name)
            if chain_config:
                # Tentukan endpoint berdasarkan versi gov
                tally_endpoint = "/cosmos/gov/v1/proposals" if "/gov/v1/" in chain_config["gov_proposals_endpoint"] else "/cosmos/gov/v1beta1/proposals"
                tally_url = f"{chain_config['rest_api_url']}{tally_endpoint}/{prop_id}/tally"
                try:
                    tally_response = await self.bot.async_client.get(tally_url)
                    tally_response.raise_for_status()
                    tally_data = tally_response.json().get('tally', {})
                    
                    yes = int(tally_data.get('yes_count', '0'))
                    no = int(tally_data.get('no_count', '0'))
                    veto = int(tally_data.get('no_with_veto_count', '0'))
                    abstain = int(tally_data.get('abstain_count', '0'))
                    total = yes + no + veto + abstain

                    if total > 0:
                        tally_text = (
                            f"```\n"
                            f"Yes:         {yes/total:8.2%} ({yes:,})\n"
                            f"No:          {no/total:8.2%} ({no:,})\n"
                            f"No w/ Veto:  {veto/total:8.2%} ({veto:,})\n"
                            f"Abstain:     {abstain/total:8.2%} ({abstain:,})\n"
                            f"```"
                        )
                    else:
                        tally_text = "No votes were recorded."
                except Exception as e:
                    logging.error(f"Failed to fetch tally for prop {prop_id} on {chain_name}: {e}")
            
            suffix = f"\n\n**Final Tally:**\n{tally_text}"
            # --- LOGIKA TALLY SELESAI ---

        embed = discord.Embed(title=title, description=f"**{prop_title}**\n\n{prop_desc}{suffix}", color=color, timestamp=datetime.datetime.now(datetime.timezone.utc))
        embed.add_field(name="Chain", value=chain_name.upper())
        embed.add_field(name="Status", value=prop_status_clean)
        embed.set_footer(text=f"Monitored by {self.bot.user.name}")

        configs = db_manager.get_chain_notification_preferences(chain_name)
        for config in configs:
            if config['notify_gov_enabled']:
                channel = self.bot.get_channel(config['channel_id'])
                if channel: await channel.send(embed=embed)

    # --- Upgrade Monitoring ---
    @tasks.loop(seconds=UPGRADE_CHECK_INTERVAL_SECONDS)
    async def monitor_upgrades(self):
        logging.info("Running upgrade monitoring loop...")
        chains_to_monitor = db_manager.get_all_chain_notification_chains()

        for chain_name in chains_to_monitor:
            chain_config = self.bot.supported_chains.get(chain_name)
            if not chain_config or "current_plan_endpoint" not in chain_config:
                continue

            upgrade_url = f"{chain_config['rest_api_url']}{chain_config['current_plan_endpoint']}"
            try:
                response = await self.bot.async_client.get(upgrade_url)
                current_plan = response.json().get('plan') if response.status_code == 200 else None
                old_plan = self._upgrade_plan_cache.get(chain_name)

                if current_plan and (not old_plan or current_plan['name'] != old_plan['name']):
                    await self.send_upgrade_notification(chain_name, current_plan)
                
                self._upgrade_plan_cache[chain_name] = current_plan
            except Exception as e:
                logging.error(f"Error processing upgrades for {chain_name}: {e}")

    async def send_upgrade_notification(self, chain_name, plan_data):
        plan_name = plan_data.get('name', 'N/A')
        plan_height = int(plan_data.get('height', 0))
        
        current_height = await get_latest_block_height(self.bot.async_client, self.bot.supported_chains[chain_name]['rest_api_url'])
        blocks_remaining = f"{plan_height - current_height:,}" if current_height and plan_height > current_height else "Reached"

        embed = discord.Embed(
            title=f"🚀 System Notice: Upcoming Software Upgrade '{plan_name}'",
            description=f"A software upgrade is scheduled for the **{chain_name.upper()}** network.",
            color=discord.Color.purple(),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        embed.add_field(name="Target Height", value=f"`{plan_height:,}`")
        embed.add_field(name="Blocks Remaining", value=f"`{blocks_remaining}`")
        embed.set_footer(text=f"Monitored by {self.bot.user.name}")
        
        configs = db_manager.get_chain_notification_preferences(chain_name)
        for config in configs:
            if config['notify_upgrade_enabled']:
                channel = self.bot.get_channel(config['channel_id'])
                if channel: await channel.send(embed=embed)

    @monitor_validators.before_loop
    @monitor_governance.before_loop
    @monitor_upgrades.before_loop
    async def before_tasks(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    """Fungsi wajib untuk me-load Cog."""
    await bot.add_cog(MonitoringTasks(bot))
