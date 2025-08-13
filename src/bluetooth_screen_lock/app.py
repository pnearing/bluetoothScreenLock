import asyncio
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

        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(target=self._run_loop, daemon=True)
        self._loop_thread.start()

        self._monitor: Optional[ProximityMonitor] = None
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
        )
        self._monitor = ProximityMonitor(
            config=mon_cfg,
            on_away=self._lock_screen,
            on_near=lambda rssi: self._indicator.set_status(f"RSSI {rssi} dBm"),
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

    def _open_settings(self) -> None:
        logger.debug("Opening settings window")
        initial = SettingsResult(
            device_mac=self._cfg.device_mac,
            device_name=self._cfg.device_name,
            rssi_threshold=self._cfg.rssi_threshold,
            grace_period_sec=self._cfg.grace_period_sec,
        )
        win = SettingsWindow(initial)
        win.set_transient_for(None)
        win.connect("destroy", lambda _w: None)
        win.show_all()

        def on_hide(_w):
            result = win.get_result()
            logger.info("Settings saved: device=%s name=%s rssi=%s grace=%s", 
                        result.device_mac, result.device_name, result.rssi_threshold, result.grace_period_sec)
            self._cfg.device_mac = result.device_mac
            self._cfg.device_name = result.device_name
            self._cfg.rssi_threshold = result.rssi_threshold
            self._cfg.grace_period_sec = result.grace_period_sec
            save_config(self._cfg)
            self._indicator.set_status("Monitoring" if result.device_mac else "Idle")
            if self._monitor and result.device_mac:
                mon_cfg = MonitorConfig(
                    device_mac=result.device_mac,
                    rssi_threshold=result.rssi_threshold,
                    grace_period_sec=result.grace_period_sec,
                )
                self._monitor.update_config(mon_cfg)
            elif result.device_mac and not self._monitor:
                self._ensure_monitor()

        win.connect("hide", on_hide)

    def run(self) -> None:
        logger.info("GTK main loop starting")
        Gtk.main()

    def quit(self) -> None:
        try:
            logger.info("Quitting application")
            Gtk.main_quit()
        finally:
            if self._loop.is_running():
                logger.debug("Stopping asyncio loop")
                self._loop.call_soon_threadsafe(self._loop.stop)
            if self._loop_thread.is_alive():
                self._loop_thread.join(timeout=1.0)
