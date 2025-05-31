# Cosmos Validator Monitoring Discord Bot

This Discord bot provides real-time monitoring and alerts for your Cosmos SDK-based blockchain validators directly within your Discord server. Stay informed about your validator's operational status, missed blocks, and other critical events.

## ✨ Features

* **Multi-Chain Support**: Easily add and monitor validators across various Cosmos SDK chains. The bot is designed to be highly configurable for new chains.
* **Real-time Alerts**: Get instant notifications for crucial validator events:
    * Validator being **JAILED** (🚨) or recovering from a jailed state.
    * Changes in staking status (e.g., `BONDED`, `UNBONDING`, `UNBONDED`).
    * Significant increases in **Missed Blocks** (if supported by the chain's API).
    * API errors when fetching validator data, indicating potential issues with the chain's RPC endpoint.
* **Detailed Status Information**: Retrieve comprehensive current validator status, including moniker, jailed status, total stake, and estimated uptime.
* **Personalized Monitoring**: Register your own validators and receive dedicated mentions for important alerts, ensuring you don't miss critical updates.
* **Easy Management**: Simple Discord slash commands allow you to register, unregister, and check the status of your monitored validators effortlessly.

## ⛓️ Supported Chains

The bot's configuration is flexible and can be expanded to support any Cosmos SDK chain. Currently, it includes configurations for:

* **Empe Testnet**
* **Lumera Testnet**

To add more chains, you'll need to update the `SUPPORTED_CHAINS` dictionary in `bot.py` with the chain's REST API URL, validator operator prefix, consensus address prefix, and token symbol.

## 🚀 Setup Instructions

Follow these steps to get the bot up and running on your system.

### 1. Clone the Repository

```bash
git clone https://github.com/astrostake/discord-cosmos-validator-monitor.git
cd discord-cosmos-validator-monitor
```

### 2. Create a Discord Bot and Obtain Your Token

1. Navigate to the [Discord Developer Portal](https://discord.com/developers/applications).
2. Log in and click "New Application".
3. Provide a name for your application (e.g., "Cosmos Validator Monitor").
4. Go to the "Bot" tab on the left sidebar.
5. Click "Add Bot" and confirm with "Yes, do it!".
6. Under "Privileged Gateway Intents", ensure **PRESENCE INTENT** and **MESSAGE CONTENT INTENT** are enabled. These are crucial for the bot's functionality.
7. Click "Reset Token" and securely copy your bot token. Never share this token or commit it directly to version control!

### 3. Configure Environment Variables

Create a new file named `.env` in the root directory of your project (where `bot.py` is located).
```dotenv
DISCORD_BOT_TOKEN=YOUR_DISCORD_BOT_TOKEN_HERE
```

Replace `YOUR_DISCORD_BOT_TOKEN_HERE` with the actual bot token you copied in the previous step. Refer to `.env.example` for the correct format.

### 4. Install Dependencies

It is highly recommended to set up a Python virtual environment to manage dependencies.

```bash
# Create a virtual environment
python -m venv venv

# Activate the virtual environment
# On Windows:
.\venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate

# Install required packages
pip install -r requirements.txt
```

### 5. Run the Bot

```bash
python bot.py
```

You should see output indicating that the bot is logging in and is ready.

### 6. Invite the Bot to Your Discord Server

1. Return to the Discord Developer Portal for your bot application.
2. Go to the "OAuth2" -> "URL Generator" tab.
3. Under "Scopes", select bot.
4. Under "Bot Permissions", select the following permissions:
    - `Read Messages/View Channels`
    - `Send Messages`
    - `Embed Links`
    - `Manage Messages` (optional, for potential future message management features)
    - `Mention Everyone` (optional, if you want the bot to be able to mention users for alerts)
5. Copy the generated URL and open it in your web browser.
6. Choose the Discord server you wish to invite the bot to and click "Authorize".

### 7. Sync Slash Commands

After the bot is running and added to your server, you need to synchronize its slash commands. In any Discord channel where the bot has permission to send messages, type:

`!sync`

- Note: This command can only be executed by the bot's owner (the Discord account that created the bot application).

## 📋 Commands
All bot commands are implemented as Discord slash commands, accessible by typing / in the chat and selecting from the suggestions.

- `/help`: Displays detailed information about the bot's features and a list of all available commands.
- `/list_chains`: Shows all currently supported blockchain networks, including their API URLs and validator prefixes.
- `/register <chain_name> <validator_address>`: Adds a validator to your personal monitoring list. Notifications for this validator will be sent to the channel where this command was used.
    - Example: `/register empe empevaloper123abc...`
- `/unregister <chain_name> <validator_address>`: Removes a validator from your monitoring list.
- `/myvalidators`: Provides an overview of all validators you have registered, showing their current real-time status.
- `/vals <chain_name>`: Lists and displays the real-time status of only the validators you have registered on a specific chain.
- `/validator_status <chain_name> <validator_address>`: Fetches and displays the immediate status of any given validator, whether it's registered by you or not.
- `/set_notifications <chain_name> <validator_address> <on/off>`: Toggles (enables/disables) notification alerts for a specific validator you have registered.
- `/notification_channel <chain_name> <validator_address>`: Shows which Discord channel is configured to receive notifications for a particular registered validator.
- `/test_notification`: Sends a sample notification embed to the current channel, demonstrating the bot's alert message format.

## 📂 Project Structure

```
.
├── bot.py                # Core Discord bot logic, command handlers, and monitoring tasks.
├── db_manager.py         # Module for handling all SQLite database interactions (add, remove, update validators).
├── validator_monitor.db  # SQLite database file; automatically created upon first bot run.
├── .env                  # Stores sensitive environment variables (e.g., DISCORD_BOT_TOKEN). This file is ignored by Git.
├── .env.example          # A template file to guide users on setting up their .env.
├── .gitignore            # Defines files and directories that Git should ignore.
├── requirements.txt      # Lists all Python dependencies required to run the bot.
└── README.md             # This comprehensive README file.
```

## 🤝 Contributing

Contributions are welcome! If you have suggestions, bug reports, or would like to contribute code, please feel free to open an issue or submit a pull request.

## 📄 License
This project is open-source and distributed under the MIT License.
