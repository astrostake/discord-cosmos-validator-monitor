import sqlite3
import datetime
from typing import List, Tuple, Optional

DATABASE_NAME = 'validator_monitor.db'

def init_db():
    """
    Initializes the SQLite database and creates the 'validators' table if it doesn't already exist.
    The table stores information about monitored validators, including user and channel IDs
    for notifications, validator details, and monitoring status.
    """
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS validators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,            -- Discord user ID who registered this validator
            channel_id INTEGER NOT NULL,         -- Discord channel ID where notifications should be sent
            chain_name TEXT NOT NULL,            -- Name of the blockchain (e.g., 'empe', 'lumera')
            validator_address TEXT NOT NULL,     -- The validator's 'valoper' address
            moniker TEXT,                        -- Last known moniker of the validator
            status TEXT,                         -- Last known operational status (e.g., BONDED, JAILED, UNBONDED, API_ERROR)
            missed_blocks INTEGER,               -- Last known missed blocks count (-1 if not applicable/inaccessible)
            last_check_time TEXT,                -- ISO formatted string of the last time the validator was checked
            notifications_enabled BOOLEAN DEFAULT 1, -- Flag to enable/disable notifications (1 for True, 0 for False)
            UNIQUE(user_id, chain_name, validator_address) -- Ensure a user can't register the same validator multiple times
        );
    ''')
    conn.commit()
    conn.close()

def add_validator(user_id: int, channel_id: int, chain_name: str, validator_address: str, moniker: Optional[str] = None) -> bool:
    """
    Adds a new validator to the monitoring database.

    Args:
        user_id (int): The Discord user ID who is registering the validator.
        channel_id (int): The Discord channel ID where notifications should be sent.
        chain_name (str): The name of the blockchain.
        validator_address (str): The validator's operator address.
        moniker (Optional[str]): The validator's known moniker. Defaults to None.

    Returns:
        bool: True if the validator was successfully added, False if it already exists.
    """
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    try:
        initial_status = "UNKNOWN"
        initial_missed_blocks = -1
        current_time = datetime.datetime.now().isoformat()

        cursor.execute(
            "INSERT INTO validators (user_id, channel_id, chain_name, validator_address, moniker, status, missed_blocks, last_check_time) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, channel_id, chain_name, validator_address, moniker, initial_status, initial_missed_blocks, current_time)
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        # This error occurs if a unique constraint is violated (e.g., same validator registered twice by the same user)
        return False
    finally:
        conn.close()

def remove_validator(user_id: int, chain_name: str, validator_address: str) -> bool:
    """
    Removes a validator from the monitoring database for a specific user.

    Args:
        user_id (int): The Discord user ID who registered the validator.
        chain_name (str): The name of the blockchain.
        validator_address (str): The validator's operator address.

    Returns:
        bool: True if the validator was successfully removed, False if not found.
    """
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM validators WHERE user_id = ? AND chain_name = ? AND validator_address = ?",
        (user_id, chain_name, validator_address)
    )
    conn.commit()
    rows_affected = cursor.rowcount
    conn.close()
    return rows_affected > 0

def get_user_validators(user_id: int) -> List[Tuple]:
    """
    Retrieves all validators registered by a specific Discord user.

    Args:
        user_id (int): The Discord user ID.

    Returns:
        List[Tuple]: A list of tuples, each containing (chain_name, validator_address, moniker, status, missed_blocks).
    """
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT chain_name, validator_address, moniker, status, missed_blocks FROM validators WHERE user_id = ?",
        (user_id,)
    )
    validators = cursor.fetchall()
    conn.close()
    return validators

def get_user_validators_by_chain(user_id: int, chain_name: str) -> List[Tuple]:
    """
    Retrieves validators registered by a specific user for a given blockchain chain.

    Args:
        user_id (int): The Discord user ID.
        chain_name (str): The name of the blockchain.

    Returns:
        List[Tuple]: A list of tuples, each containing (chain_name, validator_address, moniker, status, missed_blocks).
    """
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT chain_name, validator_address, moniker, status, missed_blocks FROM validators WHERE user_id = ? AND chain_name = ?",
        (user_id, chain_name)
    )
    validators = cursor.fetchall()
    conn.close()
    return validators

def get_user_validator_details(user_id: int, chain_name: str, validator_address: str) -> Optional[Tuple]:
    """
    Retrieves full details for a specific validator registered by a user.

    Args:
        user_id (int): The Discord user ID.
        chain_name (str): The name of the blockchain.
        validator_address (str): The validator's operator address.

    Returns:
        Optional[Tuple]: A tuple containing all details if found, otherwise None.
                        Tuple format: (chain_name, validator_address, user_id, channel_id, moniker, status, missed_blocks, notifications_enabled)
    """
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT chain_name, validator_address, user_id, channel_id, moniker, status, missed_blocks, notifications_enabled FROM validators WHERE user_id = ? AND chain_name = ? AND validator_address = ?",
        (user_id, chain_name, validator_address)
    )
    validator = cursor.fetchone()
    conn.close()
    return validator

def get_all_validators_to_monitor() -> List[Tuple]:
    """
    Retrieves all registered validators that have notifications enabled.
    This is used by the background monitoring task.

    Returns:
        List[Tuple]: A list of tuples, each containing
                     (chain_name, validator_address, user_id, channel_id, moniker, status, missed_blocks).
    """
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT chain_name, validator_address, user_id, channel_id, moniker, status, missed_blocks FROM validators WHERE notifications_enabled = 1"
    )
    validators = cursor.fetchall()
    conn.close()
    return validators

def update_validator_status(chain_name: str, validator_address: str, new_status: str, new_missed_blocks: int, last_check_time: str, moniker: Optional[str] = None):
    """
    Updates the operational status, missed blocks count, last check time, and optionally moniker
    for a specific validator in the database.

    Args:
        chain_name (str): The name of the blockchain.
        validator_address (str): The validator's operator address.
        new_status (str): The updated status of the validator.
        new_missed_blocks (int): The updated missed blocks count.
        last_check_time (str): ISO formatted string of the last check time.
        moniker (Optional[str]): The updated moniker. If None, moniker is not updated.
    """
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    if moniker:
        cursor.execute(
            "UPDATE validators SET status = ?, missed_blocks = ?, last_check_time = ?, moniker = ? WHERE chain_name = ? AND validator_address = ?",
            (new_status, new_missed_blocks, last_check_time, moniker, chain_name, validator_address)
        )
    else:
        cursor.execute(
            "UPDATE validators SET status = ?, missed_blocks = ?, last_check_time = ? WHERE chain_name = ? AND validator_address = ?",
            (new_status, new_missed_blocks, last_check_time, chain_name, validator_address)
        )
    conn.commit()
    conn.close()

def set_validator_notifications(user_id: int, chain_name: str, validator_address: str, enabled: bool) -> bool:
    """
    Enables or disables notifications for a specific validator registered by a user.

    Args:
        user_id (int): The Discord user ID.
        chain_name (str): The name of the blockchain.
        validator_address (str): The validator's operator address.
        enabled (bool): True to enable notifications, False to disable.

    Returns:
        bool: True if the notification status was updated, False if the validator was not found.
    """
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE validators SET notifications_enabled = ? WHERE user_id = ? AND chain_name = ? AND validator_address = ?",
        (1 if enabled else 0, user_id, chain_name, validator_address)
    )
    conn.commit()
    rows_affected = cursor.rowcount
    conn.close()
    return rows_affected > 0