"""Configuration load/save utilities for Bluetooth Screen Lock.

This module defines the persisted configuration schema (`Config`) and safe
helpers to load and save YAML configuration files under
`~/.config/bluetooth-screen-lock/config.yaml` with restrictive permissions.
"""

import os
import yaml
import logging
import stat
from dataclasses import dataclass, asdict
from typing import Optional
from .file_ops import open_dir_nofollow, read_text_in_dir, write_replace_text_in_dir


CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "bluetooth-screen-lock")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.yaml")

logger = logging.getLogger(__name__)


@dataclass
class Config:
    """User-configurable settings persisted to YAML.

    Attributes
    ----------
    device_mac : Optional[str]
        Target device MAC (preferred) like "AA:BB:CC:DD:EE:FF".
    device_name : Optional[str]
        Optional device name used only as fallback when MAC is not set.
    rssi_threshold : int
        dBm threshold; lower (more negative) means farther.
    grace_period_sec : int
        Seconds RSSI must remain below threshold before locking.
    unseen_grace_sec : int
        Additional seconds to wait after RSSI becomes unknown before locking.
    autostart : bool
        Create a desktop autostart entry on save when enabled.
    start_delay_sec : int
        Delay app start at login by N seconds.
    near_command : Optional[str]
        Optional command to run when device becomes NEAR.
    near_shell : bool
        If true, run `near_command` via a shell.
    hysteresis_db : int
        Extra dB above threshold required for NEAR; reduces flapping.
    stale_after_sec : int
        Consider RSSI unknown if no sightings for this many seconds.
    scan_interval_sec : float
        BLE scan loop interval.
    locking_enabled : bool
        Master toggle to enable/disable automatic locking.
    re_lock_delay_sec : int
        Suppress auto-lock for this many seconds after an unlock/NEAR.
    near_consecutive_scans : int
        Require N consecutive above-near readings before NEAR triggers.
    """
    device_mac: Optional[str] = None  # e.g., "AA:BB:CC:DD:EE:FF"
    device_name: Optional[str] = None
    rssi_threshold: int = -75  # dBm
    grace_period_sec: int = 15  # seconds below threshold before locking
    unseen_grace_sec: int = 12  # seconds unseen before locking (when RSSI is unknown)
    autostart: bool = False
    start_delay_sec: int = 0  # delay app start on login
    near_command: Optional[str] = None  # command to run when device comes near
    near_shell: bool = False            # if true, run near_command via shell (explicit opt-in)
    hysteresis_db: int = 5  # additional dB above threshold required to consider NEAR (prevents flapping)
    stale_after_sec: int = 8  # invalidate RSSI if not seen for this many seconds
    scan_interval_sec: float = 2.0  # BLE scan loop interval
    locking_enabled: bool = True  # globally enable/disable automatic screen locking
    re_lock_delay_sec: int = 0  # suppress auto-locks for N seconds after an unlock/NEAR
    near_consecutive_scans: int = 2  # require N consecutive above-near readings before NEAR


DEFAULT_CONFIG = Config()


def ensure_config_dir() -> None:
    """Ensure config directory exists, is not a symlink, and has 0700 perms."""
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
    st = os.lstat(CONFIG_DIR)
    if stat.S_ISLNK(st.st_mode):
        raise RuntimeError(f"Refusing to use symlinked config dir: {CONFIG_DIR}")
    os.chmod(CONFIG_DIR, 0o700)


# File IO is provided by file_ops for dirfd-anchored, symlink-safe operations.


def load_config() -> Config:
    """Load configuration from disk or create defaults on first run.

    Ensures sane ranges for key values and hardens file permissions.
    """
    ensure_config_dir()
    if not os.path.exists(CONFIG_PATH):
        logger.info("Config not found; creating default at %s", CONFIG_PATH)
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG
    try:
        # Harden permissions of existing file before reading (best-effort)
        try:
            os.chmod(CONFIG_PATH, 0o600)
        except Exception:
            logger.debug("Could not chmod existing config file to 0600", exc_info=True)
        dirfd = open_dir_nofollow(CONFIG_DIR)
        try:
            content = read_text_in_dir(dirfd, os.path.basename(CONFIG_PATH))
        finally:
            os.close(dirfd)
        data = yaml.safe_load(content or "") or {}
        cfg = Config(**{**asdict(DEFAULT_CONFIG), **data})
        # Safety clamp: prevent excessively small scan interval
        try:
            cfg.scan_interval_sec = max(1.0, float(getattr(cfg, 'scan_interval_sec', 2.0)))
        except Exception:
            cfg.scan_interval_sec = 2.0
        # Clamp key values to sane ranges
        try:
            cfg.rssi_threshold = max(-120, min(-20, int(getattr(cfg, 'rssi_threshold', -75))))
        except Exception:
            cfg.rssi_threshold = -75
        try:
            cfg.grace_period_sec = max(0, min(600, int(getattr(cfg, 'grace_period_sec', 15))))
        except Exception:
            cfg.grace_period_sec = 15
        logger.debug("Config loaded from %s", CONFIG_PATH)
        return cfg
    except Exception:
        logger.exception("Failed to load config; using defaults")
        return DEFAULT_CONFIG


def save_config(config: Config) -> None:
    """Persist configuration atomically with restrictive permissions.

    Uses file_ops to write via a safe temp file and atomic replace anchored
    to the config directory's dirfd. Ensures destination mode is 0600 and
    fsyncs the directory entry after replacement.
    """
    ensure_config_dir()
    content = yaml.safe_dump(asdict(config), sort_keys=False)
    dirfd = open_dir_nofollow(CONFIG_DIR)
    try:
        write_replace_text_in_dir(dirfd, os.path.basename(CONFIG_PATH), content)
        # Ensure final file permissions
        try:
            os.chmod(CONFIG_PATH, 0o600)
        except Exception:
            logger.debug("Could not chmod config file to 0600", exc_info=True)
        # Fsync the directory metadata to persist the rename
        try:
            os.fsync(dirfd)
        except Exception:
            logger.debug("Directory fsync failed after config write", exc_info=True)
    finally:
        os.close(dirfd)
    logger.debug("Config saved to %s (atomic)", CONFIG_PATH)
