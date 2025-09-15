# bot.py
# -*- coding: utf-8 -*-

# --- Standard Library Imports ---
import asyncio
import logging
import os

# --- Third-Party Imports ---
import discord
import httpx
import yaml
from discord.ext import commands
from dotenv import load_dotenv

# --- Local Imports ---
import db_manager

# --- Initial Setup ---
load_dotenv()

# Konfigurasi logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(module)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# --- Fungsi untuk memuat konfigurasi ---
def load_config(config_file='config.yaml'):
    """Memuat konfigurasi chains dari file YAML."""
    try:
        with open(config_file, 'r') as f:
            config = yaml.safe_load(f)
            logging.info(f"Configuration loaded successfully from {config_file}.")
            return config
    except FileNotFoundError:
        logging.critical(f"FATAL: Configuration file '{config_file}' not found. The bot cannot start.")
        exit(1)
    except yaml.YAMLError as e:
        logging.critical(f"FATAL: Error parsing '{config_file}': {e}. The bot cannot start.")
        exit(1)

# --- Class Bot Utama ---
class CosmosMonitorBot(commands.Bot):
    """
    Class turunan dari commands.Bot untuk merangkum fungsionalitas bot,
    termasuk konfigurasi dan klien HTTP.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.supported_chains = load_config()
        self.async_client = httpx.AsyncClient(timeout=20.0)
        logging.info("CosmosMonitorBot initialized.")

    async def setup_hook(self):
        """
        Hook ini dijalankan setelah login bot dan sebelum terhubung ke WebSocket.
        Digunakan untuk memuat ekstensi (cogs).
        """
        logging.info("Running setup_hook...")
        
        # Memuat semua file .py dari direktori 'cogs'
        cogs_loaded = 0
        for filename in os.listdir('./cogs'):
            if filename.endswith('.py') and not filename.startswith('__'):
                try:
                    await self.load_extension(f'cogs.{filename[:-3]}')
                    logging.info(f"Successfully loaded cog: {filename}")
                    cogs_loaded += 1
                except Exception as e:
                    logging.error(f"Failed to load cog {filename}: {type(e).__name__} - {e}")
        
        logging.info(f"Completed loading {cogs_loaded} cogs.")

        # Sinkronisasi slash commands secara global setelah semua cogs dimuat
        try:
            synced = await self.tree.sync()
            logging.info(f"Successfully synced {len(synced)} application commands globally.")
        except Exception as e:
            logging.error(f"Failed to sync application commands: {e}")

    async def on_ready(self):
        """Event yang dijalankan saat bot siap beroperasi."""
        logging.info(f'Logged in as {self.user.name} ({self.user.id})')
        logging.info('Bot is ready and online!')
        await self.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="Validator Performance"))

    async def on_close(self):
        """Event untuk membersihkan resource saat bot ditutup."""
        logging.info("Closing bot... Closing HTTP client session.")
        await self.async_client.aclose()


# --- Main Execution Block ---
if __name__ == '__main__':
    DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
    if not DISCORD_BOT_TOKEN:
        logging.critical("DISCORD_BOT_TOKEN environment variable not set. Please create a .env file or export it.")
        exit(1)

    # Inisialisasi database
    try:
        db_manager.init_db()
        logging.info("Database initialized successfully.")
    except Exception as e:
        logging.critical(f"Failed to initialize database: {e}")
        exit(1)
        
    # Menyiapkan intents
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True
    
    # Membuat instance dan menjalankan bot
    bot = CosmosMonitorBot(command_prefix='!', intents=intents)
    
    try:
        bot.run(DISCORD_BOT_TOKEN)
    except discord.errors.LoginFailure:
        logging.critical("Login Failed. Please ensure your Discord Bot Token is correct.")
    except Exception as e:
        logging.critical(f"An unexpected error occurred while running the bot: {e}")