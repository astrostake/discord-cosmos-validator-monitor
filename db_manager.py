# db_manager.py
# -*- coding: utf-8 -*-
"""Async database manager using aiosqlite.

All database operations are non-blocking, preventing the bot's async event loop
from being blocked by I/O operations.
"""

import datetime
import logging
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite

logger = logging.getLogger(__name__)

# Module-level database path, set during initialization
_db_path: str = 'validator_monitor.db'


def set_db_path(path: str):
    """Set the database file path. Must be called before init_db()."""
    global _db_path
    _db_path = path


async def init_db():
    """Initialize the database and create/migrate tables."""
    async with aiosqlite.connect(_db_path) as db:
        await db.execute("PRAGMA journal_mode=WAL")

        await db.execute('''
            CREATE TABLE IF NOT EXISTS validators (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                chain_name TEXT NOT NULL,
                validator_address TEXT NOT NULL UNIQUE,
                moniker TEXT,
                status TEXT DEFAULT 'UNKNOWN',
                missed_blocks INTEGER DEFAULT -1,
                last_check_time TEXT,
                notifications_enabled BOOLEAN DEFAULT 1,
                last_total_stake REAL DEFAULT 0,
                mention_type TEXT
            );
        ''')

        await db.execute('''
            CREATE TABLE IF NOT EXISTS chain_notification_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER NOT NULL,
                chain_name TEXT NOT NULL,
                notify_gov_enabled BOOLEAN DEFAULT 0,
                notify_upgrade_enabled BOOLEAN DEFAULT 0,
                mention_type TEXT,
                UNIQUE(channel_id, chain_name)
            );
        ''')

        await db.execute('''
            CREATE TABLE IF NOT EXISTS bot_runtime_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        ''')

        # --- Migrations ---
        await _run_migrations(db)
        await db.commit()
    logger.info("Database initialized successfully.")


async def _run_migrations(db: aiosqlite.Connection):
    """Run pending database migrations safely."""
    async with db.execute("PRAGMA table_info(validators)") as cursor:
        columns = {row[1] async for row in cursor}

    if 'last_total_stake' not in columns:
        logger.info("Migrating DB: Adding last_total_stake column...")
        await db.execute("ALTER TABLE validators ADD COLUMN last_total_stake REAL DEFAULT 0")

    if 'mention_type' not in columns:
        logger.info("Migrating DB: Adding mention_type column...")
        await db.execute("ALTER TABLE validators ADD COLUMN mention_type TEXT")


# =============================================================================
# Validator CRUD Operations
# =============================================================================

async def add_validator(
    user_id: int, channel_id: int, chain_name: str,
    validator_address: str, moniker: str = None,
    mention_type: str = None
) -> bool:
    """Add a new validator to monitoring. Returns True if added, False if duplicate."""
    try:
        async with aiosqlite.connect(_db_path) as db:
            await db.execute(
                """INSERT INTO validators
                   (user_id, channel_id, chain_name, validator_address, moniker,
                    status, missed_blocks, last_check_time, mention_type)
                   VALUES (?, ?, ?, ?, ?, 'UNKNOWN', -1, ?, ?)""",
                (user_id, channel_id, chain_name, validator_address, moniker,
                 datetime.datetime.now().isoformat(), mention_type)
            )
            await db.commit()
        return True
    except aiosqlite.IntegrityError:
        return False


