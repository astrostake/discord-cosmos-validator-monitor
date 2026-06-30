# settings.py
# -*- coding: utf-8 -*-
"""Centralized settings management for the bot."""

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass
class ChainConfig:
    """Typed configuration for a single blockchain."""
    name: str
    rest_api_url: str
    valoper_prefix: str
    valcons_prefix: str
    token_symbol: str
    base_denom: str
    decimals: int = 6
    missed_blocks_supported: bool = True
    signing_infos_endpoint: str = "/cosmos/slashing/v1beta1/signing_infos?pagination.limit=500"
    slashing_params_endpoint: str = "/cosmos/slashing/v1beta1/params"
    gov_proposals_endpoint: str = "/cosmos/gov/v1/proposals"
    current_plan_endpoint: str = "/cosmos/upgrade/v1beta1/current_plan"

    @classmethod
    def from_dict(cls, name: str, data: dict) -> "ChainConfig":
        """Create a ChainConfig from a dictionary, ignoring unknown keys."""
        known_fields = {f.name for f in cls.__dataclass_fields__.values() if f.name != 'name'}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(name=name, **filtered)

    def get_gov_version(self) -> str:
        """Determine gov API version from endpoint."""
        return "v1" if "/gov/v1/" in self.gov_proposals_endpoint else "v1beta1"

    def get_tally_endpoint(self, prop_id: str) -> str:
        """Build full tally URL for a proposal."""
        version = self.get_gov_version()
        return f"{self.rest_api_url}/cosmos/gov/{version}/proposals/{prop_id}/tally"


@dataclass
class BotSettings:
    """Bot-wide configuration settings with runtime update support."""
    monitor_interval_seconds: int = 60
    governance_check_interval_seconds: int = 300
    upgrade_check_interval_seconds: int = 3600
    missed_blocks_threshold: int = 50
    min_stake_change_amount: float = 1000.0
    log_level: str = "INFO"
    log_file: Optional[str] = "bot.log"
    db_path: str = "validator_monitor.db"
    api_timeout: float = 20.0
    api_max_retries: int = 3
    api_retry_backoff: float = 2.0
    admin_user_ids: List[int] = field(default_factory=list)

    # Keys that are safe to modify at runtime via Discord commands
    RUNTIME_MUTABLE_KEYS = {
        'monitor_interval_seconds', 'governance_check_interval_seconds',
        'upgrade_check_interval_seconds', 'missed_blocks_threshold',
        'min_stake_change_amount', 'api_timeout', 'api_max_retries',
        'api_retry_backoff', 'log_level',
    }

    @classmethod
    def from_dict(cls, data: dict) -> "BotSettings":
        """Create BotSettings from a dictionary, ignoring unknown keys."""
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)

    def update(self, key: str, value: Any) -> bool:
        """Update a single setting with type coercion. Returns True if successful."""
        if not hasattr(self, key) or key not in self.RUNTIME_MUTABLE_KEYS:
            return False

        current = getattr(self, key)
        try:
            if isinstance(current, bool):
                value = str(value).lower() in ('true', '1', 'yes')
            elif isinstance(current, int):
                value = int(value)
            elif isinstance(current, float):
                value = float(value)
            elif isinstance(current, str):
                value = str(value)
        except (ValueError, TypeError):
            return False

        setattr(self, key, value)
        return True


def load_config(config_file: str = 'config.yaml') -> tuple:
    """Load bot settings and chain configs from YAML file.

    Supports both legacy flat format and new structured format with 'bot:' section.

    Returns:
        Tuple of (BotSettings, dict[str, ChainConfig])
    """
    try:
        with open(config_file, 'r') as f:
            raw = yaml.safe_load(f)
    except FileNotFoundError:
        logger.critical(f"Configuration file '{config_file}' not found. Please copy 'config.example.yaml' to '{config_file}' and configure it.")
        raise SystemExit(1)
    except yaml.YAMLError as e:
        logger.critical(f"Error parsing '{config_file}': {e}")
        raise SystemExit(1)

    if not isinstance(raw, dict):
        logger.critical(f"Invalid config format in '{config_file}'.")
        raise SystemExit(1)

    # Extract bot settings section (if present)
    bot_data = raw.pop('bot', {})
    settings = BotSettings.from_dict(bot_data) if bot_data else BotSettings()

    # Override with environment variables
    env_overrides = {
        'MONITOR_INTERVAL': 'monitor_interval_seconds',
        'GOV_CHECK_INTERVAL': 'governance_check_interval_seconds',
        'UPGRADE_CHECK_INTERVAL': 'upgrade_check_interval_seconds',
        'MISSED_BLOCKS_THRESHOLD': 'missed_blocks_threshold',
        'LOG_LEVEL': 'log_level',
        'DB_PATH': 'db_path',
    }
    for env_key, setting_key in env_overrides.items():
        env_val = os.getenv(env_key)
        if env_val:
            settings.update(setting_key, env_val)

    # Extract chain configs — support both 'chains:' key and flat format
    chains_data = raw.pop('chains', raw)

    chains: Dict[str, ChainConfig] = {}
    for chain_name, chain_data in chains_data.items():
        if isinstance(chain_data, dict):
            try:
                chains[chain_name] = ChainConfig.from_dict(chain_name, chain_data)
            except Exception as e:
                logger.warning(f"Failed to load chain config '{chain_name}': {e}")

    if not chains:
        logger.warning("No chain configurations loaded from config file!")

    logger.info(f"Loaded settings and {len(chains)} chain configs from {config_file}.")
    return settings, chains
