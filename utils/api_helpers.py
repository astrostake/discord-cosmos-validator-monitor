# utils/api_helpers.py
# -*- coding: utf-8 -*-
"""API helper functions for fetching and processing chain data."""

import base64
import hashlib
import logging
from typing import Optional

import httpx
from bech32 import bech32_encode, convertbits

from utils.retry import api_get_with_retry

logger = logging.getLogger(__name__)


def create_progress_bar(percentage: float, length: int = 20) -> str:
    """Create a text-based progress bar from a percentage value."""
    if not 0 <= percentage <= 100:
        return f"[{' ' * length}]"

    filled_length = int(length * percentage // 100)
    bar = '█' * filled_length + '░' * (length - filled_length)
    return f"[{bar}]"


def pubkey_to_consensus_address(pubkey_b64: str, valcons_prefix: str) -> Optional[str]:
    """Convert a base64 public key to a bech32 consensus address."""
    try:
        pubkey_bytes = base64.b64decode(pubkey_b64)
        sha256_hash = hashlib.sha256(pubkey_bytes).digest()
        address_bytes = sha256_hash[:20]
        converted_bits = convertbits(address_bytes, 8, 5)
        if converted_bits is None:
            return None
        return bech32_encode(valcons_prefix, converted_bits)
    except Exception as e:
        logger.error(f"Error in pubkey_to_consensus_address for {pubkey_b64}: {e}")
        return None


async def get_validator_info(
    async_client: httpx.AsyncClient,
    chain_config,
    validator_address: str,
    slashing_info_cache: dict,
    slashing_params_cache: dict,
    max_retries: int = 3,
    backoff_base: float = 2.0,
) -> dict:
    """Fetch and process detailed validator information from the chain API.

    Uses retry logic for resilient API calls. Accepts ChainConfig dataclass
    with attribute-style access.

    Args:
        async_client: The httpx async client.
        chain_config: ChainConfig dataclass instance.
        validator_address: The validator's operator address.
        slashing_info_cache: Cached slashing signing info keyed by consensus address.
        slashing_params_cache: Cached slashing parameters.
        max_retries: Maximum retry attempts for API calls.
        backoff_base: Base for exponential backoff.

    Returns:
        Dict with validator info. Always has 'success' key.
    """
    rest_api_url = chain_config.rest_api_url
    valcons_prefix = chain_config.valcons_prefix
    token_symbol = chain_config.token_symbol
    token_decimals = chain_config.decimals
    missed_blocks_supported = chain_config.missed_blocks_supported

    try:
        staking_url = f"{rest_api_url}/cosmos/staking/v1beta1/validators/{validator_address}"
        staking_response = await api_get_with_retry(
            async_client, staking_url,
            max_retries=max_retries, backoff_base=backoff_base
        )
        validator_details = staking_response.json()['validator']

        moniker = validator_details['description']['moniker']
        jailed = validator_details['jailed']
        status = "JAILED" if jailed else {
            "BOND_STATUS_BONDED": "Bonded",
            "BOND_STATUS_UNBONDING": "Unbonding",
            "BOND_STATUS_UNBONDED": "Unbonded"
        }.get(validator_details['status'], validator_details['status'])

        raw_tokens_str = validator_details.get(
            'tokens', validator_details.get('delegator_shares', '0')
        )
        raw_tokens_float = float(raw_tokens_str)

        # Convert to human-readable format
        total_stake_human = f"{raw_tokens_float / (10**token_decimals):,.2f} {token_symbol}"

        missed_blocks = -1
        estimated_uptime = "N/A"
        estimated_uptime_percentage = 0.0

        if missed_blocks_supported and slashing_info_cache and slashing_params_cache:
            consensus_pubkey_b64 = validator_details['consensus_pubkey']['key']
            validator_cons_address = pubkey_to_consensus_address(
                consensus_pubkey_b64, valcons_prefix
            )

            if validator_cons_address:
                slashing_data = slashing_info_cache.get(validator_cons_address)
                if slashing_data:
                    missed_blocks = int(slashing_data.get('missed_blocks_counter', -1))
                    signed_blocks_window = int(
                        slashing_params_cache.get('signed_blocks_window', '0')
                    )
                    if signed_blocks_window > 0 and missed_blocks >= 0:
                        uptime_percentage = (
                            (signed_blocks_window - missed_blocks) / signed_blocks_window
                        ) * 100
                        estimated_uptime = f"{uptime_percentage:.2f}%"
                        estimated_uptime_percentage = uptime_percentage

        return {
            'success': True,
            'moniker': moniker,
            'status': status,
            'jailed': jailed,
            'missed_blocks': missed_blocks,
            'total_stake': total_stake_human,
            'raw_stake': raw_tokens_float,
            'estimated_uptime': estimated_uptime,
            'estimated_uptime_percentage': estimated_uptime_percentage
        }

    except httpx.RequestError as e:
        logger.error(f"API request failed for {validator_address}: {e}")
        return {'success': False, 'error': f"Network error: {e}"}
    except (KeyError, ValueError) as e:
        logger.error(f"Data structure mismatch for {validator_address}: {e}")
        return {'success': False, 'error': "Validator not found or data format is invalid."}
    except Exception as e:
        logger.error(f"Unexpected error in get_validator_info for {validator_address}: {e}")
        return {'success': False, 'error': "An unexpected error occurred."}


async def get_latest_block_height(
    async_client: httpx.AsyncClient, rest_api_url: str,
    max_retries: int = 2
) -> Optional[int]:
    """Fetch the latest block height for a chain."""
    try:
        response = await api_get_with_retry(
            async_client,
            f"{rest_api_url}/cosmos/base/tendermint/v1beta1/blocks/latest",
            max_retries=max_retries
        )
        data = response.json()
        return int(data['block']['header']['height'])
    except Exception as e:
        logger.error(f"Error fetching latest block height from {rest_api_url}: {e}")
        return None