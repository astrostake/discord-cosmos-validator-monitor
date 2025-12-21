import sqlite3
import datetime
import logging

DATABASE_NAME = 'validator_monitor.db'

def init_db():
    """Initializes the database and creates tables if they don't exist."""
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS validators (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                chain_name TEXT NOT NULL,
                validator_address TEXT NOT NULL UNIQUE,
                moniker TEXT,
                status TEXT,
                missed_blocks INTEGER,
                last_check_time TEXT,
                notifications_enabled BOOLEAN DEFAULT 1,
                last_total_stake REAL DEFAULT 0
            );
        ''')
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

        # MIGRATION HELPER: Cek apakah kolom last_total_stake sudah ada (untuk user lama)
        try:
            cursor.execute("SELECT last_total_stake FROM validators LIMIT 1")
        except sqlite3.OperationalError:
            # Jika error, berarti kolom belum ada. Tambahkan sekarang.
            logging.info("Migrating DB: Adding last_total_stake column...")
            cursor.execute("ALTER TABLE validators ADD COLUMN last_total_stake REAL DEFAULT 0")

def add_validator(user_id, channel_id, chain_name, validator_address, moniker=None):
    """Adds a new validator to the database."""
    try:
        with sqlite3.connect(DATABASE_NAME) as conn:
            cursor = conn.cursor()
            initial_status = "UNKNOWN"
            initial_missed_blocks = -1
            current_time = datetime.datetime.now().isoformat()

            cursor.execute(
                "INSERT INTO validators (user_id, channel_id, chain_name, validator_address, moniker, status, missed_blocks, last_check_time) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, channel_id, chain_name, validator_address, moniker, initial_status, initial_missed_blocks, current_time)
            )
        return True
    except sqlite3.IntegrityError:
        # This error occurs if the validator_address is not unique, which is expected.
        return False

def remove_validator(user_id, chain_name, validator_address):
    """Removes a validator from the database."""
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM validators WHERE user_id = ? AND chain_name = ? AND validator_address = ?", (user_id, chain_name, validator_address))
        return cursor.rowcount > 0

def get_user_validators(user_id):
    """Retrieves a list of validators registered by a specific user."""
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT chain_name, validator_address, moniker, status, missed_blocks FROM validators WHERE user_id = ?", (user_id,))
        return cursor.fetchall()

def get_user_validators_by_chain(user_id, chain_name):
    """Retrieves a list of validators registered by a user for a given chain."""
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT chain_name, validator_address, moniker, status, missed_blocks FROM validators WHERE user_id = ? AND chain_name = ?",
            (user_id, chain_name)
        )
        return cursor.fetchall()

def get_user_validator_details(user_id, chain_name, validator_address):
    """Retrieves full details for a specific validator registered by a user."""
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM validators WHERE user_id = ? AND chain_name = ? AND validator_address = ?",
            (user_id, chain_name, validator_address)
        )
        return cursor.fetchone()

def get_all_validators_to_monitor():
    """Retrieves all registered validators for monitoring."""
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT chain_name, validator_address, user_id, channel_id, moniker, status, missed_blocks, last_total_stake FROM validators WHERE notifications_enabled = 1")
        return cursor.fetchall()

def update_validator_status(chain_name, validator_address, new_status, new_missed_blocks, last_check_time, moniker=None, new_stake=None):
    """Updates the validator's status, moniker, and stake in the database."""
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        
        # Logika query dinamis tergantung parameter yang dikirim
        if moniker and new_stake is not None:
             cursor.execute(
                "UPDATE validators SET status = ?, missed_blocks = ?, last_check_time = ?, moniker = ?, last_total_stake = ? WHERE chain_name = ? AND validator_address = ?",
                (new_status, new_missed_blocks, last_check_time, moniker, new_stake, chain_name, validator_address)
            )
        elif moniker:
             cursor.execute(
                "UPDATE validators SET status = ?, missed_blocks = ?, last_check_time = ?, moniker = ? WHERE chain_name = ? AND validator_address = ?",
                (new_status, new_missed_blocks, last_check_time, moniker, chain_name, validator_address)
            )
        elif new_stake is not None:
            cursor.execute(
                "UPDATE validators SET status = ?, missed_blocks = ?, last_check_time = ?, last_total_stake = ? WHERE chain_name = ? AND validator_address = ?",
                (new_status, new_missed_blocks, last_check_time, new_stake, chain_name, validator_address)
            )
        else:
            cursor.execute(
                "UPDATE validators SET status = ?, missed_blocks = ?, last_check_time = ? WHERE chain_name = ? AND validator_address = ?",
                (new_status, new_missed_blocks, last_check_time, chain_name, validator_address)
            )

def set_validator_notifications(user_id, chain_name, validator_address, enabled):
    """Sets the notification status for a specific validator."""
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE validators SET notifications_enabled = ? WHERE user_id = ? AND chain_name = ? AND validator_address = ?",
            (1 if enabled else 0, user_id, chain_name, validator_address)
        )
        return cursor.rowcount > 0

def set_chain_notification_preference(channel_id, chain_name, notify_gov_enabled, notify_upgrade_enabled, mention_type):
    """Sets or updates notification preferences for a specific channel and chain."""
    try:
        with sqlite3.connect(DATABASE_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO chain_notification_settings (channel_id, chain_name, notify_gov_enabled, notify_upgrade_enabled, mention_type)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(channel_id, chain_name) DO UPDATE SET
                    notify_gov_enabled = excluded.notify_gov_enabled,
                    notify_upgrade_enabled = excluded.notify_upgrade_enabled,
                    mention_type = excluded.mention_type;
                """,
                (channel_id, chain_name, 1 if notify_gov_enabled else 0, 1 if notify_upgrade_enabled else 0, mention_type)
            )
        return True
    except Exception as e:
        logging.error(f"Error setting chain notification preference: {e}")
        return False

def get_chain_notification_preferences(chain_name):
    """Retrieves all channels configured to receive notifications for a specific chain."""
    with sqlite3.connect(DATABASE_NAME) as conn:
        conn.row_factory = sqlite3.Row # Allows accessing columns by name
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT channel_id, notify_gov_enabled, notify_upgrade_enabled, mention_type
            FROM chain_notification_settings
            WHERE chain_name = ? AND (notify_gov_enabled = 1 OR notify_upgrade_enabled = 1);
            """,
            (chain_name,)
        )
        return [dict(row) for row in cursor.fetchall()]

def get_all_chain_notification_chains():
    """Retrieves all unique chain names that have notification settings configured."""
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT chain_name FROM chain_notification_settings WHERE notify_gov_enabled = 1 OR notify_upgrade_enabled = 1;")
        return [row[0] for row in cursor.fetchall()]

def get_channels_with_validator_count(chain_name: str):
    """Retrieves channels with the count of validators registered in them for a specific chain."""
    with sqlite3.connect(DATABASE_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT channel_id, COUNT(id) as validator_count
            FROM validators
            WHERE chain_name = ?
            GROUP BY channel_id
            ORDER BY validator_count DESC;
            """,
            (chain_name,)
        )
        return [dict(row) for row in cursor.fetchall()]
