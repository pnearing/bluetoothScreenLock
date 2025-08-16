import asyncio
import os
import shutil
import subprocess
import threading
import logging
from typing import Optional

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

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
        self._ensure_monitor()

        self._indicator.set_status("Idle" if not self._cfg.device_mac else "Monitoring")
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
            rssi_threshold=self._cfg.rssi_threshold,
            grace_period_sec=self._cfg.grace_period_sec,
            hysteresis_db=getattr(self._cfg, 'hysteresis_db', 5),
            stale_after_sec=getattr(self._cfg, 'stale_after_sec', 6),
        )
        def on_near(rssi: int) -> None:
            try:
                # Update tray status with latest RSSI
                self._indicator.set_status(f"RSSI {rssi} dBm")
                # Determine near condition: RSSI above threshold
                is_near = rssi > self._cfg.rssi_threshold
                if is_near and not self._was_near:
                    # Transitioned to NEAR
                    self._run_near_command()
                self._was_near = is_near
            except Exception:
                logger.exception("on_near handling failed")

        def on_away() -> None:
            # Reset near state and lock
            self._was_near = False
            self._lock_screen()

        self._monitor = ProximityMonitor(
            config=mon_cfg,
            on_away=on_away,
            on_near=on_near,
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

    def _lock_screen(self) -> None:
        # Lock screen via systemd-logind (works on GNOME)
        try:
            logger.warning("Away detected; locking screen via loginctl")
            subprocess.run(["loginctl", "lock-session"], check=False)
            GLib.idle_add(lambda: self._indicator.set_status("Locked (away)"))
        except Exception:
            logger.exception("Failed to lock screen")

    def _run_near_command(self) -> None:
        try:
            cmd = (self._cfg.near_command or "").strip()
            if not cmd:
                return
            logger.info("Running near command: %s", cmd)
            # Run in background, do not wait. Use shell to support complex commands.
            subprocess.Popen(cmd, shell=True)
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
            hysteresis_db=getattr(self._cfg, 'hysteresis_db', 5),
            stale_after_sec=getattr(self._cfg, 'stale_after_sec', 6),
        )
        win = SettingsWindow(initial)
        win.set_transient_for(None)
        win.connect("destroy", lambda _w: None)
        win.show_all()

        def on_hide(_w):
            result = win.get_result()
            logger.info("Settings saved: device=%s name=%s rssi=%s grace=%s, near_command=%s", 
                        result.device_mac, result.device_name, result.rssi_threshold, result.grace_period_sec, result.near_command)
            self._cfg.device_mac = result.device_mac
            self._cfg.device_name = result.device_name
            self._cfg.rssi_threshold = result.rssi_threshold
            self._cfg.grace_period_sec = result.grace_period_sec
            self._cfg.near_command = getattr(result, 'near_command', None)
            self._cfg.hysteresis_db = int(getattr(result, 'hysteresis_db', getattr(self._cfg, 'hysteresis_db', 5)))
            self._cfg.stale_after_sec = int(getattr(result, 'stale_after_sec', getattr(self._cfg, 'stale_after_sec', 6)))
            
            # Handle autostart toggle or delay change
            autostart_changed = (self._cfg.autostart != result.autostart)
            delay_changed = (self._cfg.start_delay_sec != result.start_delay_sec)
            self._cfg.autostart = result.autostart
            self._cfg.start_delay_sec = max(0, int(result.start_delay_sec))
            if autostart_changed or (self._cfg.autostart and delay_changed):
                self._apply_autostart(self._cfg.autostart)
            save_config(self._cfg)
            self._indicator.set_status("Monitoring" if result.device_mac else "Idle")
            if self._monitor and result.device_mac:
                mon_cfg = MonitorConfig(
                    device_mac=result.device_mac,
                    rssi_threshold=result.rssi_threshold,
                    grace_period_sec=result.grace_period_sec,
                    hysteresis_db=int(getattr(result, 'hysteresis_db', getattr(self._cfg, 'hysteresis_db', 5))),
                    stale_after_sec=int(getattr(result, 'stale_after_sec', getattr(self._cfg, 'stale_after_sec', 6))),
                )
                self._monitor.update_config(mon_cfg)
            elif result.device_mac and not self._monitor:
                self._ensure_monitor()

        win.connect("hide", on_hide)

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
                        # fall back to running via python and project path
                        project_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
                        run_path = os.path.join(project_dir, "run.py")
                        exec_cmd = f"python3 {run_path}"
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
        # Use POSIX sh to avoid dependency on bash
        # Quote the command safely
        return f"/bin/sh -c 'sleep {delay}; exec {cmd}'"

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
