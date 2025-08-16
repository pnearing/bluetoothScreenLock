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
    near_command: Optional[str] = None  # shell command to run when device comes near
    hysteresis_db: int = 5  # additional dB above threshold required to consider NEAR (prevents flapping)
    stale_after_sec: int = 8  # invalidate RSSI if not seen for this many seconds
    scan_interval_sec: float = 2.0  # BLE scan loop interval
    locking_enabled: bool = True  # globally enable/disable automatic screen locking
    re_lock_delay_sec: int = 0  # suppress auto-locks for N seconds after an unlock/NEAR


DEFAULT_CONFIG = Config()


def ensure_config_dir() -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)


def load_config() -> Config:
    ensure_config_dir()
    if not os.path.exists(CONFIG_PATH):
        logger.info("Config not found; creating default at %s", CONFIG_PATH)
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        cfg = Config(**{**asdict(DEFAULT_CONFIG), **data})
        logger.debug("Config loaded from %s", CONFIG_PATH)
        return cfg
    except Exception:
        logger.exception("Failed to load config; using defaults")
        return DEFAULT_CONFIG


def save_config(config: Config) -> None:
    ensure_config_dir()
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(asdict(config), f, sort_keys=False)
    logger.debug("Config saved to %s", CONFIG_PATH)
