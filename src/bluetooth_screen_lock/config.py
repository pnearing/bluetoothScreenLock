import os
import yaml
import logging
from dataclasses import dataclass, asdict
from typing import Optional


CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "bluetooth-screen-lock")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.yaml")

logger = logging.getLogger(__name__)


@dataclass
class Config:
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


DEFAULT_CONFIG = Config()


def ensure_config_dir() -> None:
    # Ensure directory exists with restrictive permissions
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
    try:
        st = os.stat(CONFIG_DIR)
        # If existing perms are more permissive, tighten them
        if (st.st_mode & 0o777) != 0o700:
            os.chmod(CONFIG_DIR, 0o700)
    except Exception:
        logger.debug("Could not verify/chmod config dir perms", exc_info=True)


def load_config() -> Config:
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
        logger.debug("Config loaded from %s", CONFIG_PATH)
        return cfg
    except Exception:
        logger.exception("Failed to load config; using defaults")
        return DEFAULT_CONFIG


def save_config(config: Config) -> None:
    ensure_config_dir()
    # Write with restrictive permissions regardless of umask
    fd = os.open(CONFIG_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(asdict(config), f, sort_keys=False)
        # In case file pre-existed with looser perms, ensure 0600
        try:
            os.chmod(CONFIG_PATH, 0o600)
        except Exception:
            logger.debug("Could not chmod config file to 0600", exc_info=True)
    except Exception:
        # If opening or writing fails, ensure fd is closed
        try:
            os.close(fd)
        except Exception:
            pass
        raise
    logger.debug("Config saved to %s", CONFIG_PATH)
