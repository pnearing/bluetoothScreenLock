# Bluetooth Screen Lock (GNOME)

Lock your screen automatically when your selected Bluetooth device (e.g., your phone) goes out of range. Prefers RSSI-based proximity using BLE advertisements, with a fallback to not-seen timeout.

## Features
- __GNOME/GTK tray app__ with a status indicator and Settings.
- __RSSI preferred__ proximity detection via Bleak; configurable threshold and grace period.
- Locks screen via `loginctl lock-session` (systemd-logind).
- __Re-lock delay after unlock__: optional cooldown window after an actual system unlock to prevent immediate auto-locks. Unlock detection via GNOME ScreenSaver, freedesktop ScreenSaver, and systemd-logind.

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
 - Optionally set a Re-lock delay (seconds) to suppress auto-locks right after you unlock.

The tray menu shows live RSSI while monitoring. The app locks the session when either:

- RSSI stays below the threshold for `grace_period_sec`, or
- The device is not seen for `stale_after_sec + unseen_grace_sec` (i.e., after RSSI becomes stale, wait extra time before locking).

## Autostart and startup delay

- Enable/disable autostart from Settings. This manages `~/.config/autostart/bluetooth-screen-lock.desktop` for your user.
- You can configure an optional "Start delay" to defer launching after login (useful to let the desktop fully initialize). The desktop entry's `Exec` command is wrapped to honor this delay.

## Near-action command

- Optional "Near command" runs when your device transitions from away to near (after having been away at least once). Leave blank to disable.

## Re-lock delay (unlock detection)

- When enabled, the app starts a cooldown timer each time it detects that your session was unlocked.
- During the cooldown, away events will not trigger an automatic lock.
- Unlock detection sources:
  - GNOME: `org.gnome.ScreenSaver` `ActiveChanged(false)` on `/org/gnome/ScreenSaver`.
  - Freedesktop: `org.freedesktop.ScreenSaver` `ActiveChanged(false)` on `/org/freedesktop/ScreenSaver`.
  - systemd-logind: `org.freedesktop.login1.Session` `LockedHint=false` via `PropertiesChanged` on your session path.

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

## How it works (overview)

- __Proximity__: `monitor.py` uses Bleak to watch BLE advertisements. It marks "away" when RSSI stays below threshold for `grace_period_sec`, or when the device is unseen for `stale_after_sec + unseen_grace_sec`.
- __Locking__: `app.py` attempts several lock methods, preferring `loginctl lock-session`.
- __Re-lock delay__: after an actual __unlock__ signal is observed, the app suppresses auto-locks for `re_lock_delay_sec` seconds.

## Unlock signals and verification

- Sources listened to by `app.py`:
  - __GNOME__: `org.gnome.ScreenSaver` → `ActiveChanged(false)` on `/org/gnome/ScreenSaver`.
  - __Freedesktop__: `org.freedesktop.ScreenSaver` → `ActiveChanged(false)` on `/org/freedesktop/ScreenSaver`.
  - __systemd-logind__: `org.freedesktop.login1.Session` → `LockedHint=false` via `org.freedesktop.DBus.Properties::PropertiesChanged`.

- Verify signals with `gdbus`:

```bash
# GNOME
gdbus monitor --session --dest org.gnome.ScreenSaver

# Freedesktop screensaver
gdbus monitor --session --dest org.freedesktop.ScreenSaver

# login1 (system bus): watch all, look for PropertiesChanged on your Session
gdbus monitor --system --dest org.freedesktop.login1
```

When you unlock, you should see either `ActiveChanged false` (screensaver) or `LockedHint` change to `false` (login1).

## Testing the re-lock delay

1. Set a noticeable value (e.g., 60s) in Settings → "Re-lock delay (sec)".
2. Lock your session manually or let the app lock it.
3. Unlock your session. Within the next 60s, walk away (or power off Bluetooth) to trigger "away". The tray should show `Away (cooldown Ns)` and not re-lock.
4. After the cooldown expires, away should lock as usual.

## Desktop environment notes

- __GNOME__ (Wayland/X11): all three paths typically available; ScreenSaver and login1 are preferred.
- __KDE Plasma__: login1 is reliable. Plasma also provides `org.freedesktop.ScreenSaver` signals.
- __Xfce/others__: usually expose `org.freedesktop.ScreenSaver`; login1 works on systemd-based systems.

## Troubleshooting unlock detection

- If cooldown never activates after unlock:
  - Check logs (run with `LOG_LEVEL=DEBUG`).
  - Use the `gdbus monitor` commands above to confirm that your DE emits one of the expected signals.
  - Ensure systemd-logind is running (for login1): `loginctl seat-status`.
- If auto-lock never happens: set a less conservative RSSI threshold or shorter grace periods; confirm BLE scans are working.

## Config
User config is stored at `~/.config/bluetooth-screen-lock/config.yaml` with keys:
- `device_mac`, `device_name`
- `rssi_threshold` (default -75)
- `grace_period_sec` (default 15)
- `unseen_grace_sec` (default 12) — additional wait after RSSI becomes stale before locking due to "unseen".
- `autostart` (default false)
- `start_delay_sec` (default 0)
- `near_command` (default null)
- `hysteresis_db` (default 5)
- `stale_after_sec` (default 8)
- `scan_interval_sec` (default 2.0) — BLE scan loop interval.
- `locking_enabled` (default true) — master toggle for automatic locking.
- `re_lock_delay_sec` (default 0) — seconds to suppress auto-lock after an unlock. 0 disables.

### Tuning recommendations
- __Make grace > stale__: set `grace_period_sec` moderately higher than `stale_after_sec` to avoid locking on brief advertising gaps.
- __Unseen buffer__: set `unseen_grace_sec` so that unseen lock ≈ `stale_after_sec + unseen_grace_sec` is longer than typical gaps. Example: `stale_after_sec: 8`, `unseen_grace_sec: 12` → unseen lock ≈ 20s.
- __Scan interval__: lower `scan_interval_sec` (e.g., 1.0) for smoother updates; higher values reduce CPU at the cost of responsiveness.

## Roadmap
- Advanced heuristics/smoothing for RSSI.

## License

This project is licensed under the Apache License, Version 2.0.

- See `LICENSE` at the repository root for the full license text.
- See `NOTICE` for attributions required by the license.

Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
