import discord
from discord.ext import commands, tasks
import requests
import asyncio
import os
from dotenv import load_dotenv
import datetime
import db_manager # Pastikan db_manager.py adalah versi yang diperbarui (db_manager2.py)
from bech32 import bech32_encode, convertbits
import base64
import hashlib
from typing import Optional

load_dotenv()

# --- Configuration ---
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')

SUPPORTED_CHAINS = {
    "empe": {
        "rest_api_url": "https://lcd-testnet.empe.io",
        "valoper_prefix": "empevaloper",
        "valcons_prefix": "empevalcons",
        "token_symbol": "EMPE",
        "missed_blocks_supported": True,
        "signing_infos_endpoint": "/cosmos/slashing/v1beta1/signing_infos?pagination.limit=500",
        "slashing_params_endpoint": "/cosmos/slashing/v1beta1/params",
        "gov_proposals_endpoint": "/cosmos/gov/v1beta1/proposals", # Ditambahkan
        "current_plan_endpoint": "/cosmos/upgrade/v1beta1/current_plan", # Ditambahkan
    },
    "lumera": {
        "rest_api_url": "https://lcd.testnet.lumera.io",
        "valoper_prefix": "lumeravaloper",
        "valcons_prefix": "lumeravalcons",
        "token_symbol": "LUM",
        "missed_blocks_supported": True,
        "signing_infos_endpoint": "/cosmos/slashing/v1beta1/signing_infos?pagination.limit=300",
        "slashing_params_endpoint": "/cosmos/slashing/v1beta1/params",
        "gov_proposals_endpoint": "/cosmos/gov/v1beta1/proposals", # Ditambahkan
        "current_plan_endpoint": "/cosmos/upgrade/v1beta1/current_plan", # Ditambahkan
    },
}

MISSED_BLOCKS_THRESHOLD = 50
MONITOR_INTERVAL_SECONDS = 60
GOVERNANCE_CHECK_INTERVAL_SECONDS = 300 # Check governance every 5 minutes
UPGRADE_CHECK_INTERVAL_SECONDS = 3600 # Check upgrades every hour

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents)

_slashing_info_cache = {}
_slashing_params_cache = {}
_governance_proposals_cache = {}
_upgrade_plan_cache = {}

def pubkey_to_consensus_address(pubkey_b64, valcons_prefix):
    try:
        pubkey_bytes = base64.b64decode(pubkey_b64)

        raw_pubkey_bytes = None
        if len(pubkey_bytes) == 32:
            raw_pubkey_bytes = pubkey_bytes
        elif len(pubkey_bytes) == 36 and pubkey_bytes[0:4] == b'\x16$\xde\x64':
            raw_pubkey_bytes = pubkey_bytes[4:]
        elif len(pubkey_bytes) == 33 and (pubkey_bytes[0] == 0x02 or pubkey_bytes[0] == 0x03):
            raw_pubkey_bytes = pubkey_bytes
        else:
            print(f"Warning: Unexpected public key format/length for {pubkey_b64}: {len(pubkey_bytes)} bytes.")
            return None

        if raw_pubkey_bytes is None:
            return None

        address_bytes = hashlib.sha256(raw_pubkey_bytes).digest()[:20]

        five_bit_data = convertbits(address_bytes, 8, 5, True)

        return bech32_encode(valcons_prefix, five_bit_data)
    except Exception as e:
        print(f"Error in pubkey_to_consensus_address for {pubkey_b64}: {e}")
        return None

async def get_validator_info(chain_name, validator_address):
    chain_config = SUPPORTED_CHAINS.get(chain_name)
    if not chain_config:
        return {'success': False, 'error': f"Chain '{chain_name}' is not supported."}

    rest_api_url = chain_config["rest_api_url"]
    valcons_prefix = chain_config["valcons_prefix"]
    missed_blocks_supported = chain_config["missed_blocks_supported"]
    token_symbol = chain_config["token_symbol"]

    try:
        staking_url = f"{rest_api_url}/cosmos/staking/v1beta1/validators/{validator_address}"
        staking_response = requests.get(staking_url, timeout=10)
        staking_response.raise_for_status()
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
            display_status = status

        total_stake_raw = float(validator_details.get('delegator_shares', '0'))
        total_stake_human = f"{total_stake_raw / 1_000_000:,.2f} {token_symbol}"

        missed_blocks = -1
        estimated_uptime = "N/A"

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
                        estimated_uptime = "N/A (Window 0)"
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

# Fungsi baru untuk mendapatkan latest block height
async def get_latest_block_height(chain_name):
    chain_config = SUPPORTED_CHAINS.get(chain_name)
    if not chain_config:
        return None

    rest_api_url = chain_config["rest_api_url"]
    try:
        response = requests.get(f"{rest_api_url}/cosmos/base/tendermint/v1beta1/blocks/latest", timeout=5)
        response.raise_for_status()
        data = response.json()
        return int(data['block']['header']['height'])
    except requests.exceptions.RequestException as e:
        print(f"Error fetching latest block height for {chain_name}: {e}")
        return None
    except (KeyError, ValueError) as e:
        print(f"Unexpected block height data format for {chain_name}: {e}")
        return None


