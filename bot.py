import discord
from discord.ext import commands, tasks
import requests
import asyncio
import os
import datetime
import hashlib
import base64
from typing import Optional

from dotenv import load_dotenv
from bech32 import bech32_encode, convertbits

import db_manager

load_dotenv()

# --- Configuration ---
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')

# Define supported blockchain networks and their configurations.
# Each chain includes REST API URL, validator address prefixes, token symbol,
# and endpoints for slashing information.
SUPPORTED_CHAINS = {
    "empe": {
        "rest_api_url": "https://lcd-testnet.empe.io",
        "valoper_prefix": "empevaloper",
        "valcons_prefix": "empevalcons",
        "token_symbol": "EMPE",
        "missed_blocks_supported": True,
        "signing_infos_endpoint": "/cosmos/slashing/v1beta1/signing_infos?pagination.limit=500",
        "slashing_params_endpoint": "/cosmos/slashing/v1beta1/params"
    },
    "lumera": {
        "rest_api_url": "https://lumera-testnet-api.polkachu.com",
        "valcons_prefix": "lumeravalcons",
        "token_symbol": "LUM",
        "missed_blocks_supported": True,
        "signing_infos_endpoint": "/cosmos/slashing/v1beta1/signing_infos?pagination.limit=300",
        "slashing_params_endpoint": "/cosmos/slashing/v1beta1/params"
    },
}

# Threshold for missed blocks to trigger a warning notification.
MISSED_BLOCKS_THRESHOLD = 50
# Interval (in seconds) at which validators are monitored.
MONITOR_INTERVAL_SECONDS = 60

# Configure Discord bot intents for necessary permissions.
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

# Initialize the Discord bot with a command prefix and intents.
bot = commands.Bot(command_prefix='!', intents=intents)

# Caches for slashing information and parameters to reduce API calls.
_slashing_info_cache = {}
_slashing_params_cache = {}

def pubkey_to_consensus_address(pubkey_b64: str, valcons_prefix: str) -> Optional[str]:
    """
    Converts a base64 encoded validator public key to its Bech32 consensus address.

    Args:
        pubkey_b64 (str): The base64 encoded public key of the validator.
        valcons_prefix (str): The Bech32 prefix for consensus addresses (e.g., 'empevalcons').

    Returns:
        Optional[str]: The Bech32 consensus address, or None if conversion fails.
    """
    try:
        pubkey_bytes = base64.b64decode(pubkey_b64)

        # Handle different public key formats (Tendermint/Cosmos SDK specific)
        raw_pubkey_bytes = None
        if len(pubkey_bytes) == 32:  # Ed25519 public key (Tendermint)
            raw_pubkey_bytes = pubkey_bytes
        elif len(pubkey_bytes) == 36 and pubkey_bytes[0:4] == b'\x16$\xde\x64':  # Amino-encoded secp256k1
            raw_pubkey_bytes = pubkey_bytes[4:]
        elif len(pubkey_bytes) == 33 and (pubkey_bytes[0] == 0x02 or pubkey_bytes[0] == 0x03): # secp256k1 compressed
            raw_pubkey_bytes = pubkey_bytes
        else:
            print(f"Warning: Unexpected public key format/length for {pubkey_b64}: {len(pubkey_bytes)} bytes.")
            return None

        if raw_pubkey_bytes is None:
            return None

        # Hash the raw public key to get the address bytes (first 20 bytes of SHA256 hash)
        address_bytes = hashlib.sha256(raw_pubkey_bytes).digest()[:20]

        # Convert 8-bit data to 5-bit data for Bech32 encoding
        five_bit_data = convertbits(address_bytes, 8, 5, True)

        # Encode the 5-bit data into a Bech32 address
        return bech32_encode(valcons_prefix, five_bit_data)
    except Exception as e:
        print(f"Error in pubkey_to_consensus_address for {pubkey_b64}: {e}")
        return None

