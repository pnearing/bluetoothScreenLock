# User Guide

Bluetooth Screen Lock is a GNOME/GTK system tray app that locks your screen when your phone (or other BLE device) moves away, using RSSI.

## Requirements

- Linux desktop (GNOME recommended). GTK-based tray (AppIndicator/Ayatana).
- Python 3.10+
- System packages: PyGObject (Gtk 3), BlueZ, Bluetooth enabled
- Python packages: bleak, PyYAML (installed by packaging)

## Install

From source:

```bash
# optional: create a venv
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

Run:

```bash
bluetooth-screen-lock
# or
python3 -m bluetooth_screen_lock
```

To autostart at login, use the Settings dialog's "Start at login" checkbox.

## Configure

Open the tray menu and click "Settings…" (tabs: General, Near Command, Advanced).

- Device: Scan and select your phone (General). Prefer MAC over name matching to avoid spoofing.
- RSSI threshold (dBm): Typical near is around -50 to -65; far is -80 to -90. Start around -75 (General).
- Grace period (sec): How long RSSI must stay below threshold before locking (avoid dips) (General).
- Hysteresis (dB): Extra dB above threshold required to treat as NEAR; reduces flapping (Advanced).
- Stale RSSI timeout: Consider RSSI unknown if device not seen for this many seconds (Advanced).
- Near debounce (scans): Require N consecutive scans above near trigger to treat as NEAR (Advanced).
- Re-lock delay (sec): Suppress auto-locks briefly after you unlock (Advanced).
- Cycle rate limit (min): Allow at most one lock+unlock cycle per M minutes to avoid churn (Advanced).
- Start at login / Start delay: Manage autostart .desktop creation and optional delay (General).

Near Command tab:

- Execution:
  - Optional near command: run when device becomes NEAR (e.g., dismiss screensaver).
  - Run command in shell (advanced) for pipes/redirection if needed.
- Timing:
  - Near dwell (sec): require device to remain NEAR before running.
  - Near command timeout (sec): kill long-running commands after N seconds (0 disables).
  - Near command kill grace (sec): wait after SIGTERM before SIGKILL when a timeout occurs.

The current RSSI is shown live to help pick a suitable threshold.

## Logging

- Enable "Write log file" in Settings to additionally write logs to a rotating file.
- Default log path resolves to:
  - If `$XDG_STATE_HOME` exists: `$XDG_STATE_HOME/bluetooth-screen-lock/bluetooth-screen-lock.log`
  - Else: `~/bluetooth-screen-lock.log`
- Rotation defaults: 5 MiB per file, 3 backups.
- Stdout contains DEBUG/INFO; stderr contains WARNING and above.
- When run as a user systemd service, logs are also visible in the journal:

```bash
journalctl --user -u bluetooth-screen-lock
```

## Tray Menu

- Status line shows current state (RSSI, Monitoring, Away, etc.).
- Warning line appears when name-only matching is active.
- Lock now: Immediate manual lock.
- Enable locking: Toggle automatic locking.
- Settings…, Quit.

## Troubleshooting

- Ensure Bluetooth is on and your device is advertising (screen on) during scan.
- If your phone rotates MAC addresses, pair it or use name equality as a fallback (less secure).
- Verbosity: set `LOG_LEVEL=DEBUG` before launching to get detailed logs.
- If locking fails, the app tries several methods: loginctl, GNOME DBus, xdg-screensaver, etc.
- DBus calls have finite timeouts (e.g., ~3s) to avoid UI hangs when services stall. If your desktop's DBus is slow, operations may retry/fallback automatically.
- Autostart delay wrapper uses absolute binaries and falls back gracefully if helpers like `base64` are unavailable.
