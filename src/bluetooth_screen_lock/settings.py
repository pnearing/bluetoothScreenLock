import asyncio
import logging
from typing import Optional, List, Tuple

from dataclasses import dataclass

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

from bleak import BleakScanner

logger = logging.getLogger(__name__)


@dataclass
class SettingsResult:
    device_mac: Optional[str]
    device_name: Optional[str]
    rssi_threshold: int
    grace_period_sec: int
    autostart: bool
    start_delay_sec: int
    near_command: Optional[str] = None
    hysteresis_db: int = 5
    stale_after_sec: int = 6


class SettingsWindow(Gtk.Window):
    def __init__(self, initial: SettingsResult) -> None:
        super().__init__(title="Bluetooth Screen Lock Settings")
        self.set_default_size(460, 360)
        self.set_border_width(12)
        logger.debug("SettingsWindow created with initial: mac=%s name=%s rssi=%s grace=%s",
                     initial.device_mac, initial.device_name, initial.rssi_threshold, initial.grace_period_sec)

        self._device_list: List[Tuple[str, str]] = []  # (name, mac)
        self._selected_mac: Optional[str] = initial.device_mac
        self._selected_name: Optional[str] = initial.device_name
        self._autostart: bool = initial.autostart

        # RSSI monitor state
        self._rssi_monitor_running: bool = False
        self._rssi_monitor_thread = None

        grid = Gtk.Grid(column_spacing=10, row_spacing=10)
        self.add(grid)

        # Device selection
        lbl_device = Gtk.Label(label="Device:")
        lbl_device.set_xalign(0)
        lbl_device.set_tooltip_text(
            "Select the phone/device to monitor."
            " If your phone uses randomized MACs, pair it first or keep the screen on while scanning."
        )
        grid.attach(lbl_device, 0, 0, 1, 1)

        self.cmb_devices = Gtk.ComboBoxText()
        self.cmb_devices.set_hexpand(True)
        self.cmb_devices.set_tooltip_text(
            "Choose the target device. Shown as Name (MAC)."
            " If empty, click Scan Devices."
        )
        grid.attach(self.cmb_devices, 1, 0, 2, 1)
        self.cmb_devices.connect("changed", self._on_device_changed)

        btn_scan = Gtk.Button(label="Scan Devices")
        btn_scan.connect("clicked", self._on_scan)
        btn_scan.set_tooltip_text(
            "Search for nearby BLE devices."
            " Ensure Bluetooth is on and the device is advertising (screen on)."
        )
        grid.attach(btn_scan, 3, 0, 1, 1)

        # Current RSSI display
        lbl_rssi_cur_title = Gtk.Label(label="Current RSSI:")
        lbl_rssi_cur_title.set_xalign(0)
        lbl_rssi_cur_title.set_tooltip_text(
            "Live RSSI for the selected device (in dBm)."
        )
        grid.attach(lbl_rssi_cur_title, 0, 1, 1, 1)

        self.lbl_rssi_current = Gtk.Label(label="— dBm")
        self.lbl_rssi_current.set_xalign(0)
        self.lbl_rssi_current.set_tooltip_text(
            "Live measured signal strength: closer = higher (e.g., -50), farther = lower (e.g., -90)."
        )
        grid.attach(self.lbl_rssi_current, 1, 1, 3, 1)

        # RSSI threshold
        lbl_rssi = Gtk.Label(label="RSSI threshold (dBm):")
        lbl_rssi.set_xalign(0)
        lbl_rssi.set_tooltip_text(
            "Received Signal Strength Indicator. Values are negative dBm:"
            " closer = higher (e.g., -50), farther = lower (e.g., -90)."
            " Screen locks when RSSI stays below this threshold for the grace period."
        )
        grid.attach(lbl_rssi, 0, 2, 2, 1)

        adjustment_rssi = Gtk.Adjustment(value=initial.rssi_threshold, lower=-100, upper=-30, step_increment=1)
        self.spn_rssi = Gtk.SpinButton()
        self.spn_rssi.set_adjustment(adjustment_rssi)
        self.spn_rssi.set_digits(0)
        self.spn_rssi.set_tooltip_text(
            "Typical range: -90 (far) to -50 (near)."
            " Choose a threshold like -75 dBm for conservative locking."
        )
        grid.attach(self.spn_rssi, 2, 2, 2, 1)

        # Grace period
        lbl_grace = Gtk.Label(label="Grace period (sec):")
        lbl_grace.set_xalign(0)
        lbl_grace.set_tooltip_text(
            "How long RSSI must stay below the threshold (or device unseen)"
            " before the screen locks. Helps avoid brief signal dips."
        )
        grid.attach(lbl_grace, 0, 3, 2, 1)

        adjustment_grace = Gtk.Adjustment(value=initial.grace_period_sec, lower=1, upper=60, step_increment=1)
        self.spn_grace = Gtk.SpinButton()
        self.spn_grace.set_adjustment(adjustment_grace)
        self.spn_grace.set_digits(0)
        self.spn_grace.set_tooltip_text(
            "Seconds to tolerate weak/no signal before locking (e.g., 8 seconds)."
        )
        grid.attach(self.spn_grace, 2, 3, 2, 1)

        # Autostart at login
        self.chk_autostart = Gtk.CheckButton.new_with_label("Start at login")
        self.chk_autostart.set_tooltip_text(
            "Enable to launch Bluetooth Screen Lock automatically when you sign in."
        )
        self.chk_autostart.set_active(initial.autostart)
        grid.attach(self.chk_autostart, 0, 4, 4, 1)

        # Autostart delay
        lbl_delay = Gtk.Label(label="Start delay (sec):")
        lbl_delay.set_xalign(0)
        lbl_delay.set_tooltip_text(
            "Delay after login before starting the app."
        )
        grid.attach(lbl_delay, 0, 5, 2, 1)

        adjustment_delay = Gtk.Adjustment(value=max(0, int(getattr(initial, 'start_delay_sec', 0))), lower=0, upper=600, step_increment=1)
        self.spn_delay = Gtk.SpinButton()
        self.spn_delay.set_adjustment(adjustment_delay)
        self.spn_delay.set_digits(0)
        self.spn_delay.set_tooltip_text("0 for no delay. Typical values: 5–30 seconds.")
        self.spn_delay.set_sensitive(self.chk_autostart.get_active())
        grid.attach(self.spn_delay, 2, 5, 2, 1)

        def _toggle_delay(_btn: Gtk.CheckButton) -> None:
            self.spn_delay.set_sensitive(_btn.get_active())
        self.chk_autostart.connect("toggled", _toggle_delay)

        # Hysteresis
        lbl_hyst = Gtk.Label(label="Hysteresis (dB):")
        lbl_hyst.set_xalign(0)
        lbl_hyst.set_tooltip_text(
            "Extra dB above the threshold required to consider the device NEAR.\n"
            "This reduces flapping near the boundary. Typical: 3–8 dB."
        )
        grid.attach(lbl_hyst, 0, 6, 2, 1)

        adjustment_hyst = Gtk.Adjustment(value=max(0, int(getattr(initial, 'hysteresis_db', 5))), lower=0, upper=20, step_increment=1)
        self.spn_hyst = Gtk.SpinButton()
        self.spn_hyst.set_adjustment(adjustment_hyst)
        self.spn_hyst.set_digits(0)
        self.spn_hyst.set_tooltip_text("Extra dB to require for 'near'. 0 disables hysteresis.")
        grid.attach(self.spn_hyst, 2, 6, 2, 1)

        # Stale RSSI timeout
        lbl_stale = Gtk.Label(label="Stale RSSI timeout (sec):")
        lbl_stale.set_xalign(0)
        lbl_stale.set_tooltip_text(
            "If the device isn't detected for this many seconds, treat RSSI as unknown.\n"
            "Prevents stale high RSSI from blocking 'away'."
        )
        grid.attach(lbl_stale, 0, 7, 2, 1)

        adjustment_stale = Gtk.Adjustment(value=max(1, int(getattr(initial, 'stale_after_sec', 6))), lower=1, upper=60, step_increment=1)
        self.spn_stale = Gtk.SpinButton()
        self.spn_stale.set_adjustment(adjustment_stale)
        self.spn_stale.set_digits(0)
        self.spn_stale.set_tooltip_text("Seconds before RSSI is considered stale/unknown.")
        grid.attach(self.spn_stale, 2, 7, 2, 1)

        # Near command
        lbl_near_cmd = Gtk.Label(label="Command when device is near:")
        lbl_near_cmd.set_xalign(0)
        lbl_near_cmd.set_tooltip_text(
            "Optional shell command to run once when the device becomes NEAR (RSSI above threshold + hysteresis).\n"
            "Examples: 'gnome-screensaver-command -d' or a custom script path."
        )
        grid.attach(lbl_near_cmd, 0, 8, 2, 1)

        self.txt_near_cmd = Gtk.Entry()
        self.txt_near_cmd.set_placeholder_text("e.g., gnome-screensaver-command -d")
        self.txt_near_cmd.set_hexpand(True)
        grid.attach(self.txt_near_cmd, 2, 8, 2, 1)

        # Buttons
        btn_box = Gtk.Box(spacing=10)
        btn_box.set_halign(Gtk.Align.END)
        grid.attach(btn_box, 0, 9, 4, 1)

        btn_cancel = Gtk.Button(label="Cancel")
        btn_cancel.connect("clicked", lambda _b: self.close())
        btn_box.pack_start(btn_cancel, False, False, 0)

        btn_save = Gtk.Button(label="Save")
        btn_save.get_style_context().add_class("suggested-action")
        btn_save.connect("clicked", self._on_save)
        btn_box.pack_start(btn_save, False, False, 0)

        self._populate_initial(initial)

        # start RSSI monitor if we already have a selected device
        if self._selected_mac:
            self._start_rssi_monitor(self._selected_mac)

        # ensure monitor stops when window closes
        self.connect("destroy", lambda *_: self._stop_rssi_monitor())

    def _populate_initial(self, initial: SettingsResult) -> None:
        # If we already have a selected device, add it
        if initial.device_mac:
            name = initial.device_name or initial.device_mac
            # Ensure internal list is in sync so 'changed' handler sees a valid index
            self._device_list = [(name, initial.device_mac)]
            self.cmb_devices.append_text(f"{name} ({initial.device_mac})")
            self.cmb_devices.set_active(0)
        logger.debug("Initial device populated: %s", initial.device_mac)
        # Set initial near command
        try:
            self.txt_near_cmd.set_text(getattr(initial, 'near_command', None) or "")
        except Exception:
            logger.exception("Failed to set initial near command")

    def _on_device_changed(self, _cmb: Gtk.ComboBoxText) -> None:
        idx = self.cmb_devices.get_active()
        if idx < 0 or idx >= len(self._device_list):
            # If invalid selection, stop monitor and clear label
            self._selected_mac = None
            self._selected_name = None
            self._stop_rssi_monitor()
            self._set_rssi_label(None)
            return
        name, mac = self._device_list[idx]
        self._selected_mac = mac
        self._selected_name = name
        logger.debug("Device selection changed: %s (%s)", name, mac)
        # restart RSSI monitor for new device
        self._stop_rssi_monitor()
        if mac:
            self._start_rssi_monitor(mac)

    def _on_save(self, _btn: Gtk.Button) -> None:
        logger.info("Settings save requested")
        self.hide()

    def get_result(self) -> SettingsResult:
        return SettingsResult(
            device_mac=self._selected_mac,
            device_name=self._selected_name,
            rssi_threshold=int(self.spn_rssi.get_value()),
            grace_period_sec=int(self.spn_grace.get_value()),
            autostart=bool(self.chk_autostart.get_active()),
            start_delay_sec=int(self.spn_delay.get_value()),
            near_command=(self.txt_near_cmd.get_text() or None),
            hysteresis_db=int(self.spn_hyst.get_value()),
            stale_after_sec=int(self.spn_stale.get_value()),
        )

    def _on_scan(self, _btn: Gtk.Button) -> None:
        self.set_sensitive(False)
        # pause RSSI monitor during scan to avoid adapter contention
        self._stop_rssi_monitor()

        async def do_scan() -> None:
            try:
                logger.info("Starting BLE scan for devices")
                devices = await BleakScanner.discover(timeout=5.0)
                self._device_list = []
                self.cmb_devices.remove_all()
                for d in devices:
                    name = d.name or "(unknown)"
                    mac = d.address or ""
                    if mac:
                        self._device_list.append((name, mac))
                        self.cmb_devices.append_text(f"{name} ({mac})")
                if self._device_list:
                    self.cmb_devices.set_active(0)
                logger.info("Scan complete: %d device(s) found", len(self._device_list))
            except Exception:
                logger.exception("Device scan failed")
            finally:
                def _after():
                    self.set_sensitive(True)
                    # restart RSSI monitor for current selection
                    if self._selected_mac:
                        self._start_rssi_monitor(self._selected_mac)
                GLib.idle_add(_after)

        # run scan in background thread with its own loop to avoid blocking GTK loop
        import threading

        def run_asyncio() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(do_scan())
            loop.close()

        threading.Thread(target=run_asyncio, daemon=True).start()

    # --- RSSI live monitor helpers ---
    def _set_rssi_label(self, rssi: Optional[int]) -> None:
        try:
            if rssi is None:
                self.lbl_rssi_current.set_text("— dBm")
            else:
                self.lbl_rssi_current.set_text(f"{int(rssi)} dBm")
        except Exception:
            logger.exception("Failed to update RSSI label")

    def _start_rssi_monitor(self, mac: str) -> None:
        if self._rssi_monitor_running:
            return
        if not mac:
            return

        self._rssi_monitor_running = True

        import threading

        def _thread() -> None:
            async def _run() -> None:
                try:
                    scanner = BleakScanner()

                    last_update = 0.0

                    def on_detect(device, advertisement_data):
                        try:
                            if (device.address or "").upper() != mac.upper():
                                return
                            rssi_val = getattr(advertisement_data, "rssi", None)
                            if rssi_val is None:
                                rssi_val = getattr(device, "rssi", None)
                            if rssi_val is not None:
                                # rate-limit label updates to ~2 Hz
                                nonlocal last_update
                                now = asyncio.get_event_loop().time()
                                if now - last_update >= 0.5:
                                    last_update = now
                                    GLib.idle_add(lambda: self._set_rssi_label(int(rssi_val)))
                        except Exception:
                            logger.exception("RSSI detect callback error")

                    scanner.register_detection_callback(on_detect)
                    await scanner.start()
                    try:
                        while self._rssi_monitor_running:
                            await asyncio.sleep(1.0)
                    finally:
                        await scanner.stop()
                except Exception:
                    logger.exception("RSSI monitor failed")
                finally:
                    GLib.idle_add(lambda: self._set_rssi_label(None))

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(_run())
            loop.close()

        self._rssi_monitor_thread = threading.Thread(target=_thread, daemon=True)
        self._rssi_monitor_thread.start()

    def _stop_rssi_monitor(self) -> None:
        if not self._rssi_monitor_running:
            return
        self._rssi_monitor_running = False
        # thread will exit on next loop iteration; no join to avoid blocking UI
