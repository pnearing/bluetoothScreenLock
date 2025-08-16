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

Open the tray menu and click "Settings…".

- Device: Scan and select your phone. Prefer MAC over name matching to avoid spoofing.
- RSSI threshold (dBm): Typical near is around -50 to -65; far is -80 to -90. Start around -75.
- Grace period (sec): How long RSSI must stay below threshold before locking (avoid dips).
- Hysteresis (dB): Extra dB above threshold required to treat as NEAR; reduces flapping.
- Stale RSSI timeout: Consider RSSI unknown if device not seen for this many seconds.
- Near debounce (scans): Require N consecutive scans above near trigger to treat as NEAR.
- Re-lock delay (sec): Suppress auto-locks briefly after you unlock.
- Start at login / Start delay: Manage autostart .desktop creation and optional delay.
- Optional near command: Run a command when device becomes NEAR (e.g., dismiss screensaver).

The current RSSI is shown live to help pick a suitable threshold.

## Tray Menu

- Status line shows current state (RSSI, Monitoring, Away, etc.).
- Warning line appears when name-only matching is active.
- Lock now: Immediate manual lock.
- Enable locking: Toggle automatic locking.
- Settings…, Quit.

## Troubleshooting

- Ensure Bluetooth is on and your device is advertising (screen on) during scan.
- If your phone rotates MAC addresses, pair it or use name equality as a fallback (less secure).
- Logs: set `LOG_LEVEL=DEBUG` before launching to get detailed logs.
- If locking fails, the app tries several methods: loginctl, GNOME DBus, xdg-screensaver, etc.
