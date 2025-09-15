# cogs/monitoring_tasks.py
# -*- coding: utf-8 -*-

import asyncio
import datetime
import logging

import discord
from discord.ext import commands, tasks

import db_manager
from utils.api_helpers import (create_progress_bar, get_latest_block_height,
                               get_validator_info)

# --- Variabel Konfigurasi untuk Cog ini ---
MISSED_BLOCKS_THRESHOLD = 50
MONITOR_INTERVAL_SECONDS = 300
GOVERNANCE_CHECK_INTERVAL_SECONDS = 300
UPGRADE_CHECK_INTERVAL_SECONDS = 3600


class MonitoringTasks(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        
        # Cache sekarang menjadi milik instance Cog ini, bukan global
        self._slashing_info_cache = {}
        self._slashing_params_cache = {}
        self._governance_proposals_cache = {}
        self._upgrade_plan_cache = {}
        
        self._chain_api_error_status = {
            chain_name: {"is_error": False, "alert_channel_id": None}
            for chain_name in self.bot.supported_chains
        }
        
        # Memulai semua task loop saat Cog di-load
        self.monitor_validators.start()
        self.monitor_governance.start()
        self.monitor_upgrades.start()

    def cog_unload(self):
        # Menghentikan semua task saat Cog di-unload
        self.monitor_validators.cancel()
        self.monitor_governance.cancel()
        self.monitor_upgrades.cancel()

    @tasks.loop(seconds=MONITOR_INTERVAL_SECONDS)
    async def monitor_validators(self):
        """Loop utama untuk memonitor status semua validator terdaftar."""
        # Memperbarui cache slashing info untuk semua chain
        for chain_name, chain_config in self.bot.supported_chains.items():
            if not chain_config.get("missed_blocks_supported", False):
                continue
            
            try:
                # Ambil slashing params
                params_url = f"{chain_config['rest_api_url']}{chain_config['slashing_params_endpoint']}"
                params_response = await self.bot.async_client.get(params_url)
                params_response.raise_for_status()
                self._slashing_params_cache[chain_name] = params_response.json().get('params', {})

                # Ambil signing infos
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

        # Memproses setiap validator yang terdaftar
        validators_to_monitor = db_manager.get_all_validators_to_monitor()
        for val_data in validators_to_monitor:
            await self.check_and_notify_validator_status(val_data)
    
    @monitor_validators.before_loop
    async def before_monitor_validators(self):
        await self.bot.wait_until_ready()

    async def check_and_notify_validator_status(self, val_data):
        """Memeriksa status satu validator dan mengirim notifikasi jika perlu."""
        chain_name, val_addr, user_id, channel_id, old_moniker, old_status, old_missed = val_data
        
        chain_config = self.bot.supported_chains.get(chain_name)
        if not chain_config:
            return

        status_info = await get_validator_info(
            self.bot.async_client,
            chain_config,
            val_addr,
            self._slashing_info_cache.get(chain_name, {}),
            self._slashing_params_cache.get(chain_name, {})
        )

        if not status_info['success']:
            # Handle jika gagal mengambil info validator
            return

        # Logika untuk menentukan apakah notifikasi perlu dikirim
        # (Logika kompleks ini bisa dipecah lagi menjadi fungsi tersendiri)
        send_notification = False
        alert_title = "Validator Status Update"
        embed_color = discord.Color.blue()
        
        new_status = status_info['status']
        new_missed = status_info['missed_blocks']

        if new_status == "JAILED" and old_status != "JAILED":
            send_notification = True
            alert_title = "🔴 Critical Alert: Validator Jailed"
            embed_color = discord.Color.red()
        elif new_status != "JAILED" and old_status == "JAILED":
            send_notification = True
            alert_title = "🟢 Notice: Validator Recovered"
            embed_color = discord.Color.green()
        elif new_missed > old_missed and new_missed >= MISSED_BLOCKS_THRESHOLD and old_missed < MISSED_BLOCKS_THRESHOLD:
            send_notification = True
            alert_title = "🟠 Warning: Missed Blocks Threshold Reached"
            embed_color = discord.Color.orange()

        if send_notification:
            channel = self.bot.get_channel(channel_id)
            if not channel:
                logging.warning(f"Channel {channel_id} not found.")
                return

            embed = discord.Embed(
                title=alert_title,
                description=f"An alert has been triggered for validator `{status_info['moniker']}`.",
                color=embed_color,
                timestamp=datetime.datetime.now(datetime.timezone.utc)
            )
            # ... (Lanjutkan membuat embed seperti di kode asli)
            
            # Placeholder untuk field embed
            embed.add_field(name="Chain", value=chain_name.upper())
            embed.add_field(name="Status", value=status_info['status'])
            embed.add_field(name="Missed Blocks", value=status_info['missed_blocks'])
            
            try:
                user = await self.bot.fetch_user(user_id)
                await channel.send(content=user.mention, embed=embed)
            except Exception as e:
                logging.error(f"Failed to send notification to channel {channel_id}: {e}")

        # Update status di DB
        db_manager.update_validator_status(
            chain_name, val_addr, status_info['status'], 
            status_info['missed_blocks'], datetime.datetime.now().isoformat(), status_info['moniker']
        )

    # --- GOVERNANCE AND UPGRADE LOOPS ---
    # (Pindahkan fungsi monitor_governance dan monitor_upgrades ke sini
    # dengan penyesuaian untuk menggunakan self.bot dan self.cache)
    
    @tasks.loop(seconds=GOVERNANCE_CHECK_INTERVAL_SECONDS)
    async def monitor_governance(self):
        # Implementasi disederhanakan, Anda bisa memindahkan logika dari bot.py ke sini
        pass # TODO: Pindahkan logika monitor_governance

    @monitor_governance.before_loop
    async def before_monitor_governance(self):
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=UPGRADE_CHECK_INTERVAL_SECONDS)
    async def monitor_upgrades(self):
        # Implementasi disederhanakan, Anda bisa memindahkan logika dari bot.py ke sini
        pass # TODO: Pindahkan logika monitor_upgrades

    @monitor_upgrades.before_loop
    async def before_monitor_upgrades(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    """Fungsi wajib untuk me-load Cog."""
    await bot.add_cog(MonitoringTasks(bot))