async def remove_validator(user_id: int, chain_name: str, validator_address: str) -> bool:
    """Remove a validator from monitoring. Returns True if removed."""
    async with aiosqlite.connect(_db_path) as db:
        cursor = await db.execute(
            "DELETE FROM validators WHERE user_id = ? AND chain_name = ? AND validator_address = ?",
            (user_id, chain_name, validator_address)
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_user_validators(user_id: int) -> List[Tuple]:
    """Get all validators registered by a specific user."""
    async with aiosqlite.connect(_db_path) as db:
        async with db.execute(
            "SELECT chain_name, validator_address, moniker, status, missed_blocks "
            "FROM validators WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            return await cursor.fetchall()


async def get_user_validators_by_chain(user_id: int, chain_name: str) -> List[Tuple]:
    """Get validators for a user on a specific chain."""
    async with aiosqlite.connect(_db_path) as db:
        async with db.execute(
            "SELECT chain_name, validator_address, moniker, status, missed_blocks "
            "FROM validators WHERE user_id = ? AND chain_name = ?",
            (user_id, chain_name)
        ) as cursor:
            return await cursor.fetchall()


async def get_user_validator_details(
    user_id: int, chain_name: str, validator_address: str
) -> Optional[Tuple]:
    """Get full details for a specific validator."""
    async with aiosqlite.connect(_db_path) as db:
        async with db.execute(
            "SELECT * FROM validators WHERE user_id = ? AND chain_name = ? AND validator_address = ?",
            (user_id, chain_name, validator_address)
        ) as cursor:
            return await cursor.fetchone()


async def get_all_validators_to_monitor() -> List[Tuple]:
    """Get all validators with notifications enabled for monitoring."""
    async with aiosqlite.connect(_db_path) as db:
        async with db.execute(
            """SELECT chain_name, validator_address, user_id, channel_id,
                      moniker, status, missed_blocks, last_total_stake, mention_type
               FROM validators WHERE notifications_enabled = 1"""
        ) as cursor:
            return await cursor.fetchall()


async def update_validator_status(
    chain_name: str, validator_address: str,
    new_status: str, new_missed_blocks: int,
    last_check_time: str, moniker: str = None,
    new_stake: float = None
) -> None:
    """Update validator status using a single dynamic query.

    Replaces the old fragile 4-branch approach with a clean dynamic builder.
    """
    fields = ["status = ?", "missed_blocks = ?", "last_check_time = ?"]
    values: list[Any] = [new_status, new_missed_blocks, last_check_time]

    if moniker is not None:
        fields.append("moniker = ?")
        values.append(moniker)

    if new_stake is not None:
        fields.append("last_total_stake = ?")
        values.append(new_stake)

    values.extend([chain_name, validator_address])
    query = f"UPDATE validators SET {', '.join(fields)} WHERE chain_name = ? AND validator_address = ?"

    async with aiosqlite.connect(_db_path) as db:
        await db.execute(query, values)
        await db.commit()


async def set_validator_notifications(
    user_id: int, chain_name: str, validator_address: str, enabled: bool
) -> bool:
    """Toggle notification status for a specific validator."""
    async with aiosqlite.connect(_db_path) as db:
        cursor = await db.execute(
            "UPDATE validators SET notifications_enabled = ? "
            "WHERE user_id = ? AND chain_name = ? AND validator_address = ?",
            (1 if enabled else 0, user_id, chain_name, validator_address)
        )
        await db.commit()
        return cursor.rowcount > 0


# =============================================================================
# Chain Notification Settings
# =============================================================================

async def set_chain_notification_preference(
    channel_id: int, chain_name: str,
    notify_gov: bool, notify_upgrade: bool, mention_type: str
) -> bool:
    """Set or update notification preferences for a channel+chain combination."""
    try:
        async with aiosqlite.connect(_db_path) as db:
            await db.execute(
                """INSERT INTO chain_notification_settings
                   (channel_id, chain_name, notify_gov_enabled, notify_upgrade_enabled, mention_type)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(channel_id, chain_name) DO UPDATE SET
                       notify_gov_enabled = excluded.notify_gov_enabled,
                       notify_upgrade_enabled = excluded.notify_upgrade_enabled,
                       mention_type = excluded.mention_type""",
                (channel_id, chain_name, 1 if notify_gov else 0,
                 1 if notify_upgrade else 0, mention_type)
            )
            await db.commit()
        return True
    except Exception as e:
        logger.error(f"Error setting chain notification preference: {e}")
        return False


async def get_chain_notification_preferences(chain_name: str) -> List[Dict]:
    """Get all channels configured to receive notifications for a chain."""
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT channel_id, notify_gov_enabled, notify_upgrade_enabled, mention_type
               FROM chain_notification_settings
               WHERE chain_name = ? AND (notify_gov_enabled = 1 OR notify_upgrade_enabled = 1)""",
            (chain_name,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def get_all_chain_notification_chains() -> List[str]:
    """Get all unique chain names with active notification settings."""
    async with aiosqlite.connect(_db_path) as db:
        async with db.execute(
            """SELECT DISTINCT chain_name FROM chain_notification_settings
               WHERE notify_gov_enabled = 1 OR notify_upgrade_enabled = 1"""
        ) as cursor:
            return [row[0] async for row in cursor]


async def get_channels_with_validator_count(chain_name: str) -> List[Dict]:
    """Get channels with validator counts for a specific chain."""
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT channel_id, COUNT(id) as validator_count
               FROM validators WHERE chain_name = ?
               GROUP BY channel_id ORDER BY validator_count DESC""",
            (chain_name,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


# =============================================================================
# Runtime Settings (for admin commands)
# =============================================================================

async def get_runtime_setting(key: str) -> Optional[str]:
    """Get a persisted runtime setting value."""
    async with aiosqlite.connect(_db_path) as db:
        async with db.execute(
            "SELECT value FROM bot_runtime_settings WHERE key = ?", (key,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def set_runtime_setting(key: str, value: str) -> None:
    """Persist a runtime setting value."""
    async with aiosqlite.connect(_db_path) as db:
        await db.execute(
            """INSERT INTO bot_runtime_settings (key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (key, value)
        )
        await db.commit()


async def get_all_runtime_settings() -> Dict[str, str]:
    """Get all persisted runtime settings."""
    async with aiosqlite.connect(_db_path) as db:
        async with db.execute("SELECT key, value FROM bot_runtime_settings") as cursor:
            return {row[0]: row[1] async for row in cursor}


# =============================================================================
# Statistics
# =============================================================================

async def get_monitoring_stats() -> Dict[str, int]:
    """Get aggregated monitoring statistics for the bot status dashboard."""
    async with aiosqlite.connect(_db_path) as db:
        stats = {}

        async with db.execute("SELECT COUNT(*) FROM validators") as c:
            stats['total_validators'] = (await c.fetchone())[0]

        async with db.execute(
            "SELECT COUNT(*) FROM validators WHERE notifications_enabled = 1"
        ) as c:
            stats['active_validators'] = (await c.fetchone())[0]

        async with db.execute(
            "SELECT COUNT(DISTINCT chain_name) FROM validators"
        ) as c:
            stats['unique_chains'] = (await c.fetchone())[0]

        async with db.execute(
            "SELECT COUNT(DISTINCT user_id) FROM validators"
        ) as c:
            stats['unique_users'] = (await c.fetchone())[0]

        async with db.execute(
            "SELECT COUNT(*) FROM validators WHERE status = 'JAILED'"
        ) as c:
            stats['jailed_validators'] = (await c.fetchone())[0]

        async with db.execute(
            "SELECT COUNT(*) FROM validators WHERE status = 'API_ERROR'"
        ) as c:
            stats['api_error_validators'] = (await c.fetchone())[0]

        return stats