@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name} ({bot.user.id})')
    print('Bot is ready!')
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.streaming, name="AstroStake Validator Status", url="https://www.youtube.com/watch?v=jfKfPfyJRdk"))
    db_manager.init_db()
    monitor_validators.start()
    monitor_governance.start()
    monitor_upgrades.start()

@bot.command()
@commands.is_owner()
async def sync(ctx):
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
    global _slashing_info_cache, _slashing_params_cache

    for chain_name, chain_config in SUPPORTED_CHAINS.items():
        if chain_config["missed_blocks_supported"]:
            if chain_config["slashing_params_endpoint"]:
                params_api_url = f"{chain_config['rest_api_url']}{chain_config['slashing_params_endpoint']}"
                try:
                    params_response = requests.get(params_api_url, timeout=10)
                    params_response.raise_for_status()
                    params_data = params_response.json()
                    _slashing_params_cache[chain_name] = {
                        'signed_blocks_window': params_data['params']['signed_blocks_window']
                    }
                    print(f"Successfully loaded slashing params for chain: {chain_name}") # Uncommented
                except requests.exceptions.RequestException as e:
                    print(f"Error loading slashing params for chain {chain_name} from {params_api_url}: {e}")
                    _slashing_params_cache[chain_name] = {}
                except Exception as e:
                    print(f"Unexpected error processing slashing params for chain {chain_name}: {e}")
                    _slashing_params_cache[chain_name] = {}
            else:
                print(f"No slashing params endpoint defined for chain: {chain_name}")
                _slashing_params_cache[chain_name] = {}

            if chain_config["signing_infos_endpoint"]:
                slashing_api_url = f"{chain_config['rest_api_url']}{chain_config['signing_infos_endpoint']}"
                try:
                    slashing_response = requests.get(slashing_api_url, timeout=15)
                    slashing_response.raise_for_status()
                    slashing_data = slashing_response.json()

                    chain_slashing_info = {
                        item['address']: item for item in slashing_data.get('info', [])
                    }
                    _slashing_info_cache[chain_name] = chain_slashing_info
                    print(f"Successfully loaded signing infos for chain: {chain_name}") # Uncommented
                except requests.exceptions.RequestException as e:
                    print(f"Error loading signing infos for chain {chain_name} from {slashing_api_url}: {e}")
                    _slashing_info_cache[chain_name] = {}
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

    validators_to_monitor = db_manager.get_all_validators_to_monitor()

    if not validators_to_monitor:
        print("No validators registered to monitor. Skipping this loop.")
        return

    for val_data in validators_to_monitor:
        chain_name, validator_address, user_id, channel_id, old_moniker, old_status, old_missed_blocks = val_data

        channel = bot.get_channel(channel_id)
        if not channel:
            print(f"Warning: Channel with ID {channel_id} for user {user_id} not found. Skipping notifications.")
            continue

        print(f"Checking validator: {validator_address} on chain: {chain_name}") # Uncommented
        current_time = datetime.datetime.now().isoformat()

        status_info = await get_validator_info(chain_name, validator_address)

        # --- Notification Logic ---
        # Initialize flags and default values
        send_notification = False
        mention_required = False
        alert_title_prefix = "Validator Update:" # Default title prefix
        embed_color = discord.Color.blue() # Default color

        # Get current data from API response, fallback to old data if API call failed
        current_moniker = status_info.get('moniker', old_moniker)
        current_status = status_info.get('status', "UNKNOWN") # Default to UNKNOWN if API fails
        current_jailed = status_info.get('jailed', False)
        current_missed_blocks = status_info.get('missed_blocks', -1)

        # Main logic to decide if a notification is needed
        if status_info['success']:
            # Case 1: JAILED/Recovery
            if current_jailed and old_status != "🚨 JAILED":
                alert_title_prefix = "🚨 JAILED ALERT!"
                send_notification = True
                mention_required = True
                embed_color = discord.Color.red()
            elif not current_jailed and old_status == "🚨 JAILED":
                alert_title_prefix = "✅ RECOVERY ALERT!"
                send_notification = True
                mention_required = True
                embed_color = discord.Color.green()
            # Case 2: API Recovery (from previous API_ERROR to success)
            elif old_status == "API_ERROR": # Check if the previous state was an API error
                alert_title_prefix = "✅ API RECOVERED!"
                send_notification = True
                mention_required = True
                embed_color = discord.Color.green()
            # Case 3: General Status Change (not JAILED/Recovery and not API_ERROR transition)
            elif current_status != old_status:
                alert_title_prefix = "ℹ️ STATUS CHANGE!"
                send_notification = True
                embed_color = discord.Color.light_grey()
            
            # Case 4: Missed Blocks Logic (only if supported and valid data)
            if SUPPORTED_CHAINS[chain_name]["missed_blocks_supported"] and \
               current_missed_blocks != -1 and old_missed_blocks != -1: # Ensure valid missed block data
                missed_block_diff = current_missed_blocks - old_missed_blocks

                # If missed blocks increased and crossed threshold from below OR already above threshold with significant increase
                if missed_block_diff > 0: # Only care if missed blocks actually increased
                    if current_missed_blocks >= MISSED_BLOCKS_THRESHOLD and old_missed_blocks < MISSED_BLOCKS_THRESHOLD:
                        # Crosses threshold from below
                        if not send_notification: # Prioritize JAILED/Recovery if already flagged
                            alert_title_prefix = "⚠️ MISSED BLOCKS WARNING!"
                            embed_color = discord.Color.orange()
                        send_notification = True
                        mention_required = True # Always mention for threshold breach
                    elif current_missed_blocks >= MISSED_BLOCKS_THRESHOLD and old_missed_blocks >= MISSED_BLOCKS_THRESHOLD and missed_block_diff >= (MISSED_BLOCKS_THRESHOLD / 5):
                        # Already above threshold, but significant increase (e.g., jumps by 10 or more blocks if threshold is 50)
                        if not send_notification: # Prioritize JAILED/Recovery if already flagged
                            alert_title_prefix = "⚠️ MISSED BLOCKS INCREASE!"
                            embed_color = discord.Color.orange()
                        send_notification = True
                        mention_required = True # Always mention for significant increase above threshold
                    elif missed_block_diff > (MISSED_BLOCKS_THRESHOLD / 10) and not send_notification and current_status == old_status: # Smaller but noticeable increase, if no other alert and status didn't change
                        # This aims to catch smaller increases that aren't threshold breaches or major increases,
                        # and only if the primary status (JAILED/BONDED) hasn't already triggered an alert.
                        alert_title_prefix = "📊 Missed Blocks Update!"
                        embed_color = discord.Color.gold()
                        send_notification = True
                        # No mention for minor missed block updates unless it's a threshold breach.

            # Case 5: Moniker Change (only if you want to notify for this AND no other significant alert is pending)
            if current_moniker != old_moniker and old_moniker is not None and not send_notification:
                alert_title_prefix = "📝 Moniker Updated!"
                send_notification = True
                embed_color = discord.Color.greyple()
                # No mention for moniker changes

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
            current_moniker = old_moniker


        # Finally, send the notification if `send_notification` is True
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
            embed.add_field(name="🚨 Jailed?", value="🚨 Yes" if current_jailed else "No", inline=True)
            embed.add_field(name="📊 Status", value=current_status, inline=True)
            embed.add_field(name="📉 Missed Blocks", value=f"{current_missed_blocks}" if current_missed_blocks != -1 else "N/A", inline=True)
            embed.add_field(name="🔋 Total Stake", value=status_info.get('total_stake', "N/A"), inline=True) # Use get safely
            embed.add_field(name="⏱️ Estimated Uptime", value=status_info.get('estimated_uptime', "N/A"), inline=True) # Use get safely

            embed.set_footer(text=f"Monitored by {bot.user.name}")

            mention_text = target_user.mention + " " if target_user and mention_required else ""

            try:
                await channel.send(content=mention_text, embed=embed)
            except discord.errors.Forbidden:
                print(f"Error: Bot does not have permission to send messages in channel {channel.name} ({channel.id}) for user {user_id}. Please check bot permissions.")
            except Exception as e:
                print(f"Error sending message to Discord channel {channel.id}: {e}")

        # Always update the database with the latest status, even if no notification was sent for this loop iteration.
        # This is CRUCIAL for the next loop iteration to correctly detect *changes* in status.
        # It also updates the moniker if it has changed.
        db_manager.update_validator_status(chain_name, validator_address, current_status, current_missed_blocks, current_time, current_moniker)


