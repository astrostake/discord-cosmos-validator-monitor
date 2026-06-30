# utils/governance_helpers.py
# -*- coding: utf-8 -*-
"""Shared governance-related helper functions.

Centralizes proposal title extraction, tally fetching, and mention string
building to eliminate code duplication across cogs.
"""

import base64
import json
import logging
from typing import Optional

import httpx

from utils.retry import api_get_with_retry

logger = logging.getLogger(__name__)


def extract_proposal_title(prop_data: dict) -> str:
    """Extract proposal title from various Cosmos gov API formats.

    Tries in order: direct 'title' field (gov v1), nested 'content.title'
    (gov v1beta1), and base64-encoded 'metadata' field.
    """
    prop_id = prop_data.get('id') or prop_data.get('proposal_id', 'N/A')

    # Try direct title field (gov v1)
    title = prop_data.get('title')

    # Try nested content field (gov v1beta1)
    if not title:
        title = prop_data.get('content', {}).get('title')

    # Try base64-encoded metadata
    if not title and 'metadata' in prop_data:
        try:
            metadata_json = json.loads(base64.b64decode(prop_data['metadata']))
            title = metadata_json.get('title')
        except Exception:
            pass

    return title or f"Proposal #{prop_id}"


async def fetch_tally(
    client: httpx.AsyncClient,
    tally_url: str,
    max_retries: int = 2,
) -> dict:
    """Fetch and parse tally results for a governance proposal.

    Returns:
        Dict with 'yes', 'no', 'veto', 'abstain', 'total' keys as ints.
        Returns empty dict on failure.
    """
    try:
        response = await api_get_with_retry(client, tally_url, max_retries=max_retries)
        tally_data = response.json().get('tally', {})

        yes = int(tally_data.get('yes_count', tally_data.get('yes', '0')))
        no = int(tally_data.get('no_count', tally_data.get('no', '0')))
        veto = int(tally_data.get('no_with_veto_count', tally_data.get('no_with_veto', '0')))
        abstain = int(tally_data.get('abstain_count', tally_data.get('abstain', '0')))
        total = yes + no + veto + abstain

        return {'yes': yes, 'no': no, 'veto': veto, 'abstain': abstain, 'total': total}
    except Exception as e:
        logger.error(f"Failed to fetch tally from {tally_url}: {e}")
        return {}


def format_tally_inline(tally: dict) -> str:
    """Format tally data as a compact single-line summary."""
    if not tally or tally.get('total', 0) == 0:
        return "No votes recorded yet."

    total = tally['total']
    return (
        f"Yes: {tally['yes']/total:.2%}, "
        f"No: {tally['no']/total:.2%}, "
        f"Veto: {tally['veto']/total:.2%}"
    )


def format_tally_block(tally: dict) -> str:
    """Format tally data as a detailed code block."""
    if not tally or tally.get('total', 0) == 0:
        return "No votes were recorded."

    total = tally['total']
    return (
        f"```\n"
        f"Yes:         {tally['yes']/total:8.2%} ({tally['yes']:,})\n"
        f"No:          {tally['no']/total:8.2%} ({tally['no']:,})\n"
        f"No w/ Veto:  {tally['veto']/total:8.2%} ({tally['veto']:,})\n"
        f"Abstain:     {tally['abstain']/total:8.2%} ({tally['abstain']:,})\n"
        f"```"
    )


def get_mention_string(mention_type: Optional[str]) -> Optional[str]:
    """Convert a mention_type setting to a Discord mention string."""
    if not mention_type or mention_type == 'none':
        return None
    if mention_type == 'here':
        return '@here'
    if mention_type == 'everyone':
        return '@everyone'
    return mention_type
