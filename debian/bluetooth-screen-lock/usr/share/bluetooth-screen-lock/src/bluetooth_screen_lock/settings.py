import asyncio
import logging
from typing import Optional, List, Tuple

from dataclasses import dataclass

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

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

        # RSSI threshold
        lbl_rssi = Gtk.Label(label="RSSI threshold (dBm):")
        lbl_rssi.set_xalign(0)
        lbl_rssi.set_tooltip_text(
            "Received Signal Strength Indicator. Values are negative dBm:"
            " closer = higher (e.g., -50), farther = lower (e.g., -90)."
            " Screen locks when RSSI stays below this threshold for the grace period."
        )
        grid.attach(lbl_rssi, 0, 1, 2, 1)

        adjustment_rssi = Gtk.Adjustment(value=initial.rssi_threshold, lower=-100, upper=-30, step_increment=1)
        self.spn_rssi = Gtk.SpinButton()
        self.spn_rssi.set_adjustment(adjustment_rssi)
        self.spn_rssi.set_digits(0)
        self.spn_rssi.set_tooltip_text(
            "Typical range: -90 (far) to -50 (near)."
            " Choose a threshold like -75 dBm for conservative locking."
        )
        grid.attach(self.spn_rssi, 2, 1, 2, 1)

        # Grace period
        lbl_grace = Gtk.Label(label="Grace period (sec):")
        lbl_grace.set_xalign(0)
        lbl_grace.set_tooltip_text(
            "How long RSSI must stay below the threshold (or device unseen)"
            " before the screen locks. Helps avoid brief signal dips."
        )
        grid.attach(lbl_grace, 0, 2, 2, 1)

        adjustment_grace = Gtk.Adjustment(value=initial.grace_period_sec, lower=1, upper=60, step_increment=1)
        self.spn_grace = Gtk.SpinButton()
        self.spn_grace.set_adjustment(adjustment_grace)
        self.spn_grace.set_digits(0)
        self.spn_grace.set_tooltip_text(
            "Seconds to tolerate weak/no signal before locking (e.g., 8 seconds)."
        )
        grid.attach(self.spn_grace, 2, 2, 2, 1)

        # Autostart at login
        self.chk_autostart = Gtk.CheckButton.new_with_label("Start at login")
        self.chk_autostart.set_tooltip_text(
            "Enable to launch Bluetooth Screen Lock automatically when you sign in."
        )
        self.chk_autostart.set_active(initial.autostart)
        grid.attach(self.chk_autostart, 0, 3, 4, 1)

        # Autostart delay
        lbl_delay = Gtk.Label(label="Start delay (sec):")
        lbl_delay.set_xalign(0)
        lbl_delay.set_tooltip_text(
            "Delay after login before starting the app."
        )
        grid.attach(lbl_delay, 0, 4, 2, 1)

        adjustment_delay = Gtk.Adjustment(value=max(0, int(getattr(initial, 'start_delay_sec', 0))), lower=0, upper=600, step_increment=1)
        self.spn_delay = Gtk.SpinButton()
        self.spn_delay.set_adjustment(adjustment_delay)
        self.spn_delay.set_digits(0)
        self.spn_delay.set_tooltip_text("0 for no delay. Typical values: 5–30 seconds.")
        self.spn_delay.set_sensitive(self.chk_autostart.get_active())
        grid.attach(self.spn_delay, 2, 4, 2, 1)

        def _toggle_delay(_btn: Gtk.CheckButton) -> None:
            self.spn_delay.set_sensitive(_btn.get_active())
        self.chk_autostart.connect("toggled", _toggle_delay)

        # Buttons
        btn_box = Gtk.Box(spacing=10)
        btn_box.set_halign(Gtk.Align.END)
        grid.attach(btn_box, 0, 5, 4, 1)

        btn_cancel = Gtk.Button(label="Cancel")
        btn_cancel.connect("clicked", lambda _b: self.close())
        btn_box.pack_start(btn_cancel, False, False, 0)

        btn_save = Gtk.Button(label="Save")
        btn_save.get_style_context().add_class("suggested-action")
        btn_save.connect("clicked", self._on_save)
        btn_box.pack_start(btn_save, False, False, 0)

        self._populate_initial(initial)

    def _populate_initial(self, initial: SettingsResult) -> None:
        # If we already have a selected device, add it
        if initial.device_mac:
            name = initial.device_name or initial.device_mac
            self.cmb_devices.append_text(f"{name} ({initial.device_mac})")
            self.cmb_devices.set_active(0)
        logger.debug("Initial device populated: %s", initial.device_mac)

    def _on_device_changed(self, _cmb: Gtk.ComboBoxText) -> None:
        idx = self.cmb_devices.get_active()
        if idx < 0 or idx >= len(self._device_list):
            return
        name, mac = self._device_list[idx]
        self._selected_mac = mac
        self._selected_name = name
        logger.debug("Device selection changed: %s (%s)", name, mac)

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
        )

    def _on_scan(self, _btn: Gtk.Button) -> None:
        self.set_sensitive(False)

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
                GLib.idle_add(lambda: self.set_sensitive(True))

        # run scan in background thread with its own loop to avoid blocking GTK loop
        import threading
        from gi.repository import GLib  # local import for thread safety

        def run_asyncio() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(do_scan())
            loop.close()

        threading.Thread(target=run_asyncio, daemon=True).start()