@tasks.loop(seconds=GOVERNANCE_CHECK_INTERVAL_SECONDS)
async def monitor_governance():
    global _governance_proposals_cache
    print("Checking governance proposals...")

    # Get only chains that have governance notifications enabled in any channel
    chains_to_monitor_gov = db_manager.get_all_chain_notification_chains()

    if not chains_to_monitor_gov:
        # print("No chains configured for governance notifications. Skipping this loop.")
        return

    for chain_name in chains_to_monitor_gov:
        chain_config = SUPPORTED_CHAINS.get(chain_name)
        if not chain_config or "gov_proposals_endpoint" not in chain_config:
            print(f"Configuration for chain {chain_name} not found or no gov endpoint. Skipping.")
            continue

        gov_api_url = f"{chain_config['rest_api_url']}{chain_config['gov_proposals_endpoint']}"
        try:
            response = requests.get(gov_api_url, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            current_proposals = {prop['proposal_id']: prop for prop in data.get('proposals', [])}

            # Cek apakah cache untuk rantai ini sudah diinisialisasi
            if chain_name not in _governance_proposals_cache:
                # Jika belum diinisialisasi, isi cache tanpa mengirim notifikasi
                _governance_proposals_cache[chain_name] = current_proposals
                print(f"Initialized governance cache for {chain_name} with {len(current_proposals)} proposals. No notifications sent for initial load.")
                continue # Lanjut ke chain berikutnya atau selesai untuk iterasi ini

            # Jika cache sudah ada, lakukan perbandingan dan kirim notifikasi
            old_proposals = _governance_proposals_cache[chain_name]

            # Check for new proposals or proposals entering voting period
            for prop_id, prop_data in current_proposals.items():
                old_prop_data = old_proposals.get(prop_id)

                if not old_prop_data:
                    # New proposal detected
                    if prop_data['status'] == "PROPOSAL_STATUS_VOTING_PERIOD":
                        await send_governance_notification(chain_name, prop_data, "new_voting_period")
                    else:
                        await send_governance_notification(chain_name, prop_data, "new_proposal")
                else:
                    # Check for status changes (e.g., entering voting period or final status)
                    old_status = old_prop_data['status']
                    new_status = prop_data['status']

                    if new_status == "PROPOSAL_STATUS_VOTING_PERIOD" and old_status != "PROPOSAL_STATUS_VOTING_PERIOD":
                        await send_governance_notification(chain_name, prop_data, "new_voting_period")
                    elif new_status in ["PROPOSAL_STATUS_PASSED", "PROPOSAL_STATUS_REJECTED", "PROPOSAL_STATUS_FAILED"] and old_status not in ["PROPOSAL_STATUS_PASSED", "PROPOSAL_STATUS_REJECTED", "PROPOSAL_STATUS_FAILED"]:
                        # This handles a proposal moving to a final state from voting or any other non-final state
                        await send_governance_notification(chain_name, prop_data, "final_result")

            # Clean up old proposals that are no longer in the current API response
            for prop_id in list(old_proposals.keys()):
                if prop_id not in current_proposals:
                    # Proposal was in cache but not in current API response, likely finished
                    # If it wasn't already notified as 'final_result', you could add logic here
                    # For now, just remove from cache
                    del _governance_proposals_cache[chain_name][prop_id]

            # Update cache
            _governance_proposals_cache[chain_name] = current_proposals

        except requests.exceptions.RequestException as e:
            print(f"Error loading governance proposals for chain {chain_name} from {gov_api_url}: {e}")
        except Exception as e:
            print(f"Unexpected error processing governance proposals for chain {chain_name}: {e}")

async def send_governance_notification(chain_name, proposal_data, notification_type):
    proposal_id = proposal_data['proposal_id']
    proposal_status = proposal_data['status']

    embed_color = discord.Color.blue()
    alert_title_prefix = "📢 Governance Proposal:"
    description_suffix = ""

    # Inisialisasi dengan nilai default
    proposal_title = f"Proposal ID: {proposal_id}"
    proposal_description = "No specific title or description provided (non-text proposal)."

    content_type = proposal_data.get('content', {}).get('@type')

    if content_type == "/cosmos.gov.v1beta1.TextProposal":
        # Ini adalah tipe standar yang memiliki title dan description
        proposal_title = proposal_data['content'].get('title', 'Untitled Text Proposal')
        proposal_description = proposal_data['content'].get('description', 'No description.')
    elif content_type == "/cosmos.slashing.v1beta1.MsgUpdateParams":
        proposal_title = "Slashing Params Update Proposal"
        params = proposal_data.get('content', {}).get('params', {})
        proposal_description = (
            "This proposal aims to update slashing parameters.\n\n"
            f"**Signed Blocks Window:** {params.get('signed_blocks_window', 'N/A')}\n"
            f"**Min Signed Per Window:** {params.get('min_signed_per_window', 'N/A')}\n"
            f"**Downtime Jail Duration:** {params.get('downtime_jail_duration', 'N/A')}"
        )
    elif content_type == "/cosmos.upgrade.v1beta1.MsgSoftwareUpgrade":
        proposal_title = "Software Upgrade Proposal"
        plan = proposal_data.get('content', {}).get('plan', {})
        proposal_description = (
            f"This proposal suggests a software upgrade to **{plan.get('name', 'N/A')}**.\n\n"
            f"**Target Height:** {plan.get('height', 'N/A')}\n"
            f"**Info:** {plan.get('info', 'No additional info.')}"
        )
    # Tambahkan lebih banyak elif untuk tipe proposal lain jika diperlukan
    else:
        # Untuk tipe yang tidak dikenali, gunakan get() untuk title/description umum
        # Ini penting jika ada struktur "content" lain yang mungkin memiliki title/description
        if 'content' in proposal_data:
            proposal_title = proposal_data['content'].get('title', f"Generic Proposal ({content_type})")
            proposal_description = proposal_data['content'].get('description', 'Content structure not fully recognized.')


    if notification_type == "new_proposal":
        alert_title_prefix = "✨ New Governance Proposal!"
        embed_color = discord.Color.gold()
        description_suffix = "\n\nThis proposal has been submitted. Keep an eye out for when it enters the voting period!"
    elif notification_type == "new_voting_period":
        alert_title_prefix = "🗳️ Proposal Entered Voting Period!"
        embed_color = discord.Color.orange()
        # Optional: Add voting end time if available in proposal_data
        voting_end_time_str = proposal_data.get('voting_end_time')
        if voting_end_time_str:
            try:
                voting_end_dt = datetime.datetime.fromisoformat(voting_end_time_str.replace('Z', '+00:00'))
                description_suffix = f"\n\n**Voting ends:** <t:{int(voting_end_dt.timestamp())}:R>\n\n**It's time to cast your vote!**"
            except ValueError:
                description_suffix = "\n\n**It's time to cast your vote!**"
        else:
            description_suffix = "\n\n**It's time to cast your vote!**"
    elif notification_type == "final_result":
        if proposal_status == "PROPOSAL_STATUS_PASSED":
            alert_title_prefix = "✅ Proposal PASSED!"
            embed_color = discord.Color.green()
        elif proposal_status == "PROPOSAL_STATUS_REJECTED":
            alert_title_prefix = "❌ Proposal REJECTED!"
            embed_color = discord.Color.red()
        elif proposal_status == "PROPOSAL_STATUS_FAILED":
            alert_title_prefix = "🗑️ Proposal FAILED!"
            embed_color = discord.Color.dark_red()
        else:
            alert_title_prefix = "ℹ️ Proposal Concluded!"
            embed_color = discord.Color.light_grey()
        
        # Fetch tally for final results
        tally_result_text = "N/A"
        try:
            tally_url = f"{SUPPORTED_CHAINS[chain_name]['rest_api_url']}/cosmos/gov/v1beta1/proposals/{proposal_id}/tally"
            tally_response = requests.get(tally_url, timeout=5)
            tally_response.raise_for_status()
            tally_data = tally_response.json()
            tally = tally_data.get('tally', {})
            yes = float(tally.get('yes', '0'))
            no = float(tally.get('no', '0'))
            no_with_veto = float(tally.get('no_with_veto', '0'))
            abstain = float(tally.get('abstain', '0'))
            total_votes = yes + no + no_with_veto + abstain
            
            if total_votes > 0:
                tally_result_text = (
                    f"Yes: {yes/total_votes:.2%}\n"
                    f"No: {no/total_votes:.2%}\n"
                    f"NoWithVeto: {no_with_veto/total_votes:.2%}\n"
                    f"Abstain: {abstain/total_votes:.2%}"
                )
            else:
                tally_result_text = "No votes recorded or error in tally."
        except Exception as e:
            print(f"Error fetching tally for proposal {proposal_id}: {e}")
            tally_result_text = "Could not fetch tally results."

        description_suffix = f"\n\n**Final Status:** {proposal_status.replace('PROPOSAL_STATUS_', '').replace('_', ' ').title()}\n**Tally:**\n{tally_result_text}"


    embed = discord.Embed(
        title=f"{alert_title_prefix} (ID: {proposal_id})",
        description=f"**{proposal_title}**\n\n{proposal_description}{description_suffix}",
        color=embed_color,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    embed.add_field(name="🌐 Chain", value=chain_name.upper(), inline=True)
    embed.add_field(name="Current Status", value=proposal_status.replace('PROPOSAL_STATUS_', '').replace('_', ' ').title(), inline=True)

    embed.set_footer(text=f"Monitored by {bot.user.name}")

    # Get notification preferences for this chain
    notification_configs = db_manager.get_chain_notification_preferences(chain_name)

    for config in notification_configs:
        if config['notify_gov_enabled']: # Only send if governance notifications are enabled for this channel
            channel = bot.get_channel(config['channel_id'])
            if channel:
                mention_text = ""
                if config['mention_type'] == 'here':
                    mention_text = "@here"
                elif config['mention_type'] == 'everyone':
                    mention_text = "@everyone" # Be cautious with @everyone

                try:
                    await channel.send(content=mention_text, embed=embed)
                except discord.errors.Forbidden:
                    print(f"Error: Bot does not have permission to send messages in channel {channel.name} ({channel.id}).")
                except Exception as e:
                    print(f"Error sending governance notification to Discord channel {channel.id}: {e}")
            else:
                print(f"Warning: Configured channel with ID {config['channel_id']} not found for governance alert on {chain_name}.")


@tasks.loop(seconds=UPGRADE_CHECK_INTERVAL_SECONDS)
async def monitor_upgrades():
    global _upgrade_plan_cache
    print("Checking for chain upgrades...")

    # Get only chains that have upgrade notifications enabled in any channel
    chains_to_monitor_upgrade = db_manager.get_all_chain_notification_chains()

    if not chains_to_monitor_upgrade:
        # print("No chains configured for upgrade notifications. Skipping this loop.")
        return

    for chain_name in chains_to_monitor_upgrade:
        chain_config = SUPPORTED_CHAINS.get(chain_name)
        if not chain_config or "current_plan_endpoint" not in chain_config:
            print(f"Configuration for chain {chain_name} not found or no upgrade endpoint. Skipping.")
            continue
            
        upgrade_api_url = f"{chain_config['rest_api_url']}{chain_config['current_plan_endpoint']}"
        try:
            response = requests.get(upgrade_api_url, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            current_plan = data.get('plan') # This will be None if no plan is scheduled

            # Initialize cache for the chain if it doesn't exist
            if chain_name not in _upgrade_plan_cache:
                _upgrade_plan_cache[chain_name] = None # No plan initially

            old_plan = _upgrade_plan_cache[chain_name]
            
            if current_plan and (old_plan is None or current_plan['name'] != old_plan['name']):
                # New upgrade plan or a change in the existing plan
                await send_upgrade_notification(chain_name, current_plan, "new_plan")
            elif old_plan and current_plan is None:
                # An upgrade plan was present but now it's gone (implies upgrade happened or was cancelled)
                await send_upgrade_notification(chain_name, old_plan, "completed_or_cancelled")

            _upgrade_plan_cache[chain_name] = current_plan # Update cache

        except requests.exceptions.RequestException as e:
            if e.response and e.response.status_code == 404: # Common for no current plan
                # print(f"No current upgrade plan for {chain_name} (404 Not Found).")
                # If a plan was previously cached but now it's 404, send a completion/cancellation notice
                if _upgrade_plan_cache.get(chain_name) is not None:
                     await send_upgrade_notification(chain_name, _upgrade_plan_cache[chain_name], "completed_or_cancelled")
                _upgrade_plan_cache[chain_name] = None
            else:
                print(f"Error loading upgrade plan for chain {chain_name} from {upgrade_api_url}: {e}")
        except Exception as e:
            print(f"Unexpected error processing upgrade plan for chain {chain_name}: {e}")

async def send_upgrade_notification(chain_name, upgrade_plan, notification_type="new_plan"):
    embed_color = discord.Color.purple()
    alert_title_prefix = "🚀 Chain Upgrade Alert!"
    description_text = ""

    if notification_type == "new_plan":
        upgrade_name = upgrade_plan.get('name', 'N/A')
        upgrade_height = int(upgrade_plan.get('height', 0)) # Pastikan ini int
        info = upgrade_plan.get('info', 'No additional info provided.')

        # Ambil current height
        current_height = await get_latest_block_height(chain_name)

        remaining_blocks = "N/A"

        if current_height is not None and upgrade_height > 0:
            remaining_blocks_calc = upgrade_height - current_height
            if remaining_blocks_calc > 0:
                remaining_blocks = f"{remaining_blocks_calc:,}"
            else:
                remaining_blocks = "0 (Upgrade height reached or passed)"


        description_text = (
            f"A new chain upgrade has been planned for **{chain_name.upper()}**!\n\n"
            f"**Upgrade Name:** `{upgrade_name}`\n"
            f"**Target Height:** `{upgrade_height:,}`\n" # Format dengan koma
            f"**Current Height:** `{current_height:,}`\n\n" if current_height is not None else "" # Tambahkan current height
            f"**Blocks Remaining:** `{remaining_blocks}`\n\n" # Hanya tampilkan sisa blok
            f"**Info:** {info}"
        )
    elif notification_type == "completed_or_cancelled":
        upgrade_name = upgrade_plan.get('name', 'N/A') # Use the old plan's name
        alert_title_prefix = "✅ Chain Upgrade Concluded (or cancelled)!"
        embed_color = discord.Color.green()
        description_text = (
            f"The previously announced upgrade (`{upgrade_name}`) for **{chain_name.upper()}** "
            "appears to have been completed or cancelled."
        )

    embed = discord.Embed(
        title=alert_title_prefix,
        description=description_text,
        color=embed_color,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    embed.add_field(name="🌐 Chain", value=chain_name.upper(), inline=True)
    embed.set_footer(text=f"Monitored by {bot.user.name}")

    # Get notification preferences for this chain
    notification_configs = db_manager.get_chain_notification_preferences(chain_name)

    for config in notification_configs:
        if config['notify_upgrade_enabled']: # Only send if upgrade notifications are enabled for this channel
            channel = bot.get_channel(config['channel_id'])
            if channel:
                mention_text = ""
                if config['mention_type'] == 'here':
                    mention_text = "@here"
                elif config['mention_type'] == 'everyone':
                    mention_text = "@everyone" # Be cautious with @everyone

                try:
                    await channel.send(content=mention_text, embed=embed)
                except discord.errors.Forbidden:
                    print(f"Error: Bot does not have permission to send messages in channel {channel.name} ({channel.id}).")
                except Exception as e:
                    print(f"Error sending upgrade notification to Discord channel {channel.id}: {e}")
            else:
                print(f"Warning: Configured channel with ID {config['channel_id']} not found for upgrade alert on {chain_name}.")


@bot.tree.command(name="help", description="Provides info about what this bot does and how to use it.")
async def help_slash(interaction: discord.Interaction):
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
            "  - Validator **JAILED** or recovering, status changes, missed blocks.\n" # Ringkasan
            "  - **Governance Proposals**: New, voting period, final results.\n" # Ringkasan
            "  - **Chain Upgrades**: New plan announcements, and status.\n" # Ringkasan
            "📊 **Detailed Status**: Get current validator status, stake, uptime.\n" # Ringkasan
            "👤 **Personalized Monitoring**: Register your own validators." # Ringkasan
        ),
        inline=False
    )
    embed.add_field(
        name="How to Use Commands",
        value=(
            "Type `/` to see commands and arguments.\n" # Ringkasan
            "- **`/help`**: Bot info.\n" # Ringkasan
            "- **`/list_chains`**: Supported chains.\n" # Ringkasan
            "- **`/register <chain> <addr>`**: Add validator.\n" # Ringkasan
            "- **`/unregister <chain> <addr>`**: Remove validator.\n" # Ringkasan
            "- **`/myvalidators`**: Your registered validators.\n" # Ringkasan
            "- **`/vals <chain>`**: Your validators on a specific chain.\n" # Ringkasan
            "- **`/validator_status <chain> <addr>`**: Instant validator status.\n" # Ringkasan
            "- **`/set_notifications <chain> <addr> <on/off>`**: Manage validator alerts.\n" # Ringkasan
            "- **`/notification_channel <chain> <addr>`**: Show validator notification channel.\n" # Ringkasan
            "- **`/set_chain_notifications <chain> <gov_on/off> <up_on/off> <mention_here>`**: Configure governance & upgrade alerts for THIS channel.\n" # Ringkasan
            "- **`/test_notification`**: See a sample alert." # Ringkasan
        ),
        inline=False
    )
    embed.set_footer(text=f"Bot developed by AstroStake.xyz")
    await interaction.response.send_message(embed=embed, ephemeral=False)

@bot.tree.command(name="list_chains", description="Displays a list of supported chains.")
async def list_chains_slash(interaction: discord.Interaction):
    response = "**Supported Chains:**\n"
    for chain_id, config in SUPPORTED_CHAINS.items():
        response += f"- `{chain_id.upper()}` (API: {config['rest_api_url']})\n"
        response += f"  Validator Prefix: `{config['valoper_prefix']}`\n"
        response += f"  Token Symbol: `{config['token_symbol']}`\n"
        response += f"  Missed Blocks Monitoring: {'✅ Supported' if config['missed_blocks_supported'] else '❌ Not Supported'}\n"
        response += f"  Governance Monitoring: {'✅ Supported' if 'gov_proposals_endpoint' in config else '❌ Not Supported'}\n" # Ditambahkan
        response += f"  Upgrade Monitoring: {'✅ Supported' if 'current_plan_endpoint' in config else '❌ Not Supported'}\n" # Ditambahkan
    await interaction.response.send_message(response, ephemeral=True)

@bot.tree.command(name="register", description="Registers a validator for monitoring on a specific chain.")
@discord.app_commands.describe(chain_name="Name of the chain (e.g., empe, lumera)", validator_address="Validator address (e.g., empevaloper1...)")
async def register_slash(interaction: discord.Interaction, chain_name: str, validator_address: str):
    await interaction.response.defer(ephemeral=False)

    chain_name = chain_name.lower()
    if chain_name not in SUPPORTED_CHAINS:
        await interaction.followup.send(f"❌ Chain `{chain_name}` is not supported. Use `/list_chains` to see supported chains.", ephemeral=True)
        return

    chain_config = SUPPORTED_CHAINS[chain_name]
    valoper_prefix = chain_config["valoper_prefix"]

    if not validator_address.startswith(valoper_prefix):
        await interaction.followup.send(f"Invalid validator address format for `{chain_name.upper()}` chain. Please use `{valoper_prefix}...`.", ephemeral=True)
        return

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
    await interaction.response.defer(ephemeral=True)

    validators = db_manager.get_user_validators(interaction.user.id)
    if not validators:
        await interaction.followup.send("You have not registered any validators yet.", ephemeral=True)
        return

    # Fetch real-time status for each validator
    embeds = []
    for chain_name, val_addr, _, _, _ in validators: # moniker, status, missed_blocks from DB are not used directly
        # Ensure slashing caches are updated if needed for the current chain
        if SUPPORTED_CHAINS.get(chain_name, {}).get("missed_blocks_supported", False):
            chain_config_slashing = SUPPORTED_CHAINS[chain_name]
            if chain_name not in _slashing_params_cache and chain_config_slashing["slashing_params_endpoint"]:
                params_api_url = f"{chain_config_slashing['rest_api_url']}{chain_config_slashing['slashing_params_endpoint']}"
                try:
                    params_response = requests.get(params_api_url, timeout=10)
                    params_response.raise_for_status()
                    params_data = params_response.json()
                    _slashing_params_cache[chain_name] = {'signed_blocks_window': params_data['params']['signed_blocks_window']}
                except Exception as e:
                    print(f"Error loading slashing params for chain {chain_name} in /myvalidators: {e}")
                    _slashing_params_cache[chain_name] = {}
            if chain_name not in _slashing_info_cache and chain_config_slashing["signing_infos_endpoint"]:
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
            embed.add_field(name="🚨 Jailed?", value="🚨 Yes" if jailed else "No", inline=True)
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
        # Discord allows sending up to 10 embeds at once
        for i in range(0, len(embeds), 10):
            await interaction.followup.send(embeds=embeds[i:i+10])
    else:
        await interaction.followup.send("You have not registered any validators yet.", ephemeral=True)

@bot.tree.command(name="vals", description="Displays validators you registered on a specific chain.")
@discord.app_commands.describe(chain_name="Name of the chain (e.g., empe, lumera)")
async def vals_slash(interaction: discord.Interaction, chain_name: str):
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

    # Fetch real-time status for each validator
    embeds = []
    for chain, val_addr, _, _, _ in validators: # moniker, status, missed_blocks from DB are not used directly
        # Ensure slashing caches are updated if needed for the current chain
        if SUPPORTED_CHAINS.get(chain, {}).get("missed_blocks_supported", False):
            chain_config_slashing = SUPPORTED_CHAINS[chain]
            if chain not in _slashing_params_cache and chain_config_slashing["slashing_params_endpoint"]:
                params_api_url = f"{chain_config_slashing['rest_api_url']}{chain_config_slashing['slashing_params_endpoint']}"
                try:
                    params_response = requests.get(params_api_url, timeout=10)
                    params_response.raise_for_status()
                    params_data = params_response.json()
                    _slashing_params_cache[chain] = {'signed_blocks_window': params_data['params']['signed_blocks_window']}
                except Exception as e:
                    print(f"Error loading slashing params for chain {chain} in /vals: {e}")
                    _slashing_params_cache[chain] = {}
            if chain not in _slashing_info_cache and chain_config_slashing["signing_infos_endpoint"]:
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
            embed.add_field(name="🚨 Jailed?", value="🚨 Yes" if jailed else "No", inline=True)
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
        # Discord allows sending up to 10 embeds at once
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

    # Fetch slashing info and params if supported and not cached
    if SUPPORTED_CHAINS.get(target_chain_name, {}).get("missed_blocks_supported", False):
        chain_config_slashing = SUPPORTED_CHAINS[target_chain_name]

        if target_chain_name not in _slashing_params_cache and chain_config_slashing["slashing_params_endpoint"]:
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

        if target_chain_name not in _slashing_info_cache and chain_config_slashing["signing_infos_endpoint"]:
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
        embed.add_field(name="🚨 Jailed?", value="🚨 Yes" if jailed else "No", inline=True)
        embed.add_field(name="📊 Status", value=status, inline=True)
        embed.add_field(name="📉 Missed Blocks", value=f"{missed_blocks}" if missed_blocks != -1 else "N/A", inline=True)
        embed.add_field(name="🔋 Total Stake", value=total_stake, inline=True)
        embed.add_field(name="⏱️ Estimated Uptime", value=estimated_uptime, inline=True)

        embed.set_footer(text=f"Monitored by {bot.user.name}")
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
    await interaction.response.defer(ephemeral=True)

    chain_name = chain_name.lower()
    if chain_name not in SUPPORTED_CHAINS:
        await interaction.followup.send(f"❌ Chain `{chain_name}` is not supported. Use `/list_chains` to see supported chains.", ephemeral=True)
        return

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
        channel_id_from_db = validator_data[3]
        
        target_channel = bot.get_channel(channel_id_from_db)

        if target_channel:
            await interaction.followup.send(
                f"✅ Notifications for `{validator_address}` on **{chain_name.upper()}** are configured to be sent to channel: {target_channel.mention} (ID: `{target_channel.id}`).",
                ephemeral=True
            )
        else:
            await interaction.followup.send(
                f"⚠️ Could not find the channel (ID: `{channel_id_from_db}`) where notifications for `{validator_address}` on **{chain_name.upper()}** are configured. It might have been deleted or bot lacks access.",
                ephemeral=True
            )
    except IndexError:
        await interaction.followup.send("Error: Could not retrieve channel information. The database query might not return the channel ID.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"An unexpected error occurred: {e}", ephemeral=True)


@bot.tree.command(name="set_chain_notifications", description="Configure governance and upgrade notifications for THIS channel.")
@discord.app_commands.describe(
    chain_name="Name of the chain (e.g., empe, lumera)",
    enable_gov="Enable/Disable governance notifications for this channel (on/off)",
    enable_upgrade="Enable/Disable upgrade notifications for this channel (on/off)",
    mention_here="Mention @here with notifications (true/false, default: false)"
)
async def set_chain_notifications_slash(
    interaction: discord.Interaction,
    chain_name: str,
    enable_gov: str,
    enable_upgrade: str,
    mention_here: bool = False
):
    await interaction.response.defer(ephemeral=True)

    chain_name = chain_name.lower()
    if chain_name not in SUPPORTED_CHAINS:
        await interaction.followup.send(f"❌ Chain `{chain_name}` is not supported. Use `/list_chains` to see supported chains.", ephemeral=True)
        return

    gov_enabled = enable_gov.lower() in ('on', 'true', 'enable', 'aktif')
    upgrade_enabled = enable_upgrade.lower() in ('on', 'true', 'enable', 'aktif')
    
    mention_type = 'here' if mention_here else None # You can extend this for 'everyone' if needed

    success = db_manager.set_chain_notification_preference(
        interaction.channel_id,
        chain_name,
        gov_enabled,
        upgrade_enabled,
        mention_type
    )

    if success:
        gov_status = "enabled" if gov_enabled else "disabled"
        upgrade_status = "enabled" if upgrade_enabled else "disabled"
        mention_text = " with `@here` mention" if mention_here else ""
        await interaction.followup.send(
            f"✅ Notifications for **{chain_name.upper()}** in this channel set:\n"
            f"- Governance: `{gov_status}`\n"
            f"- Upgrade: `{upgrade_status}`{mention_text}",
            ephemeral=True
        )
    else:
        await interaction.followup.send(f"❌ Failed to set notification preferences. An error occurred.", ephemeral=True)

@bot.tree.command(name="test_notification", description="Sends a sample notification to this channel.")
async def test_notification_slash(interaction: discord.Interaction):
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
        # Pastikan db_manager yang digunakan adalah versi yang sudah ada chain_notification_settings
        db_manager.init_db()
        bot.run(DISCORD_BOT_TOKEN)
    except discord.errors.LoginFailure:
        print("Login Failed. Please ensure your Discord Bot Token is correct.")
    except Exception as e:
        print(f"An error occurred while running the bot: {e}")