async def get_validator_info(chain_name: str, validator_address: str) -> dict:
    """
    Fetches detailed information about a validator from the specified blockchain's REST API.

    Args:
        chain_name (str): The name of the blockchain (e.g., 'empe', 'lumera').
        validator_address (str): The validator's `valoper` address.

    Returns:
        dict: A dictionary containing validator details like moniker, status, jailed status,
              missed blocks, total stake, estimated uptime, and success status.
    """
    chain_config = SUPPORTED_CHAINS.get(chain_name)
    if not chain_config:
        return {'success': False, 'error': f"Chain '{chain_name}' is not supported."}

    rest_api_url = chain_config["rest_api_url"]
    valcons_prefix = chain_config["valcons_prefix"]
    missed_blocks_supported = chain_config["missed_blocks_supported"]
    token_symbol = chain_config["token_symbol"]
    valoper_prefix = chain_config.get("valoper_prefix", "") # Safely get valoper_prefix

    try:
        # Fetch staking validator details
        staking_url = f"{rest_api_url}/cosmos/staking/v1beta1/validators/{validator_address}"
        staking_response = requests.get(staking_url, timeout=10)
        staking_response.raise_for_status() # Raise an exception for HTTP errors
        staking_data = staking_response.json()

        validator_details = staking_data['validator']
        moniker = validator_details['description']['moniker']
        status = validator_details['status']
        jailed = validator_details['jailed']

        display_status = ""
        if jailed:
            display_status = "🚨 JAILED"
        elif status == "BOND_STATUS_BONDED":
            display_status = "BONDED"
        elif status == "BOND_STATUS_UNBONDING":
            display_status = "UNBONDING"
        elif status == "BOND_STATUS_UNBONDED":
            display_status = "UNBONDED"
        else:
            display_status = status # Fallback for other or unknown statuses

        # Format total stake for display
        total_stake_raw = float(validator_details.get('delegator_shares', '0'))
        total_stake_human = f"{total_stake_raw / 1_000_000:,.2f} {token_symbol}" # Assuming 6 decimal places for tokens

        missed_blocks = -1
        estimated_uptime = "N/A"

        # If missed blocks monitoring is supported for this chain and caches are populated
        if missed_blocks_supported and chain_name in _slashing_info_cache and chain_name in _slashing_params_cache:
            consensus_pubkey_b64 = validator_details['consensus_pubkey']['key']
            validator_cons_address = pubkey_to_consensus_address(consensus_pubkey_b64, valcons_prefix)

            if validator_cons_address:
                slashing_data_for_validator = _slashing_info_cache[chain_name].get(validator_cons_address)
                if slashing_data_for_validator:
                    missed_blocks = int(slashing_data_for_validator.get('missed_blocks_counter', -1))

                    signed_blocks_window = int(_slashing_params_cache[chain_name].get('signed_blocks_window', '0'))
                    if signed_blocks_window > 0:
                        signed_blocks = signed_blocks_window - missed_blocks
                        uptime_percentage = (signed_blocks / signed_blocks_window) * 100
                        estimated_uptime = f"{uptime_percentage:.2f}%"
                    else:
                        estimated_uptime = "N/A (Window 0)" # Avoid division by zero
                else:
                    print(f"Validator {validator_address} ({chain_name}) cons address {validator_cons_address} not found in slashing info cache.")
                    missed_blocks = -1
            else:
                print(f"Could not derive consensus address for {validator_address} ({chain_name}). Skipping missed blocks lookup.")
                missed_blocks = -1

        return {
            'moniker': moniker,
            'status': display_status,
            'jailed': jailed,
            'missed_blocks': missed_blocks,
            'total_stake': total_stake_human,
            'estimated_uptime': estimated_uptime,
            'success': True
        }
    except requests.exceptions.RequestException as e:
        print(f"API request failed for {validator_address} ({chain_name}): {e}")
        return {'success': False, 'error': str(e)}
    except KeyError as e:
        print(f"Missing key in API response for {validator_address} ({chain_name}): {e}. Check API structure. (Perhaps validator not found?)")
        return {'success': False, 'error': f"Data structure mismatch or validator not found: {e}"}
    except Exception as e:
        print(f"An unexpected error occurred for {validator_address} ({chain_name}): {e}")
        return {'success': False, 'error': str(e)}

@bot.event
async def on_ready():
    """
    Event handler that runs when the bot successfully connects to Discord.
    Initializes the database and starts the validator monitoring loop.
    """
    print(f'Logged in as {bot.user.name} ({bot.user.id})')
    print('Bot is ready!')
    db_manager.init_db() # Ensure database table is created
    monitor_validators.start() # Start the background task

@bot.command()
@commands.is_owner() # This command can only be used by the bot owner
async def sync(ctx: commands.Context):
    """
    Syncs slash commands globally with Discord.
    Requires bot owner permissions.
    """
    try:
        await ctx.reply("Attempting to sync slash commands, this might take a few seconds...", mention_author=True)
        synced = await bot.tree.sync()
        await ctx.reply(f"✅ Successfully synced {len(synced)} slash commands globally.", mention_author=True)
        print(f"Synced {len(synced)} commands globally.")
    except Exception as e:
        await ctx.reply(f"❌ Error syncing commands: {e}", mention_author=True)
        print(f"Error syncing commands: {e}")

