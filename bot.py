# bot.py
# -*- coding: utf-8 -*-
"""Main entry point for the Cosmos Validator Monitoring Discord Bot."""

# --- Standard Library Imports ---
import datetime
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

# --- Third-Party Imports ---
import discord
import httpx
from discord.ext import commands
from dotenv import load_dotenv

# --- Local Imports ---
import db_manager
from settings import load_config

# --- Initial Setup ---
load_dotenv()


def setup_logging(log_level: str = "INFO", log_file: str = None):
    """Configure logging with console output and optional rotating file handler."""
    level = getattr(logging, log_level.upper(), logging.INFO)

    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(module)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Prevent duplicate handlers on reload
    root_logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Rotating file handler
    if log_file:
        file_handler = RotatingFileHandler(
            log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8'
        )
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
        logging.info(f"File logging enabled: {log_file}")


# --- Main Bot Class ---
class CosmosMonitorBot(commands.Bot):
    """
    Extended Bot class that holds configuration, HTTP client, and
    provides admin check utilities.
    """

    def __init__(self, settings, chains, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.settings = settings
        self.supported_chains = chains  # Dict[str, ChainConfig]
        self.async_client = httpx.AsyncClient(timeout=settings.api_timeout)
        self.start_time = datetime.datetime.now(datetime.timezone.utc)
        logging.info("CosmosMonitorBot initialized.")

    async def setup_hook(self):
        """
        Called after login but before connecting to the WebSocket.
        Initializes the database and loads all cogs.
        """
        logging.info("Running setup_hook...")

        # Initialize async database
        await db_manager.init_db()

        # Restore persisted runtime settings
        await self._restore_runtime_settings()

        # Load all cogs from the cogs/ directory
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

        # Sync slash commands globally
        try:
            synced = await self.tree.sync()
            logging.info(f"Successfully synced {len(synced)} application commands globally.")
        except Exception as e:
            logging.error(f"Failed to sync application commands: {e}")

    async def _restore_runtime_settings(self):
        """Restore any settings that were changed at runtime and persisted to DB."""
        try:
            saved_settings = await db_manager.get_all_runtime_settings()
            restored = 0
            for key, value in saved_settings.items():
                if self.settings.update(key, value):
                    restored += 1
            if restored:
                logging.info(f"Restored {restored} runtime settings from database.")
        except Exception as e:
            logging.warning(f"Could not restore runtime settings: {e}")

    async def on_ready(self):
        """Called when the bot is fully ready."""
        logging.info(f'Logged in as {self.user.name} ({self.user.id})')
        logging.info('Bot is ready and online!')
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching, name="Validator Performance"
            )
        )

    async def on_close(self):
        """Clean up resources on shutdown."""
        logging.info("Closing bot... Closing HTTP client session.")
        await self.async_client.aclose()

    @property
    def uptime(self) -> datetime.timedelta:
        """Calculate bot uptime."""
        return datetime.datetime.now(datetime.timezone.utc) - self.start_time

    def is_admin(self, user_id: int) -> bool:
        """Check if a user is a bot administrator."""
        return user_id in self.settings.admin_user_ids


# --- Main Execution Block ---
if __name__ == '__main__':
    DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
    if not DISCORD_BOT_TOKEN:
        print("FATAL: DISCORD_BOT_TOKEN environment variable not set.")
        print("Please create a .env file or export it.")
        exit(1)

    # Load configuration
    settings, chains = load_config()

    # Setup logging (must be after config load to use configured level)
    setup_logging(settings.log_level, settings.log_file)

    # Set database path
    db_manager.set_db_path(settings.db_path)

    # Setup intents
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True

    # Create and run bot
    bot = CosmosMonitorBot(settings, chains, command_prefix='!', intents=intents)

    try:
        bot.run(DISCORD_BOT_TOKEN)
    except discord.errors.LoginFailure:
        logging.critical("Login Failed. Please ensure your Discord Bot Token is correct.")
    except Exception as e:
        logging.critical(f"An unexpected error occurred while running the bot: {e}")