import asyncio
import base64
import os
import shlex
import shutil
import subprocess
import threading
import logging
import time
from typing import Optional

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Gio

from .config import load_config, save_config, Config
from .monitor import ProximityMonitor, MonitorConfig
from .indicator import TrayIndicator
from .settings import SettingsWindow, SettingsResult


logger = logging.getLogger(__name__)


class App:
    def __init__(self) -> None:
        logger.debug("Initializing App")
        self._cfg: Config = load_config()
        self._indicator = TrayIndicator(
            app_id="bluetooth-screen-lock",
            on_open_settings=self._open_settings,
            on_quit=self.quit,
            on_lock_now=self._lock_screen,
            on_toggle_locking=self._on_toggle_locking,
            locking_enabled=bool(getattr(self._cfg, "locking_enabled", True)),
        )

        # Ensure autostart entry reflects current config on startup
        try:
            self._apply_autostart(self._cfg.autostart)
        except Exception:
            logger.exception("Failed to apply autostart on startup")

        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(target=self._run_loop, daemon=True)
        self._loop_thread.start()

        self._monitor: Optional[ProximityMonitor] = None
        self._was_near: bool = False
        # Armed indicates whether a near transition is allowed to trigger the near command.
        # Start disarmed so that if the app launches while the device is already near,
        # we do NOT execute the near command until the device has gone away at least once.
        self._armed: bool = False
        # Timestamp of the last time the session was unlocked (used for re-lock delay cooldown)
        self._last_unlock_ts: float = 0.0
        self._ensure_monitor()

        # Listen for unlock signals from the session to implement re-lock delay
        try:
            self._init_unlock_monitor()
        except Exception:
            logger.exception("Failed to initialize unlock monitor; re-lock delay may not work")

        self._indicator.set_status(
            "Idle" if not self._cfg.device_mac else (
                "Monitoring" if getattr(self._cfg, "locking_enabled", True) else "Monitoring (lock off)"
            )
        )
        # Show warning if using name-only matching
        self._update_name_fallback_warning()
        logger.info("App ready: %s", "Idle" if not self._cfg.device_mac else "Monitoring")

    def _run_loop(self) -> None:
        logger.debug("Starting asyncio loop thread")
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _ensure_monitor(self) -> None:
        if not self._cfg.device_mac:
            logger.debug("No device configured; monitor not started")
            return
        mon_cfg = MonitorConfig(
            device_mac=self._cfg.device_mac,
            device_name=self._cfg.device_name,
            rssi_threshold=self._cfg.rssi_threshold,
            grace_period_sec=self._cfg.grace_period_sec,
            hysteresis_db=getattr(self._cfg, 'hysteresis_db', 5),
            stale_after_sec=getattr(self._cfg, 'stale_after_sec', 6),
            unseen_grace_sec=getattr(self._cfg, 'unseen_grace_sec', self._cfg.grace_period_sec),
            scan_interval_sec=float(getattr(self._cfg, 'scan_interval_sec', 2.0)),
        )
        def on_near(rssi: int) -> None:
            try:
                # Determine near condition: RSSI above threshold
                is_near = rssi > self._cfg.rssi_threshold
                if is_near and not self._was_near:
                    # Transitioned to NEAR
                    if self._armed:
                        self._run_near_command()
                        # Disarm until we've observed an AWAY again
                        self._armed = False
                self._was_near = is_near
            except Exception:
                logger.exception("on_near handling failed")

        def on_away() -> None:
            # Reset near state and lock
            self._was_near = False
            # Arm the near action so that the next NEAR transition can execute the command
            self._armed = True
            # Only auto-lock if locking is enabled
            if getattr(self._cfg, "locking_enabled", True):
                # Enforce re-lock delay after an actual UNLOCK event
                cooldown = max(0, int(getattr(self._cfg, "re_lock_delay_sec", 0)))
                if cooldown > 0 and self._last_unlock_ts:
                    since_unlock = time.time() - self._last_unlock_ts
                    if since_unlock < cooldown:
                        remaining = int(cooldown - since_unlock)
                        logger.info("Re-lock delay active: skipping auto-lock (%ss remaining)", remaining)
                        self._indicator.set_status(f"Away (cooldown {remaining}s)")
                        return
                self._lock_screen()
            else:
                self._indicator.set_status("Away (lock off)")

        def on_rssi(rssi: Optional[int]) -> None:
            try:
                if rssi is None:
                    self._indicator.set_status("Not Read")
                else:
                    self._indicator.set_status(f"RSSI {int(rssi)} dBm")
            except Exception:
                logger.exception("on_rssi handling failed")

        self._monitor = ProximityMonitor(
            config=mon_cfg,
            on_away=on_away,
            on_near=on_near,
            on_rssi=on_rssi,
        )
        # Start in asyncio loop thread
        def start_monitor() -> None:
            assert self._monitor is not None
            logger.info("Starting proximity monitor for %s (threshold=%s dBm, grace=%ss)",
                        self._cfg.device_mac, self._cfg.rssi_threshold, self._cfg.grace_period_sec)
            self._monitor.start()
        self._call_soon_threadsafe(start_monitor)

    def _call_soon_threadsafe(self, func) -> None:
        def runner():
            try:
                func()
            except Exception:
                logger.exception("Error running thread-safe call")
        self._loop.call_soon_threadsafe(runner)

    def _try_run(self, args: list[str]) -> bool:
        """Run a command if available and return True on success (exit code 0)."""
        try:
            if not args or shutil.which(args[0]) is None:
                return False
            res = subprocess.run(args, check=False)
            return res.returncode == 0
        except Exception:
            logger.debug("Lock command failed: %s", " ".join(args) if args else "<none>", exc_info=True)
            return False

    def _lock_screen(self) -> None:
        # Lock screen with prioritized fallbacks. GNOME/Wayland: loginctl first.
        try:
            logger.warning("Away detected; attempting to lock screen")
            candidates: list[list[str]] = [
                # Primary: systemd-logind (GNOME/Wayland)
                ["loginctl", "lock-session"],
                # GNOME screensaver command (X11)
                ["gnome-screensaver-command", "-l"],
                # GNOME via DBus (alternative)
                ["dbus-send", "--session", "--dest=org.gnome.ScreenSaver", 
                 "/org/gnome/ScreenSaver", "org.gnome.ScreenSaver.Lock"],
                ["gdbus", "call", "--session", "--dest", "org.gnome.ScreenSaver", 
                 "--object-path", "/org/gnome/ScreenSaver", "--method", "org.gnome.ScreenSaver.Lock"],
                # Desktop-agnostic fallback
                ["xdg-screensaver", "lock"],
                # LightDM
                ["dm-tool", "lock"],
                # XScreenSaver
                ["xscreensaver-command", "-lock"],
                # systemd: lock all sessions as a last resort
                ["loginctl", "lock-sessions"],
            ]

            for cmd in candidates:
                if self._try_run(cmd):
                    logger.info("Screen locked via: %s", " ".join(cmd))
                    GLib.idle_add(lambda: self._indicator.set_status("Locked (away)"))
                    return

            logger.error("All lock methods failed")
            GLib.idle_add(lambda: self._indicator.set_status("Lock failed"))
        except Exception:
            logger.exception("Failed to lock screen")

    def _run_near_command(self) -> None:
        try:
            cmd = (self._cfg.near_command or "").strip()
            if not cmd:
                return
            logger.info("Running near command: %s", cmd)
            # Run in background, do not wait. Default: no shell. Opt-in via cfg.near_shell=True.
            use_shell = bool(getattr(self._cfg, "near_shell", False))
            if use_shell:
                subprocess.Popen(cmd, shell=True)
            else:
                argv = shlex.split(cmd)
                if not argv:
                    return
                subprocess.Popen(argv)
        except Exception:
            logger.exception("Failed to run near command")

    def _open_settings(self) -> None:
        logger.debug("Opening settings window")
        initial = SettingsResult(
            device_mac=self._cfg.device_mac,
            device_name=self._cfg.device_name,
            rssi_threshold=self._cfg.rssi_threshold,
            grace_period_sec=self._cfg.grace_period_sec,
            autostart=self._cfg.autostart,
            start_delay_sec=self._cfg.start_delay_sec,
            near_command=self._cfg.near_command,
            near_shell=bool(getattr(self._cfg, 'near_shell', False)),
            hysteresis_db=getattr(self._cfg, 'hysteresis_db', 5),
            stale_after_sec=getattr(self._cfg, 'stale_after_sec', 6),
            re_lock_delay_sec=int(getattr(self._cfg, 're_lock_delay_sec', 0)),
            scan_interval_sec=float(getattr(self._cfg, 'scan_interval_sec', 2.0)),
        )
        win = SettingsWindow(initial)
        win.set_transient_for(None)
        win.connect("destroy", lambda _w: None)
        win.show_all()

        def on_hide(_w):
            result = win.get_result()
            logger.info("Settings saved: device=%s name=%s rssi=%s grace=%s, near_command=%s, near_shell=%s", 
                        result.device_mac, result.device_name, result.rssi_threshold, result.grace_period_sec, result.near_command, getattr(result, 'near_shell', False))
            self._cfg.device_mac = result.device_mac
            self._cfg.device_name = result.device_name
            self._cfg.rssi_threshold = result.rssi_threshold
            self._cfg.grace_period_sec = result.grace_period_sec
            self._cfg.near_command = getattr(result, 'near_command', None)
            self._cfg.near_shell = bool(getattr(result, 'near_shell', getattr(self._cfg, 'near_shell', False)))
            self._cfg.hysteresis_db = int(getattr(result, 'hysteresis_db', getattr(self._cfg, 'hysteresis_db', 5)))
            self._cfg.stale_after_sec = int(getattr(result, 'stale_after_sec', getattr(self._cfg, 'stale_after_sec', 6)))
            self._cfg.re_lock_delay_sec = int(getattr(result, 're_lock_delay_sec', getattr(self._cfg, 're_lock_delay_sec', 0)))
            self._cfg.scan_interval_sec = float(getattr(result, 'scan_interval_sec', getattr(self._cfg, 'scan_interval_sec', 2.0)))
            
            # Handle autostart toggle or delay change
            autostart_changed = (self._cfg.autostart != result.autostart)
            delay_changed = (self._cfg.start_delay_sec != result.start_delay_sec)
            self._cfg.autostart = result.autostart
            self._cfg.start_delay_sec = max(0, int(result.start_delay_sec))
            if autostart_changed or (self._cfg.autostart and delay_changed):
                self._apply_autostart(self._cfg.autostart)
            save_config(self._cfg)
            self._indicator.set_status("Monitoring" if result.device_mac else "Idle")
            # Refresh warning after settings change
            self._update_name_fallback_warning()
            if self._monitor and result.device_mac:
                mon_cfg = MonitorConfig(
                    device_mac=result.device_mac,
                    device_name=result.device_name,
                    rssi_threshold=result.rssi_threshold,
                    grace_period_sec=result.grace_period_sec,
                    hysteresis_db=int(getattr(result, 'hysteresis_db', getattr(self._cfg, 'hysteresis_db', 5))),
                    stale_after_sec=int(getattr(result, 'stale_after_sec', getattr(self._cfg, 'stale_after_sec', 6))),
                    unseen_grace_sec=int(getattr(self._cfg, 'unseen_grace_sec', result.grace_period_sec)),
                    scan_interval_sec=float(getattr(self._cfg, 'scan_interval_sec', 2.0)),
                )
                self._monitor.update_config(mon_cfg)
            elif result.device_mac and not self._monitor:
                self._ensure_monitor()

        win.connect("hide", on_hide)

    def _on_toggle_locking(self, enabled: bool) -> None:
        try:
            self._cfg.locking_enabled = bool(enabled)
            save_config(self._cfg)
            # Reflect in UI
            if self._cfg.device_mac:
                self._indicator.set_status("Monitoring" if enabled else "Monitoring (lock off)")
            # Ensure the tray toggle reflects canonical state (in case it was changed programmatically elsewhere)
            self._indicator.set_locking_enabled(self._cfg.locking_enabled)
            logger.info("Locking %s", "enabled" if enabled else "disabled")
        except Exception:
            logger.exception("Failed to persist locking toggle")

    def _update_name_fallback_warning(self) -> None:
        """Show or hide a tray warning if name-based fallback matching is active.
        Active when a device name is set but MAC address is not configured.
        """
        try:
            name = (self._cfg.device_name or "").strip()
            mac = (self._cfg.device_mac or "").strip()
            if name and not mac:
                self._indicator.set_warning("Name-only matching enabled; prefer MAC to avoid spoofing/false positives.")
            else:
                self._indicator.set_warning(None)
        except Exception:
            logger.exception("Failed to update name fallback warning")

    def _apply_autostart(self, enable: bool) -> None:
        """Create or remove the autostart .desktop entry for this app."""
        try:
            autostart_dir = os.path.join(os.path.expanduser("~"), ".config", "autostart")
            os.makedirs(autostart_dir, exist_ok=True)
            dst = os.path.join(autostart_dir, "bluetooth-screen-lock.desktop")

            if enable:
                # Prefer copying existing applications desktop if available for consistency
                src = os.path.join(os.path.expanduser("~"), ".local", "share", "applications", "bluetooth-screen-lock.desktop")
                if os.path.exists(src):
                    shutil.copyfile(src, dst)
                else:
                    # Fallback: generate a minimal desktop entry
                    exec_cmd = os.path.join(os.path.expanduser("~"), ".local", "bin", "bluetooth-screen-lock")
                    if not os.path.exists(exec_cmd):
                        # Fall back to running the module with PYTHONPATH pointing to project dir
                        project_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
                        src_dir = os.path.join(project_dir, "src")
                        # Use sh -c so we can set PYTHONPATH then exec the module
                        exec_cmd = (
                            f"/bin/sh -c 'PYTHONPATH=\"{src_dir}:$PYTHONPATH\" exec python3 -m bluetooth_screen_lock'"
                        )
                    exec_path = self._wrap_with_delay(exec_cmd)
                    content = (
                        "[Desktop Entry]\n"
                        "Type=Application\n"
                        "Name=Bluetooth Screen Lock\n"
                        "Comment=Lock/unlock the screen based on Bluetooth proximity\n"
                        f"Exec={exec_path}\n"
                        "Icon=bluetooth-screen-lock\n"
                        "Terminal=false\n"
                        "Categories=Utility;GTK;\n"
                        "X-GNOME-UsesNotifications=true\n"
                        "X-GNOME-Autostart-enabled=true\n"
                        "Hidden=false\n"
                    )
                    with open(dst, "w", encoding="utf-8") as f:
                        f.write(content)
                # Ensure autostart flags and adjust Exec for delay if needed
                self._ensure_autostart_flags(dst)
                self._ensure_exec_delay(dst)
                logger.info("Autostart enabled")
            else:
                if os.path.exists(dst):
                    os.remove(dst)
                logger.info("Autostart disabled")
        except Exception:
            logger.exception("Failed to apply autostart setting")

    @staticmethod
    def _ensure_autostart_flags(desktop_path: str) -> None:
        """Ensure the desktop file has autostart-related keys set correctly."""
        try:
            # Read, tweak keys, write back (simple line-based edits)
            if not os.path.exists(desktop_path):
                return
            with open(desktop_path, "r", encoding="utf-8") as f:
                lines = f.read().splitlines()
            def upsert(key: str, val: str) -> None:
                nonlocal lines
                prefix = key + "="
                for i, line in enumerate(lines):
                    if line.startswith(prefix):
                        lines[i] = prefix + val
                        break
                else:
                    lines.append(prefix + val)
            upsert("X-GNOME-Autostart-enabled", "true")
            upsert("Hidden", "false")
            with open(desktop_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except Exception:
            logger.exception("Failed to ensure autostart flags on %s", desktop_path)

    def _ensure_exec_delay(self, desktop_path: str) -> None:
        """Ensure the Exec line includes the configured delay if any."""
        try:
            if not os.path.exists(desktop_path):
                return
            with open(desktop_path, "r", encoding="utf-8") as f:
                lines = f.read().splitlines()
            for i, line in enumerate(lines):
                if line.startswith("Exec="):
                    orig = line[len("Exec="):]
                    # Extract the actual command we want to run, ignoring previous wrapper
                    # If it already includes a sleep wrapper, do a simple replace.
                    if "/bin/sh -c" in orig or "bash -lc" in orig:
                        # naive approach: replace entire line with rebuilt wrapper using the tail command as-is
                        # Try to find the last ';' and take the right side as command, else keep as-is
                        cmd = orig
                    else:
                        cmd = orig
                    wrapped = self._wrap_with_delay(cmd)
                    lines[i] = "Exec=" + wrapped
                    break
            with open(desktop_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except Exception:
            logger.exception("Failed to ensure Exec delay on %s", desktop_path)

    def _wrap_with_delay(self, cmd: str) -> str:
        delay = max(0, int(getattr(self._cfg, "start_delay_sec", 0)))
        if delay <= 0:
            return cmd
        # Use POSIX sh. To avoid fragile quoting, pass the full command as a separate
        # base64-encoded argument and decode it at runtime, then exec via sh -c.
        try:
            encoded = base64.b64encode(cmd.encode("utf-8")).decode("ascii")
        except Exception:
            # Fallback to no-delay if encoding somehow fails (very unlikely)
            return cmd
        # The single-quoted script does not interpolate 'encoded'; it is passed as $1.
        # We decode with base64 -d and exec it using a fresh 'sh -c'.
        return (
            f"/bin/sh -c 'sleep {delay}; CMD=$(printf %s \"$1\" | base64 -d); exec sh -c \"$CMD\"'"
            f" dummy {encoded}"
        )

    def run(self) -> None:
        logger.info("GTK main loop starting")
        Gtk.main()

    def quit(self) -> None:
        try:
            logger.info("Quitting application")
            Gtk.main_quit()
        finally:
            # First, stop the monitor gracefully so its BLE scanner can shut down in its finally block
            try:
                if self._monitor is not None and self._loop.is_running():
                    logger.debug("Stopping proximity monitor before shutting down loop")
                    fut = asyncio.run_coroutine_threadsafe(self._monitor.stop_async(), self._loop)
                    # Wait a moment for clean shutdown
                    fut.result(timeout=5.0)
            except Exception:
                logger.exception("Error while stopping proximity monitor")

            if self._loop.is_running():
                logger.debug("Stopping asyncio loop")
                self._loop.call_soon_threadsafe(self._loop.stop)
            if self._loop_thread.is_alive():
                self._loop_thread.join(timeout=1.0)

    # --- Unlock monitor (GNOME) ---
    def _init_unlock_monitor(self) -> None:
        """Subscribe to GNOME ScreenSaver ActiveChanged to detect unlock events.
        When Active changes to false, the session is unlocked.
        """
        try:
            self._session_bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception:
            self._session_bus = None
            raise

        if not self._session_bus:
            return

        def _on_screensaver_signal(_conn, _sender_name, _object_path, _interface_name, _signal_name, parameters):
            try:
                if _signal_name == "ActiveChanged":
                    # parameters: (b: active)
                    active = parameters.get_child_value(0).get_boolean()
                    if not active:
                        # Unlocked
                        self._last_unlock_ts = time.time()
                        logger.info("Unlock detected via ScreenSaver ActiveChanged")
            except Exception:
                logger.exception("Failed handling ScreenSaver signal")

        # Subscribe to org.gnome.ScreenSaver ActiveChanged on the session bus
        try:
            self._session_bus.signal_subscribe(
                sender="org.gnome.ScreenSaver",
                interface_name="org.gnome.ScreenSaver",
                member="ActiveChanged",
                object_path="/org/gnome/ScreenSaver",
                arg0=None,
                flags=Gio.DBusSignalFlags.NONE,
                callback=_on_screensaver_signal,
            )
            logger.debug("Subscribed to org.gnome.ScreenSaver ActiveChanged")
        except Exception:
            logger.exception("Could not subscribe to ScreenSaver signals")

        # Fallback: org.freedesktop.ScreenSaver ActiveChanged
        try:
            self._session_bus.signal_subscribe(
                sender="org.freedesktop.ScreenSaver",
                interface_name="org.freedesktop.ScreenSaver",
                member="ActiveChanged",
                object_path="/org/freedesktop/ScreenSaver",
                arg0=None,
                flags=Gio.DBusSignalFlags.NONE,
                callback=_on_screensaver_signal,
            )
            logger.debug("Subscribed to org.freedesktop.ScreenSaver ActiveChanged")
        except Exception:
            logger.exception("Could not subscribe to freedesktop ScreenSaver signals")

        # Fallback: systemd-logind (org.freedesktop.login1) on the SYSTEM bus
        # Detect unlock via org.freedesktop.login1.Session LockedHint property changes.
        try:
            self._system_bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        except Exception:
            self._system_bus = None
            logger.exception("Could not connect to system bus for login1 monitoring")
            return

        if not self._system_bus:
            return

        # Resolve this process's session object path via GetSessionByPID
        try:
            res = self._system_bus.call_sync(
                "org.freedesktop.login1",
                "/org/freedesktop/login1",
                "org.freedesktop.login1.Manager",
                "GetSessionByPID",
                GLib.Variant("(u)", (os.getpid(),)),
                None,
                Gio.DBusCallFlags.NONE,
                -1,
                None,
            )
            session_path = res.unpack()[0]
        except Exception:
            session_path = None
            logger.exception("login1 GetSessionByPID failed; skipping login1 unlock monitoring")

        if session_path:
            def _on_login1_properties(_conn, _sender, _object_path, _interface, _member, parameters):
                try:
                    # parameters: (s a{sv} as)
                    iface_name, changed, _invalidated = parameters.unpack()
                    if iface_name == "org.freedesktop.login1.Session":
                        if "LockedHint" in changed:
                            locked = changed["LockedHint"].unpack()
                            if locked is False:
                                self._last_unlock_ts = time.time()
                                logger.info("Unlock detected via login1 LockedHint=false")
                except Exception:
                    logger.exception("Failed handling login1 PropertiesChanged")

            try:
                self._system_bus.signal_subscribe(
                    sender="org.freedesktop.login1",
                    interface_name="org.freedesktop.DBus.Properties",
                    member="PropertiesChanged",
                    object_path=session_path,
                    arg0=None,
                    flags=Gio.DBusSignalFlags.NONE,
                    callback=_on_login1_properties,
                )
                logger.debug("Subscribed to login1 PropertiesChanged for session %s", session_path)
            except Exception:
                logger.exception("Could not subscribe to login1 PropertiesChanged")