@tasks.loop(seconds=MONITOR_INTERVAL_SECONDS)
async def monitor_validators():
    """
    Background task that periodically monitors registered validators.
    It fetches current validator status, compares it with the last known status
    in the database, and sends Discord notifications for significant changes.
    """
    global _slashing_info_cache, _slashing_params_cache

    # First, refresh slashing info and params for all supported chains if necessary
    for chain_name, chain_config in SUPPORTED_CHAINS.items():
        if chain_config["missed_blocks_supported"]:
            # Fetch slashing parameters (e.g., signed_blocks_window)
            if chain_config.get("slashing_params_endpoint"):
                params_api_url = f"{chain_config['rest_api_url']}{chain_config['slashing_params_endpoint']}"
                try:
                    params_response = requests.get(params_api_url, timeout=10)
                    params_response.raise_for_status()
                    params_data = params_response.json()
                    _slashing_params_cache[chain_name] = {
                        'signed_blocks_window': params_data['params']['signed_blocks_window']
                    }
                    print(f"Successfully loaded slashing params for chain: {chain_name}")
                except requests.exceptions.RequestException as e:
                    print(f"Error loading slashing params for chain {chain_name} from {params_api_url}: {e}")
                    _slashing_params_cache[chain_name] = {} # Clear cache for this chain on error
                except Exception as e:
                    print(f"Unexpected error processing slashing params for chain {chain_name}: {e}")
                    _slashing_params_cache[chain_name] = {}
            else:
                print(f"No slashing params endpoint defined for chain: {chain_name}")
                _slashing_params_cache[chain_name] = {}

            # Fetch signing information (e.g., missed_blocks_counter)
            if chain_config.get("signing_infos_endpoint"):
                slashing_api_url = f"{chain_config['rest_api_url']}{chain_config['signing_infos_endpoint']}"
                try:
                    slashing_response = requests.get(slashing_api_url, timeout=15)
                    slashing_response.raise_for_status()
                    slashing_data = slashing_response.json()

                    # Store slashing info indexed by consensus address
                    chain_slashing_info = {
                        item['address']: item for item in slashing_data.get('info', [])
                    }
                    _slashing_info_cache[chain_name] = chain_slashing_info
                    print(f"Successfully loaded signing infos for chain: {chain_name}")
                except requests.exceptions.RequestException as e:
                    print(f"Error loading signing infos for chain {chain_name} from {slashing_api_url}: {e}")
                    _slashing_info_cache[chain_name] = {} # Clear cache for this chain on error
                except Exception as e:
                    print(f"Unexpected error processing signing infos for chain {chain_name}: {e}")
                    _slashing_info_cache[chain_name] = {}
            else:
                print(f"No signing infos endpoint defined for chain: {chain_name}")
                _slashing_info_cache[chain_name] = {}
        else:
            print(f"Slashing monitoring not enabled for chain: {chain_name}. Skipping API calls.")
            _slashing_info_cache[chain_name] = {}
            _slashing_params_cache[chain_name] = {}

    # Get all validators registered for monitoring from the database
    validators_to_monitor = db_manager.get_all_validators_to_monitor()

    if not validators_to_monitor:
        print("No validators registered to monitor. Skipping this loop.")
        return

    # Iterate through each registered validator and check its status
    for val_data in validators_to_monitor:
        chain_name, validator_address, user_id, channel_id, old_moniker, old_status, old_missed_blocks = val_data

        channel = bot.get_channel(channel_id)
        if not channel:
            print(f"Warning: Channel with ID {channel_id} for user {user_id} not found. Skipping notifications for this validator.")
            continue

        print(f"Checking validator: {validator_address} on chain: {chain_name}")
        current_time = datetime.datetime.now().isoformat()

        status_info = await get_validator_info(chain_name, validator_address)

        # --- Notification Logic ---
        send_notification = False
        mention_required = False
        alert_title_prefix = "Validator Update:" # Default title prefix
        embed_color = discord.Color.blue() # Default color

        # Get current data from API response; fallback to old data if API call failed
        current_moniker = status_info.get('moniker', old_moniker)
        current_status = status_info.get('status', "UNKNOWN")
        current_jailed = status_info.get('jailed', False)
        current_missed_blocks = status_info.get('missed_blocks', -1)

        if status_info['success']:
            # Case 1: JAILED status change
            if current_jailed and old_status != "🚨 JAILED":
                alert_title_prefix = "🚨 JAILED ALERT!"
                send_notification = True
                mention_required = True
                embed_color = discord.Color.red()
            elif not current_jailed and old_status == "🚨 JAILED": # Recovery from jailed
                alert_title_prefix = "✅ RECOVERY ALERT!"
                send_notification = True
                mention_required = True
                embed_color = discord.Color.green()
            # Case 2: API Recovery (from previous API_ERROR to success)
            elif old_status == "API_ERROR":
                alert_title_prefix = "✅ API RECOVERED!"
                send_notification = True
                mention_required = True
                embed_color = discord.Color.green()
            # Case 3: General Status Change (e.g., BONDED -> UNBONDING)
            elif current_status != old_status:
                alert_title_prefix = "ℹ️ STATUS CHANGE!"
                send_notification = True
                embed_color = discord.Color.light_grey()
            
            # Case 4: Missed Blocks Logic (only if supported and valid data)
            # This logic triggers if missed blocks increased significantly or crossed the threshold.
            if SUPPORTED_CHAINS[chain_name]["missed_blocks_supported"] and \
               current_missed_blocks != -1 and old_missed_blocks != -1:
                missed_block_diff = current_missed_blocks - old_missed_blocks

                if missed_block_diff > 0: # Only care if missed blocks actually increased
                    # Threshold breach (from below threshold to at or above)
                    if current_missed_blocks >= MISSED_BLOCKS_THRESHOLD and old_missed_blocks < MISSED_BLOCKS_THRESHOLD:
                        if not send_notification: # Don't override a JAILED/Recovery alert
                            alert_title_prefix = "⚠️ MISSED BLOCKS WARNING!"
                            embed_color = discord.Color.orange()
                        send_notification = True
                        mention_required = True # Always mention for threshold breach
                    # Significant increase while already above threshold
                    elif current_missed_blocks >= MISSED_BLOCKS_THRESHOLD and old_missed_blocks >= MISSED_BLOCKS_THRESHOLD and missed_block_diff >= (MISSED_BLOCKS_THRESHOLD / 5):
                        if not send_notification:
                            alert_title_prefix = "⚠️ MISSED BLOCKS INCREASE!"
                            embed_color = discord.Color.orange()
                        send_notification = True
                        mention_required = True # Always mention for significant increase above threshold
                    # Smaller but noticeable increase, if no other alert and status didn't change
                    elif missed_block_diff > (MISSED_BLOCKS_THRESHOLD / 10) and not send_notification and current_status == old_status:
                        alert_title_prefix = "📊 Missed Blocks Update!"
                        embed_color = discord.Color.gold()
                        send_notification = True
                        # No mention for minor missed block updates unless it's a threshold breach.

            # Case 5: Moniker Change (only if you want to notify for this AND no other significant alert is pending)
            if current_moniker != old_moniker and old_moniker is not None and not send_notification:
                alert_title_prefix = "📝 Moniker Updated!"
                send_notification = True
                embed_color = discord.Color.greyple()
                # No mention for moniker changes by default

        else: # API call was NOT successful (status_info['success'] is False)
            # Only send notification if previous status was NOT "API_ERROR"
            if old_status != "API_ERROR":
                alert_title_prefix = "❌ API ERROR!"
                send_notification = True
                mention_required = True
                embed_color = discord.Color.red()
            
            # For database update, set current_status to "API_ERROR"
            current_status = "API_ERROR" 
            # Keep other values as old ones for consistency in DB if API fails
            current_missed_blocks = old_missed_blocks
            current_moniker = old_moniker # Retain old moniker if API fails to get new data

        # Send the notification if `send_notification` is True
        if send_notification:
            target_user = None
            try:
                target_user = await bot.fetch_user(user_id)
            except discord.NotFound:
                print(f"User with ID {user_id} not found for mention.")
            except Exception as e:
                print(f"Error fetching user {user_id}: {e}")

            embed = discord.Embed(
                title=f"🔔 {alert_title_prefix}",
                description=f"Status update for your validator (`{validator_address}`).",
                color=embed_color,
                timestamp=datetime.datetime.now(datetime.timezone.utc)
            )
            embed.add_field(name="🌐 Chain", value=chain_name.upper(), inline=True)
            embed.add_field(name="🆔 Moniker", value=current_moniker if current_moniker else "N/A", inline=True)
            embed.add_field(name="📜 Address", value=f"`{validator_address}`", inline=False)
            embed.add_field(name="🚨 Jailed?", value="🚨 Yes" if current_jailed else "✅ No", inline=True)
            embed.add_field(name="📊 Status", value=current_status, inline=True)
            embed.add_field(name="📉 Missed Blocks", value=f"{current_missed_blocks}" if current_missed_blocks != -1 else "N/A", inline=True)
            embed.add_field(name="🔋 Total Stake", value=status_info.get('total_stake', "N/A"), inline=True)
            embed.add_field(name="⏱️ Estimated Uptime", value=status_info.get('estimated_uptime', "N/A"), inline=True)

            embed.set_footer(text=f"Monitored by {bot.user.name}")

            mention_text = target_user.mention + " " if target_user and mention_required else ""

            try:
                await channel.send(content=mention_text, embed=embed)
            except discord.errors.Forbidden:
                print(f"Error: Bot does not have permission to send messages in channel {channel.name} ({channel.id}) for user {user_id}. Please check bot permissions.")
            except Exception as e:
                print(f"Error sending message to Discord channel {channel.id}: {e}")

        # Always update the database with the latest status, even if no notification was sent.
        # This is crucial for the next loop iteration to correctly detect *changes*.
        db_manager.update_validator_status(chain_name, validator_address, current_status, current_missed_blocks, current_time, current_moniker)


