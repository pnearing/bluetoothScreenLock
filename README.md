# Bluetooth Screen Lock (GNOME)

Lock your screen automatically when your selected Bluetooth device (e.g., your phone) goes out of range. Prefers RSSI-based proximity using BLE advertisements, with a fallback to not-seen timeout.

## Features
- __GNOME/GTK tray app__ with a status indicator and Settings.
- __RSSI preferred__ proximity detection via Bleak; configurable threshold and grace period.
- Locks screen via `loginctl lock-session` (systemd-logind).

## Requirements
This app uses PyGObject (GTK) and AppIndicator via GObject Introspection. Install system packages (Ubuntu/Debian):

```bash
sudo apt update
sudo apt install -y \
  python3-gi gir1.2-gtk-3.0 \
  gir1.2-appindicator3-0.1 || sudo apt install -y gir1.2-ayatanaappindicator3-0.1

# Bluetooth stack (usually already installed)
sudo apt install -y bluez bluez-obexd
```

Python packages (installed via pip):

```bash
pip install -r requirements.txt
```

If you use a virtualenv:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

You can run the app either as a Python module (recommended for development) or via the installed launcher script.

- Module (dev):

```bash
python3 -m bluetooth_screen_lock
```

- Installed launcher (after packaging/install):

```bash
/usr/bin/bluetooth-screen-lock
```

You should see a tray icon (wireless icon). Open Settings to:
- Scan for nearby BLE devices and select your phone.
- Set RSSI threshold (e.g., -75 dBm) and grace period (seconds).

The tray menu shows live RSSI while monitoring. When RSSI stays below the threshold or the device isn’t seen for the grace period, the app locks the session.

## Autostart and startup delay

- Enable/disable autostart from Settings. This manages `~/.config/autostart/bluetooth-screen-lock.desktop` for your user.
- You can configure an optional "Start delay" to defer launching after login (useful to let the desktop fully initialize). The desktop entry's `Exec` command is wrapped to honor this delay.

## Near-action command

- Optional "Near command" runs when your device transitions from away to near (after having been away at least once). Leave blank to disable.

## Version

The package exposes a unified version string:

```python
from bluetooth_screen_lock import __version__
print(__version__)
```

## Notes/Troubleshooting
- Ensure Bluetooth is powered on: `bluetoothctl power on`.
- Some phones randomize MAC addresses per advertising; if RSSI doesn’t appear, try pairing the device or ensure it advertises while screen is on.
- AppIndicator package name varies by distro. If `gir1.2-appindicator3-0.1` isn’t available, install `gir1.2-ayatanaappindicator3-0.1`.
- No sudo required; Bleak uses BlueZ over D-Bus.

## Config
User config is stored at `~/.config/bluetooth-screen-lock/config.yaml` with keys:
- `device_mac`, `device_name`
- `rssi_threshold` (default -75)
- `grace_period_sec` (default 8)
- `autostart` (default false)
- `start_delay_sec` (default 0)
- `near_command` (default null)
- `hysteresis_db` (default 5)
- `stale_after_sec` (default 6)

## Roadmap
- Optional systemd user service to autostart on login.
- Advanced heuristics/smoothing for RSSI.
