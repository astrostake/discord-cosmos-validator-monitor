import sqlite3
import datetime

DATABASE_NAME = 'validator_monitor.db'

def init_db():
    """Initializes the database and creates tables if they don't exist."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS validators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,            -- Discord user ID who registered
            channel_id INTEGER NOT NULL,         -- Channel ID for notifications
            chain_name TEXT NOT NULL,            -- Chain name (e.g., 'empe', 'lumera')
            validator_address TEXT NOT NULL UNIQUE, -- Validator address (e.g., empevaloper...)
            moniker TEXT,                        -- Last known moniker (can be updated)
            status TEXT,                         -- Last known status (e.g., BONDED, JAILED, UNBONDED)
            missed_blocks INTEGER,               -- Last known missed blocks count (-1 if inaccessible)
            last_check_time TEXT,                -- Last check time (ISO format string)
            notifications_enabled BOOLEAN DEFAULT 1 -- Whether notifications are active (1=True, 0=False)
        );
    ''')
    # --- NEW TABLE FOR GOVERNANCE AND UPGRADE NOTIFICATIONS ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chain_notification_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id INTEGER NOT NULL,
            chain_name TEXT NOT NULL,
            notify_gov_enabled BOOLEAN DEFAULT 0,
            notify_upgrade_enabled BOOLEAN DEFAULT 0,
            mention_type TEXT, -- 'here', 'everyone', or NULL
            UNIQUE(channel_id, chain_name)
        );
    ''')
    conn.commit()
    conn.close()

def add_validator(user_id, channel_id, chain_name, validator_address, moniker=None):
    """Adds a new validator to the database."""
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
        return False
    finally:
        conn.close()

def remove_validator(user_id, chain_name, validator_address):
    """Removes a validator from the database."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM validators WHERE user_id = ? AND chain_name = ? AND validator_address = ?", (user_id, chain_name, validator_address))
    conn.commit()
    rows_affected = cursor.rowcount
    conn.close()
    return rows_affected > 0

def get_user_validators(user_id):
    """Retrieves a list of validators registered by a specific user."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT chain_name, validator_address, moniker, status, missed_blocks FROM validators WHERE user_id = ?", (user_id,))
    validators = cursor.fetchall()
    conn.close()
    return validators

def get_user_validators_by_chain(user_id, chain_name):
    """Retrieves a list of validators registered by a specific user for a given chain."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT chain_name, validator_address, moniker, status, missed_blocks FROM validators WHERE user_id = ? AND chain_name = ?",
        (user_id, chain_name)
    )
    validators = cursor.fetchall()
    conn.close()
    return validators

def get_user_validator_details(user_id, chain_name, validator_address):
    """Retrieves full details for a specific validator registered by a user."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT chain_name, validator_address, user_id, channel_id, moniker, status, missed_blocks, notifications_enabled FROM validators WHERE user_id = ? AND chain_name = ? AND validator_address = ?",
        (user_id, chain_name, validator_address)
    )
    validator = cursor.fetchone()
    conn.close()
    return validator # Returns None if not found, or a tuple of details


def get_all_validators_to_monitor():
    """Retrieves all registered validators for monitoring."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT chain_name, validator_address, user_id, channel_id, moniker, status, missed_blocks FROM validators WHERE notifications_enabled = 1")
    validators = cursor.fetchall()
    conn.close()
    return validators

def update_validator_status(chain_name, validator_address, new_status, new_missed_blocks, last_check_time, moniker=None):
    """Updates the validator's status and optionally moniker in the database."""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    if moniker:
        # If moniker is provided, update moniker along with other fields
        cursor.execute(
            "UPDATE validators SET status = ?, missed_blocks = ?, last_check_time = ?, moniker = ? WHERE chain_name = ? AND validator_address = ?",
            (new_status, new_missed_blocks, last_check_time, moniker, chain_name, validator_address)
        )
    else:
        # If moniker is NOT provided, update only status, missed_blocks, and last_check_time
        cursor.execute(
            "UPDATE validators SET status = ?, missed_blocks = ?, last_check_time = ? WHERE chain_name = ? AND validator_address = ?",
            (new_status, new_missed_blocks, last_check_time, chain_name, validator_address)
        )
    conn.commit()
    conn.close()

def set_validator_notifications(user_id, chain_name, validator_address, enabled):
    """Sets the notification status for a specific validator."""
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

# --- NEW FUNCTIONS FOR CHAIN NOTIFICATION SETTINGS ---

def set_chain_notification_preference(channel_id, chain_name, notify_gov_enabled, notify_upgrade_enabled, mention_type):
    """
    Sets or updates notification preferences for governance and upgrades for a specific channel and chain.
    """
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO chain_notification_settings (channel_id, chain_name, notify_gov_enabled, notify_upgrade_enabled, mention_type)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(channel_id, chain_name) DO UPDATE SET
                notify_gov_enabled = ?,
                notify_upgrade_enabled = ?,
                mention_type = ?;
            """,
            (channel_id, chain_name, 1 if notify_gov_enabled else 0, 1 if notify_upgrade_enabled else 0, mention_type,
             1 if notify_gov_enabled else 0, 1 if notify_upgrade_enabled else 0, mention_type)
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"Error setting chain notification preference: {e}")
        return False
    finally:
        conn.close()

def get_chain_notification_preferences(chain_name):
    """
    Retrieves all channels configured to receive notifications for a specific chain's governance or upgrades.
    Returns a list of dictionaries with channel_id, notify_gov_enabled, notify_upgrade_enabled, and mention_type.
    """
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT channel_id, notify_gov_enabled, notify_upgrade_enabled, mention_type
        FROM chain_notification_settings
        WHERE chain_name = ? AND (notify_gov_enabled = 1 OR notify_upgrade_enabled = 1);
        """,
        (chain_name,)
    )
    # Fetch all results and convert to list of dictionaries for easier access
    columns = [description[0] for description in cursor.description]
    results = [dict(zip(columns, row)) for row in cursor.fetchall()]
    conn.close()
    return results

def get_all_chain_notification_chains():
    """
    Retrieves all unique chain names that have notification settings configured.
    Useful for monitor tasks to know which chains to check.
    """
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT chain_name FROM chain_notification_settings WHERE notify_gov_enabled = 1 OR notify_upgrade_enabled = 1;")
    chains = [row[0] for row in cursor.fetchall()]
    conn.close()
    return chains
