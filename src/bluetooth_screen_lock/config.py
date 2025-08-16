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


def _safe_open_nofollow(path: str, mode: int = 0o600):
    """Open a file securely for writing without following symlinks.

    Returns a raw file descriptor opened with O_NOFOLLOW when available and
    verifies the target is not a symlink as a defense-in-depth check.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, mode)
    # Paranoid double-check: ensure we didn't open a symlink
    st = os.fstat(fd)
    if stat.S_ISLNK(st.st_mode):
        os.close(fd)
        raise RuntimeError(f"Refusing to write symlink: {path}")
    return fd


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
        # Harden permissions of existing file before reading
        try:
            os.chmod(CONFIG_PATH, 0o600)
        except Exception:
            logger.debug("Could not chmod existing config file to 0600", exc_info=True)
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
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
    """Persist configuration atomically with restrictive permissions."""
    ensure_config_dir()
    # Write with restrictive permissions regardless of umask, and do not follow symlinks
    fd = _safe_open_nofollow(CONFIG_PATH, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(asdict(config), f, sort_keys=False)
            # Flush and fsync for durability
            f.flush()
            os.fsync(f.fileno())
        # Ensure permissions are correct even if file pre-existed
        os.chmod(CONFIG_PATH, 0o600)
        # fsync the directory to persist the entry metadata
        dirfd = os.open(os.path.dirname(CONFIG_PATH), os.O_DIRECTORY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    except Exception:
        # If opening or writing fails, ensure fd is closed
        try:
            os.close(fd)
        except Exception:
            pass
        raise
    logger.debug("Config saved to %s", CONFIG_PATH)
