# Cosmos Validator Monitoring Discord Bot

This Discord bot provides real-time monitoring and alerts for your Cosmos SDK-based blockchain validators directly within your Discord server. Stay informed about your validator's operational status, missed blocks, and other critical events with clean, professional embeds.

## ✨ Features

* **Multi-Chain Support**: Easily add and monitor validators across various Cosmos SDK chains by editing a simple YAML configuration file.
* **Real-time Alerts**: Get instant notifications for crucial events:
    * Validator being **JAILED** (🚨) or recovering.
    * Changes in staking status (e.g., `BONDED`, `UNBONDING`).
    * Reaching a threshold of **Missed Blocks**.
    * New **Governance Proposals** and changes in their status (Voting Period, Passed, Rejected).
    * Scheduled **Chain Upgrades**.
* **Detailed & Modern Status**: Retrieve comprehensive validator status with a clean embed design, including moniker, jailed status, total stake, and an estimated uptime with a visual progress bar.
* **Personalized Monitoring**: Register your validators and receive dedicated mentions for important alerts.
* **Easy Management**: Simple and intuitive Discord slash commands allow you to manage your monitoring list effortlessly.

## ⛓️ Supported Chains

The bot's configuration is highly flexible. To add, remove, or modify a supported chain, simply edit the **`config.yaml`** file.

Currently, the default configuration includes:

* **Empeiria Testnet**
* **Lumera Mainnet & Testnet**
* **Paxi Mainnet**
* **Safrochain Testnet**

## 🚀 Setup Instructions

Follow these steps to get the bot up and running.

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
6. Under "Privileged Gateway Intents", ensure **SERVER MEMBERS INTENT** and **MESSAGE CONTENT INTENT** are enabled. These are crucial for the bot's functionality.
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

## 📋 Commands
All bot commands are implemented as Discord slash commands, accessible by typing / in the chat and selecting from the suggestions.

- `/help`: Displays detailed information about the bot's features and a list of all available commands.
- `/list_chains`: Shows all currently supported blockchain networks, including their API URLs and validator prefixes.
- `/register <chain_name> <validator_address>`: Adds a validator to your personal monitoring list. Notifications for this validator will be sent to the channel where this command was used.
    - Example: `/register empe empevaloper123abc...`
- `/unregister <chain_name> <validator_address>`: Removes a validator from your monitoring list.
- `/myvalidators`: Provides an overview of all validators you have registered, showing their current real-time status.
- `/validator_status <chain_name> <validator_address>`: Fetches and displays the immediate status of any given validator, whether it's registered by you or not.
- `/set_chain_notifications <chain> <gov_on/off> <up_on/off> <mention_here>`: Configure governance & upgrade alerts for THIS channel.
- `/active_proposals <chain_name>`: Displays all active governance proposals for a specific chain.
- `/test_notification`: Sends a sample notification embed to the current channel, demonstrating the bot's alert message format.

## 📂 Project Structure

The project uses a modern, modular structure for better organization and scalability.

```
.
├── bot.py                # Main bot entry point. Loads config and Cogs.
├── config.yaml           # All chain-specific configurations (API URLs, prefixes, etc.).
├── db_manager.py         # Handles all SQLite database interactions.
├── requirements.txt      # Lists all Python dependencies.
├── .env                  # Stores the secret Discord bot token.
├── validator_monitor.db  # SQLite database file (created automatically).
│
├── cogs/                 # Directory for all bot modules (Cogs).
│   ├── general_commands.py      # Contains general-purpose commands (/help, /list_chains).
│   ├── monitoring_tasks.py      # Contains all background monitoring loops.
│   └── validator_commands.py    # Contains validator-specific commands (/register, /myvalidators).
│
└── utils/                # Directory for helper functions.
    ├── api_helpers.py           # Functions for fetching and processing data from chain APIs.
    └── embed_factory.py         # Functions for creating standardized Discord embeds.
```

## 🤝 Contributing

Contributions are welcome! If you have suggestions, bug reports, or would like to contribute code, please feel free to open an issue or submit a pull request.

## 📄 License
This project is open-source and distributed under the MIT License.