@bot.tree.command(name="help", description="Provides info about what this bot does and how to use it.")
async def help_slash(interaction: discord.Interaction):
    """
    Sends an embed message detailing the bot's features and how to use its commands.
    """
    embed = discord.Embed(
        title="🤖 Cosmos Validator Monitoring Bot",
        description="This bot helps you monitor the status of your Cosmos validators across multiple chains.",
        color=discord.Color.brand_green(),
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    embed.add_field(
        name="Features",
        value=(
            "🌐 **Multi-Chain Support**: Monitor validators on various Cosmos chains.\n"
            "🔔 **Real-time Alerts**: Get instant notifications for critical events like:\n"
            "  - Validator being **JAILED** (🚨) or recovering.\n"
            "  - Changes in staking status (e.g., BONDED, UNBONDED).\n"
            "  - Significant **Missed Blocks** (if supported by chain API).\n"
            "📊 **Detailed Status**: Get current validator status, total stake, and estimated uptime.\n"
            "👤 **Personalized Monitoring**: Register your own validators and receive mentions."
        ),
        inline=False
    )
    embed.add_field(
        name="How to Use Commands",
        value=(
            "Type `/` in the chat to see available commands and their arguments.\n"
            "**`/help`**: Get this information about the bot.\n"
            "**`/list_chains`**: See which chains are supported.\n"
            "**`/register <chain_name> <validator_address>`**: Add your validator for monitoring.\n"
            "**`/validator_status <chain_name> <validator_address>`**: Get instant status for a specific validator.\n"
            "**`/vals <chain_name>`**: Display validators you registered on a specific chain.\n"
            "**`/set_notifications <chain_name> <validator_address> <on/off>`**: Manage alerts for your registered validator.\n"
            "**`/unregister <chain_name> <validator_address>`**: Remove a validator from your monitoring list.\n"
            "**`/myvalidators`**: See all validators you've registered.\n"
            "**`/notification_channel <chain_name> <validator_address>`**: Shows where notifications are sent for a specific validator.\n"
            "**`/test_notification`**: See an example of a bot notification."
        ),
        inline=False
    )
    embed.set_footer(text=f"Bot developed by {bot.user.name}")
    await interaction.response.send_message(embed=embed, ephemeral=False)

@bot.tree.command(name="list_chains", description="Displays a list of supported chains.")
async def list_chains_slash(interaction: discord.Interaction):
    """
    Displays a list of all supported blockchain chains with their details.
    """
    response = "**Supported Chains:**\n"
    for chain_id, config in SUPPORTED_CHAINS.items():
        response += f"- `{chain_id.upper()}` (API: {config['rest_api_url']})\n"
        response += f"  Validator Operator Prefix: `{config['valoper_prefix']}`\n" # Clarified prefix
        response += f"  Token Symbol: `{config['token_symbol']}`\n"
        response += f"  Missed Blocks Monitoring: {'✅ Supported' if config['missed_blocks_supported'] else '❌ Not Supported'}\n"
    await interaction.response.send_message(response, ephemeral=True)

@bot.tree.command(name="register", description="Registers a validator for monitoring on a specific chain.")
@discord.app_commands.describe(chain_name="Name of the chain (e.g., empe, lumera)", validator_address="Validator address (e.g., empevaloper1...)")
async def register_slash(interaction: discord.Interaction, chain_name: str, validator_address: str):
    """
    Registers a validator for monitoring by the bot.
    The bot will send notifications for this validator to the channel where it was registered.
    """
    await interaction.response.defer(ephemeral=True)

    chain_name = chain_name.lower()
    if chain_name not in SUPPORTED_CHAINS:
        await interaction.followup.send(f"❌ Chain `{chain_name}` is not supported. Use `/list_chains` to see supported chains.", ephemeral=True)
        return

    chain_config = SUPPORTED_CHAINS[chain_name]
    valoper_prefix = chain_config["valoper_prefix"]

    if not validator_address.startswith(valoper_prefix):
        await interaction.followup.send(f"Invalid validator address format for `{chain_name.upper()}` chain. Please use `{valoper_prefix}...`.", ephemeral=True)
        return

    await interaction.followup.send(f"Verifying validator `{validator_address}` on `{chain_name.upper()}` chain...", ephemeral=True)

    status_info = await get_validator_info(chain_name, validator_address)
    if not status_info['success']:
        await interaction.followup.send(f"❌ Could not find validator with address `{validator_address}` on `{chain_name.upper()}` chain or an API error occurred. Please double-check the address and chain.", ephemeral=True)
        return

    moniker = status_info['moniker']

    if db_manager.add_validator(interaction.user.id, interaction.channel_id, chain_name, validator_address, moniker):
        await interaction.followup.send(f"✅ Validator `{moniker} ({validator_address})` on **{chain_name.upper()}** Chain successfully registered for monitoring in this channel.", ephemeral=False)
    else:
        await interaction.followup.send(f"ℹ️ Validator `{validator_address}` on **{chain_name.upper()}** Chain is already registered by you.", ephemeral=False)

@bot.tree.command(name="unregister", description="Removes a validator from your monitoring list.")
@discord.app_commands.describe(chain_name="Chain name (e.g., empe, lumera)", validator_address="Validator address")
async def unregister_slash(interaction: discord.Interaction, chain_name: str, validator_address: str):
    """
    Removes a previously registered validator from the monitoring list for the user.
    """
    await interaction.response.defer(ephemeral=True)

    chain_name = chain_name.lower()
    if chain_name not in SUPPORTED_CHAINS:
        await interaction.followup.send(f"❌ Chain `{chain_name}` is not supported. Use `/list_chains` to see supported chains.", ephemeral=True)
        return

    if db_manager.remove_validator(interaction.user.id, chain_name, validator_address):
        await interaction.followup.send(f"✅ Validator `{validator_address}` on **{chain_name.upper()}** Chain successfully removed from your monitoring list.", ephemeral=True)
    else:
        await interaction.followup.send(f"ℹ️ Validator `{validator_address}` on **{chain_name.upper()}** Chain was not found in your monitoring list.", ephemeral=True)

@bot.tree.command(name="myvalidators", description="Displays a list of validators you have registered.")
async def myvalidators_slash(interaction: discord.Interaction):
    """
    Displays the real-time status of all validators registered by the user.
    """
    await interaction.response.defer(ephemeral=True)

    validators = db_manager.get_user_validators(interaction.user.id)
    if not validators:
        await interaction.followup.send("You have not registered any validators yet.", ephemeral=True)
        return

    embeds = []
    for chain_name, val_addr, _, _, _ in validators: # Unpack only relevant fields
        # Ensure slashing caches are updated if needed for the current chain
        # This part is crucial for accurate missed block info on manual checks too.
        if SUPPORTED_CHAINS.get(chain_name, {}).get("missed_blocks_supported", False):
            chain_config_slashing = SUPPORTED_CHAINS[chain_name]
            if chain_name not in _slashing_params_cache and chain_config_slashing.get("slashing_params_endpoint"):
                params_api_url = f"{chain_config_slashing['rest_api_url']}{chain_config_slashing['slashing_params_endpoint']}"
                try:
                    params_response = requests.get(params_api_url, timeout=10)
                    params_response.raise_for_status()
                    params_data = params_response.json()
                    _slashing_params_cache[chain_name] = {'signed_blocks_window': params_data['params']['signed_blocks_window']}
                except Exception as e:
                    print(f"Error loading slashing params for chain {chain_name} in /myvalidators: {e}")
                    _slashing_params_cache[chain_name] = {}
            if chain_name not in _slashing_info_cache and chain_config_slashing.get("signing_infos_endpoint"):
                slashing_api_url = f"{chain_config_slashing['rest_api_url']}{chain_config_slashing['signing_infos_endpoint']}"
                try:
                    slashing_response = requests.get(slashing_api_url, timeout=15)
                    slashing_response.raise_for_status()
                    slashing_data = slashing_response.json()
                    _slashing_info_cache[chain_name] = {item['address']: item for item in slashing_data.get('info', [])}
                except Exception as e:
                    print(f"Error loading signing infos for chain {chain_name} in /myvalidators: {e}")
                    _slashing_info_cache[chain_name] = {}

        status_info = await get_validator_info(chain_name, val_addr)

        if status_info['success']:
            moniker = status_info['moniker']
            status = status_info['status']
            jailed = status_info['jailed']
            missed_blocks = status_info['missed_blocks']
            total_stake = status_info['total_stake']
            estimated_uptime = status_info['estimated_uptime']

            embed = discord.Embed(
                title=f"📊 Validator Status: {moniker} ({chain_name.upper()})",
                description=f"Current information for `{val_addr}`.",
                color=discord.Color.blue(),
                timestamp=datetime.datetime.now(datetime.timezone.utc)
            )
            embed.add_field(name="🚨 Jailed?", value="🚨 Yes" if jailed else "✅ No", inline=True)
            embed.add_field(name="📊 Status", value=status, inline=True)
            embed.add_field(name="📉 Missed Blocks", value=f"{missed_blocks}" if missed_blocks != -1 else "N/A", inline=True)
            embed.add_field(name="🔋 Total Stake", value=total_stake, inline=True)
            embed.add_field(name="⏱️ Estimated Uptime", value=estimated_uptime, inline=True)
            embed.set_footer(text=f"Data from {bot.user.name}")
            embeds.append(embed)
        else:
            error_embed = discord.Embed(
                title=f"❌ Error for {val_addr} ({chain_name.upper()})",
                description=f"Could not retrieve status. Reason: `{status_info['error']}`.",
                color=discord.Color.red(),
                timestamp=datetime.datetime.now(datetime.timezone.utc)
            )
            error_embed.set_footer(text=f"Data from {bot.user.name}")
            embeds.append(error_embed)

    if embeds:
        # Discord allows sending up to 10 embeds at once per message
        for i in range(0, len(embeds), 10):
            await interaction.followup.send(embeds=embeds[i:i+10])
    else:
        # This case should ideally be caught by the initial `if not validators:`
        await interaction.followup.send("You have not registered any validators yet.", ephemeral=True)

@bot.tree.command(name="vals", description="Displays validators you registered on a specific chain.")
@discord.app_commands.describe(chain_name="Name of the chain (e.g., empe, lumera)")
async def vals_slash(interaction: discord.Interaction, chain_name: str):
    """
    Displays the real-time status of validators registered by the user on a specific chain.
    """
    await interaction.response.defer(ephemeral=False)

    target_chain_name = chain_name.lower()

    if target_chain_name not in SUPPORTED_CHAINS:
        await interaction.followup.send(f"❌ Chain `{target_chain_name}` is not supported. Use `/list_chains` to see supported chains.")
        return

    user_id = interaction.user.id
    
    validators = db_manager.get_user_validators_by_chain(user_id, target_chain_name)

    if not validators:
        await interaction.followup.send(
            f"ℹ️ You have not registered any validators on the **{target_chain_name.upper()}** chain.\n"
            f"To register a validator, use `/register {target_chain_name} <validator_address>`."
        )
        return

    embeds = []
    for chain, val_addr, _, _, _ in validators: # Unpack only relevant fields
        # Ensure slashing caches are updated if needed for the current chain for manual checks
        if SUPPORTED_CHAINS.get(chain, {}).get("missed_blocks_supported", False):
            chain_config_slashing = SUPPORTED_CHAINS[chain]
            if chain not in _slashing_params_cache and chain_config_slashing.get("slashing_params_endpoint"):
                params_api_url = f"{chain_config_slashing['rest_api_url']}{chain_config_slashing['slashing_params_endpoint']}"
                try:
                    params_response = requests.get(params_api_url, timeout=10)
                    params_response.raise_for_status()
                    params_data = params_response.json()
                    _slashing_params_cache[chain] = {'signed_blocks_window': params_data['params']['signed_blocks_window']}
                except Exception as e:
                    print(f"Error loading slashing params for chain {chain} in /vals: {e}")
                    _slashing_params_cache[chain] = {}
            if chain not in _slashing_info_cache and chain_config_slashing.get("signing_infos_endpoint"):
                slashing_api_url = f"{chain_config_slashing['rest_api_url']}{chain_config_slashing['signing_infos_endpoint']}"
                try:
                    slashing_response = requests.get(slashing_api_url, timeout=15)
                    slashing_response.raise_for_status()
                    slashing_data = slashing_response.json()
                    _slashing_info_cache[chain] = {item['address']: item for item in slashing_data.get('info', [])}
                except Exception as e:
                    print(f"Error loading signing infos for chain {chain} in /vals: {e}")
                    _slashing_info_cache[chain] = {}

        status_info = await get_validator_info(chain, val_addr)

        if status_info['success']:
            moniker = status_info['moniker']
            status = status_info['status']
            jailed = status_info['jailed']
            missed_blocks = status_info['missed_blocks']
            total_stake = status_info['total_stake']
            estimated_uptime = status_info['estimated_uptime']

            embed = discord.Embed(
                title=f"📊 Validator Status: {moniker} ({chain.upper()})",
                description=f"Current information for `{val_addr}`.",
                color=discord.Color.blue(),
                timestamp=datetime.datetime.now(datetime.timezone.utc)
            )
            embed.add_field(name="🚨 Jailed?", value="🚨 Yes" if jailed else "✅ No", inline=True)
            embed.add_field(name="📊 Status", value=status, inline=True)
            embed.add_field(name="📉 Missed Blocks", value=f"{missed_blocks}" if missed_blocks != -1 else "N/A", inline=True)
            embed.add_field(name="🔋 Total Stake", value=total_stake, inline=True)
            embed.add_field(name="⏱️ Estimated Uptime", value=estimated_uptime, inline=True)
            embed.set_footer(text=f"Data from {bot.user.name}")
            embeds.append(embed)
        else:
            error_embed = discord.Embed(
                title=f"❌ Error for {val_addr} ({chain.upper()})",
                description=f"Could not retrieve status. Reason: `{status_info['error']}`.",
                color=discord.Color.red(),
                timestamp=datetime.datetime.now(datetime.timezone.utc)
            )
            error_embed.set_footer(text=f"Data from {bot.user.name}")
            embeds.append(error_embed)

    if embeds:
        for i in range(0, len(embeds), 10):
            await interaction.followup.send(embeds=embeds[i:i+10])
    else:
        await interaction.followup.send(
            f"ℹ️ You have not registered any validators on the **{target_chain_name.upper()}** chain.\n"
            f"To register a validator, use `/register {target_chain_name} <validator_address>`."
        )

@bot.tree.command(name="validator_status", description="Gets status for a specific validator.")
@discord.app_commands.describe(
    chain_name="Name of the chain (e.g., empe, lumera)",
    validator_address="Validator address (e.g., empevaloper1...)"
)
async def validator_status_slash(interaction: discord.Interaction, chain_name: str, validator_address: str):
    """
    Retrieves and displays the current status of a single validator on a specified chain.
    """
    await interaction.response.defer(ephemeral=False)

    target_chain_name = chain_name.lower()
    target_validator_address = validator_address

    chain_config = SUPPORTED_CHAINS.get(target_chain_name)
    if not chain_config:
        await interaction.followup.send(f"❌ Chain `{target_chain_name}` is not supported. Use `/list_chains` to see supported chains.")
        return

    valoper_prefix = chain_config["valoper_prefix"]
    if not target_validator_address.startswith(valoper_prefix):
        await interaction.followup.send(f"Invalid validator address format for `{target_chain_name.upper()}` chain. Please use `{valoper_prefix}...`.")
        return

    await interaction.followup.send(f"Checking status for `{target_validator_address}` on **{target_chain_name.upper()}** Chain...", ephemeral=False)

    global _slashing_info_cache, _slashing_params_cache

    # Fetch slashing info and params if supported and not cached for this specific chain
    if SUPPORTED_CHAINS.get(target_chain_name, {}).get("missed_blocks_supported", False):
        chain_config_slashing = SUPPORTED_CHAINS[target_chain_name]

        if target_chain_name not in _slashing_params_cache and chain_config_slashing.get("slashing_params_endpoint"):
            params_api_url = f"{chain_config_slashing['rest_api_url']}{chain_config_slashing['slashing_params_endpoint']}"
            try:
                params_response = requests.get(params_api_url, timeout=10)
                params_response.raise_for_status()
                params_data = params_response.json()
                _slashing_params_cache[target_chain_name] = {
                    'signed_blocks_window': params_data['params']['signed_blocks_window']
                }
            except Exception as e:
                print(f"Error loading slashing params for chain {target_chain_name} in manual check: {e}")
                _slashing_params_cache[target_chain_name] = {}

        if target_chain_name not in _slashing_info_cache and chain_config_slashing.get("signing_infos_endpoint"):
            slashing_api_url = f"{chain_config_slashing['rest_api_url']}{chain_config_slashing['signing_infos_endpoint']}"
            try:
                slashing_response = requests.get(slashing_api_url, timeout=15)
                slashing_response.raise_for_status()
                slashing_data = slashing_response.json()
                _slashing_info_cache[target_chain_name] = {
                    item['address']: item for item in slashing_data.get('info', [])
                }
            except Exception as e:
                print(f"Error loading signing infos for chain {target_chain_name} in manual check: {e}")
                _slashing_info_cache[target_chain_name] = {}


    status_info = await get_validator_info(target_chain_name, target_validator_address)

    if status_info['success']:
        moniker = status_info['moniker']
        status = status_info['status']
        jailed = status_info['jailed']
        missed_blocks = status_info['missed_blocks']
        total_stake = status_info['total_stake']
        estimated_uptime = status_info['estimated_uptime']

        embed = discord.Embed(
            title=f"📊 Validator Status: {moniker} ({target_chain_name.upper()})",
            description=f"Current information for `{target_validator_address}`.",
            color=discord.Color.blue(),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        embed.add_field(name="🚨 Jailed?", value="🚨 Yes" if jailed else "✅ No", inline=True)
        embed.add_field(name="📊 Status", value=status, inline=True)
        embed.add_field(name="📉 Missed Blocks", value=f"{missed_blocks}" if missed_blocks != -1 else "N/A", inline=True)
        embed.add_field(name="🔋 Total Stake", value=total_stake, inline=True)
        embed.add_field(name="⏱️ Estimated Uptime", value=estimated_uptime, inline=True)

        embed.set_footer(text=f"Data from {bot.user.name}")
        await interaction.followup.send(embed=embed)
    else:
        error_embed = discord.Embed(
            title=f"❌ Error for {target_validator_address} ({target_chain_name.upper()})",
            description=f"Could not retrieve status. Reason: `{status_info['error']}`.",
            color=discord.Color.red(),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        error_embed.set_footer(text=f"Data from {bot.user.name}")
        await interaction.followup.send(embed=error_embed)

@bot.tree.command(name="set_notifications", description="Manages notifications for your validator (on/off).")
@discord.app_commands.describe(chain_name="Name of the chain", validator_address="Validator address", enabled_str="Enable notifications (on/off)")
async def set_notifications_slash(interaction: discord.Interaction, chain_name: str, validator_address: str, enabled_str: str):
    """
    Enables or disables notifications for a specific registered validator.
    """
    await interaction.response.defer(ephemeral=True)

    chain_name = chain_name.lower()
    if chain_name not in SUPPORTED_CHAINS:
        await interaction.followup.send(f"❌ Chain `{chain_name}` is not supported. Use `/list_chains` to see supported chains.", ephemeral=True)
        return

    # Convert user input to a boolean
    enabled = enabled_str.lower() in ('on', 'true', 'enable', 'aktif')

    if db_manager.set_validator_notifications(interaction.user.id, chain_name, validator_address, enabled):
        status_text = "enabled" if enabled else "disabled"
        await interaction.followup.send(f"✅ Notifications for validator `{validator_address}` on **{chain_name.upper()}** Chain have been set to `{status_text}`.", ephemeral=True)
    else:
        await interaction.followup.send(f"ℹ️ Validator `{validator_address}` on **{chain_name.upper()}** Chain was not found in your monitoring list.", ephemeral=True)

@bot.tree.command(name="notification_channel", description="Shows the channel where notifications for your validator are sent.")
@discord.app_commands.describe(
    chain_name="Name of the chain (e.g., empe, lumera)",
    validator_address="Validator address (e.g., empevaloper1...)"
)
async def notification_channel_slash(interaction: discord.Interaction, chain_name: str, validator_address: str):
    """
    Displays the Discord channel where notifications for a specific registered validator are sent.
    """
    await interaction.response.defer(ephemeral=True)

    chain_name = chain_name.lower()
    if chain_name not in SUPPORTED_CHAINS:
        await interaction.followup.send(f"❌ Chain `{chain_name}` is not supported. Use `/list_chains` to see supported chains.", ephemeral=True)
        return

    validator_data = db_manager.get_user_validator_details(interaction.user.id, chain_name, validator_address)

    if not validator_data:
        await interaction.followup.send(f"ℹ️ Validator `{validator_address}` on **{chain_name.upper()}** Chain was not found in your monitoring list.", ephemeral=True)
        return

    try:
        # channel_id is at index 3 in the tuple returned by get_user_validator_details
        # (chain_name, validator_address, user_id, channel_id, moniker, status, missed_blocks, notifications_enabled)
        channel_id_from_db = validator_data[3]
        
        target_channel = bot.get_channel(channel_id_from_db)

        if target_channel:
            await interaction.followup.send(
                f"✅ Notifications for `{validator_address}` on **{chain_name.upper()}** are configured to be sent to channel: {target_channel.mention} (ID: `{target_channel.id}`).",
                ephemeral=True
            )
        else:
            await interaction.followup.send(
                f"⚠️ Could not find the channel (ID: `{channel_id_from_db}`) where notifications for `{validator_address}` on **{chain_name.upper()}** are configured. It might have been deleted or the bot lacks access to it.",
                ephemeral=True
            )
    except IndexError:
        await interaction.followup.send("Error: Could not retrieve channel information. The database query might not return the channel ID.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"An unexpected error occurred: {e}", ephemeral=True)


@bot.tree.command(name="test_notification", description="Sends a sample notification to this channel.")
async def test_notification_slash(interaction: discord.Interaction):
    """
    Sends a sample notification to the current channel to demonstrate the bot's alert format.
    """
    await interaction.response.defer(ephemeral=True)

    embed = discord.Embed(
        title="🔔 Validator Alert: 🚨 TEST JAILED ALERT!",
        description="This is a sample notification to show the bot's alert format.",
        color=discord.Color.red(),
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    embed.add_field(name="🌐 Chain", value="EMPE (TEST)", inline=True)
    embed.add_field(name="🆔 Moniker", value="TestValidator", inline=True)
    embed.add_field(name="📜 Address", value="`empevaloper1testaddress...`", inline=False)
    embed.add_field(name="🚨 Jailed?", value="🚨 Yes", inline=True)
    embed.add_field(name="📊 Status", value="🚨 JAILED", inline=True)
    embed.add_field(name="📉 Missed Blocks", value="150", inline=True)
    embed.add_field(name="🔋 Total Stake", value="10,000.00 EMPE", inline=True)
    embed.add_field(name="⏱️ Estimated Uptime", value="98.50%", inline=True)

    embed.set_footer(text=f"Monitored by {bot.user.name}")

    mention_text = interaction.user.mention + " "

    try:
        await interaction.followup.send(content=mention_text, embed=embed, ephemeral=False)
    except discord.errors.Forbidden:
        await interaction.followup.send("Error: Bot does not have permission to send messages in this channel. Please check bot permissions.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"An unexpected error occurred while sending test notification: {e}", ephemeral=True)

if __name__ == '__main__':
    if not DISCORD_BOT_TOKEN:
        print("Error: DISCORD_BOT_TOKEN environment variable not set.")
        print("Please set it before running the bot, e.g., export DISCORD_BOT_TOKEN='YOUR_TOKEN' or create a .env file.")
        exit()
    try:
        db_manager.init_db()
        bot.run(DISCORD_BOT_TOKEN)
    except discord.errors.LoginFailure:
        print("Login Failed. Please ensure your Discord Bot Token is correct.")
    except Exception as e:
        print(f"An error occurred while running the bot: {e}")