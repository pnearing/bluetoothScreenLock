"""Configuration utilities for Bluetooth Screen Lock.

This module defines:

- The persisted configuration schema (`Config`).
- Safe helpers to load and save YAML configuration files under
  `~/.config/bluetooth-screen-lock/config.yaml` (0600 perms).
- XDG state helpers and default log-file path resolution.

Logging file location policy:
- Prefer `$XDG_STATE_HOME/bluetooth-screen-lock/bluetooth-screen-lock.log`
  (creating the app subdirectory if the state root exists).
- If the XDG state root does not exist, use `~/bluetooth-screen-lock.log`.
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

# XDG state directory (root). We do not create the root if it does not exist,
# to follow the policy "use state if it exists, else fall back to $HOME".
XDG_STATE_HOME = os.environ.get("XDG_STATE_HOME", os.path.join(os.path.expanduser("~"), ".local", "state"))
STATE_DIR = os.path.join(XDG_STATE_HOME, "bluetooth-screen-lock")

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
    near_shell_warned : bool
        One-time flag: true after the UI has shown the safety warning about
        enabling shell execution for the near command.
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
    near_dwell_sec : int
        Minimum seconds the device must remain NEAR before running near_command.
        0 disables dwell (immediate on NEAR transition).
    cycle_rate_limit_min : int
        Global rate limit window in minutes. At most one lock+unlock cycle
        is allowed per window. 0 disables the rate limit.
    near_timeout_sec : int
        Maximum time to allow the near command to run before it is forcefully
        terminated. 0 disables the timeout (default).
    near_kill_grace_sec : int
        After sending SIGTERM on timeout, wait this many seconds before
        sending SIGKILL if the process group has not exited.
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
    # One-time UI notice shown when enabling shell execution for near_command
    near_shell_warned: bool = False
    hysteresis_db: int = 5  # additional dB above threshold required to consider NEAR (prevents flapping)
    stale_after_sec: int = 8  # invalidate RSSI if not seen for this many seconds
    scan_interval_sec: float = 2.0  # BLE scan loop interval
    locking_enabled: bool = True  # globally enable/disable automatic screen locking
    re_lock_delay_sec: int = 0  # suppress auto-locks for N seconds after an unlock/NEAR
    near_consecutive_scans: int = 2  # require N consecutive above-near readings before NEAR
    # Require the device to remain NEAR for this many seconds before running near_command
    near_dwell_sec: int = 0
    # Global rate-limit: allow at most one lock+unlock cycle per M minutes (0 = disabled)
    cycle_rate_limit_min: int = 0
    # Timeout behavior for near_command: 0 disables timeout; grace controls SIGKILL delay
    near_timeout_sec: int = 0
    near_kill_grace_sec: int = 5
    # --- File logging options ---
    # Master toggle to enable writing logs to a rotating file in addition to stdout/stderr
    file_logging_enabled: bool = False
    # Optional explicit log file path. If None, we resolve a default path via XDG state
    # (see `default_log_path`).
    file_log_path: Optional[str] = None
    # Rotate at ~5 MiB by default, keep 3 backups
    file_log_max_bytes: int = 5_242_880
    file_log_backups: int = 3


DEFAULT_CONFIG = Config()


def ensure_config_dir() -> None:
    """Ensure config directory exists, is not a symlink, and has 0700 perms."""
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
    st = os.lstat(CONFIG_DIR)
    if stat.S_ISLNK(st.st_mode):
        raise RuntimeError(f"Refusing to use symlinked config dir: {CONFIG_DIR}")
    os.chmod(CONFIG_DIR, 0o700)


def default_log_path() -> str:
    """Return the default log file path based on XDG state policy.

    Policy:
    - If the XDG state root directory exists (file-system check), place the log
      under `$XDG_STATE_HOME/bluetooth-screen-lock/bluetooth-screen-lock.log`.
      We will ensure the app subdirectory exists with 0700 perms when used.
    - Otherwise, fall back to a file in the home directory: `~/bluetooth-screen-lock.log`.

    Note: This function does not create any directories/files; callers adding
    a file handler should ensure parent dir exists as needed.
    """
    try:
        state_root = XDG_STATE_HOME
        if os.path.isdir(state_root):
            return os.path.join(STATE_DIR, "bluetooth-screen-lock.log")
    except Exception:
        logger.debug("Failed to stat XDG_STATE_HOME; using home fallback", exc_info=True)
    return os.path.join(os.path.expanduser("~"), "bluetooth-screen-lock.log")


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
        # Clamp new options to sane ranges
        try:
            cfg.near_dwell_sec = max(0, min(600, int(getattr(cfg, 'near_dwell_sec', 0))))
        except Exception:
            cfg.near_dwell_sec = 0
        try:
            cfg.cycle_rate_limit_min = max(0, min(240, int(getattr(cfg, 'cycle_rate_limit_min', 0))))
        except Exception:
            cfg.cycle_rate_limit_min = 0
        # Timeout/clamping for near command execution
        try:
            cfg.near_timeout_sec = max(0, min(3600, int(getattr(cfg, 'near_timeout_sec', 0))))
        except Exception:
            cfg.near_timeout_sec = 0
        try:
            cfg.near_kill_grace_sec = max(1, min(60, int(getattr(cfg, 'near_kill_grace_sec', 5))))
        except Exception:
            cfg.near_kill_grace_sec = 5
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
