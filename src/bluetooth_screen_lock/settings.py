"""GTK settings window for configuring Bluetooth Screen Lock.

Provides device selection (with BLE scanning), live RSSI display to help pick
an `rssi_threshold`, and additional tuning options (grace period, hysteresis,
stale timeout, debounce, etc.).
"""

import asyncio
import logging
from typing import Optional, List, Tuple

from dataclasses import dataclass

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

from bleak import BleakScanner
from .config import default_log_path

logger = logging.getLogger(__name__)


@dataclass
class SettingsResult:
    """Value object returned from the settings dialog on save."""
    device_mac: Optional[str]
    device_name: Optional[str]
    rssi_threshold: int
    grace_period_sec: int
    autostart: bool
    start_delay_sec: int
    near_command: Optional[str] = None
    near_shell: bool = False
    hysteresis_db: int = 5
    stale_after_sec: int = 6
    re_lock_delay_sec: int = 0
    scan_interval_sec: float = 2.0
    near_consecutive_scans: int = 2
    file_logging_enabled: bool = False
    # New: require NEAR dwell before running near_command
    near_dwell_sec: int = 0
    # New: global rate limit for lock+unlock cycles
    cycle_rate_limit_min: int = 0
    # New: one-time warning flag for shell execution
    near_shell_warned: bool = False
    # New: timeout controls for near command
    near_timeout_sec: int = 0
    near_kill_grace_sec: int = 5


class SettingsWindow(Gtk.Window):
    """Preferences dialog for selecting a device and tuning proximity logic."""
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
        # Track whether we've shown the shell warning in this session
        self._near_shell_warned_session: bool = bool(getattr(initial, 'near_shell_warned', False))

        # RSSI monitor state
        self._rssi_monitor_running: bool = False
        self._rssi_monitor_thread = None

        # Root layout with a notebook to declutter settings
        root = Gtk.Grid(column_spacing=10, row_spacing=10)
        self.add(root)

        notebook = Gtk.Notebook()
        notebook.set_hexpand(True)
        notebook.set_vexpand(True)
        root.attach(notebook, 0, 0, 1, 1)

        gen_grid = Gtk.Grid(column_spacing=10, row_spacing=10, margin_bottom=6)
        near_grid = Gtk.Grid(column_spacing=10, row_spacing=10, margin_bottom=6)
        adv_grid = Gtk.Grid(column_spacing=10, row_spacing=10, margin_bottom=6)
        notebook.append_page(gen_grid, Gtk.Label(label="General"))
        notebook.append_page(near_grid, Gtk.Label(label="Near Command"))
        notebook.append_page(adv_grid, Gtk.Label(label="Advanced"))

        # Device selection
        lbl_device = Gtk.Label(label="Device:")
        lbl_device.set_xalign(0)
        lbl_device.set_tooltip_text(
            "Select the phone/device to monitor."
            " If your phone uses randomized MACs, pair it first or keep the screen on while scanning."
        )
        gen_grid.attach(lbl_device, 0, 0, 1, 1)

        self.cmb_devices = Gtk.ComboBoxText()
        self.cmb_devices.set_hexpand(True)
        self.cmb_devices.set_tooltip_text(
            "Choose the target device. Shown as Name (MAC)."
            " If empty, click Scan Devices."
        )
        gen_grid.attach(self.cmb_devices, 1, 0, 2, 1)
        self.cmb_devices.connect("changed", self._on_device_changed)

        btn_scan = Gtk.Button(label="Scan Devices")
        btn_scan.connect("clicked", self._on_scan)
        btn_scan.set_tooltip_text(
            "Search for nearby BLE devices."
            " Ensure Bluetooth is on and the device is advertising (screen on)."
        )
        gen_grid.attach(btn_scan, 3, 0, 1, 1)

        # Inline warning for name-only matching fallback
        self.lbl_name_fallback = Gtk.Label()
        self.lbl_name_fallback.set_xalign(0)
        self.lbl_name_fallback.set_line_wrap(True)
        self.lbl_name_fallback.set_max_width_chars(60)
        # Slightly deemphasized style; visibility toggled dynamically
        try:
            self.lbl_name_fallback.get_style_context().add_class("dim-label")
        except Exception:
            pass
        gen_grid.attach(self.lbl_name_fallback, 0, 1, 4, 1)

        # Current RSSI display
        lbl_rssi_cur_title = Gtk.Label(label="Current RSSI:")
        lbl_rssi_cur_title.set_xalign(0)
        lbl_rssi_cur_title.set_tooltip_text(
            "Live RSSI for the selected device (in dBm)."
        )
        gen_grid.attach(lbl_rssi_cur_title, 0, 2, 1, 1)

        self.lbl_rssi_current = Gtk.Label(label="— dBm")
        self.lbl_rssi_current.set_xalign(0)
        self.lbl_rssi_current.set_tooltip_text(
            "Live measured signal strength: closer = higher (e.g., -50), farther = lower (e.g., -90)."
        )
        gen_grid.attach(self.lbl_rssi_current, 1, 2, 3, 1)

        # RSSI threshold
        lbl_rssi = Gtk.Label(label="RSSI threshold (dBm):")
        lbl_rssi.set_xalign(0)
        lbl_rssi.set_tooltip_text(
            "Received Signal Strength Indicator. Values are negative dBm:"
            " closer = higher (e.g., -50), farther = lower (e.g., -90)."
            " Screen locks when RSSI stays below this threshold for the grace period."
        )
        gen_grid.attach(lbl_rssi, 0, 3, 2, 1)

        adjustment_rssi = Gtk.Adjustment(value=initial.rssi_threshold, lower=-100, upper=-30, step_increment=1)
        self.spn_rssi = Gtk.SpinButton()
        self.spn_rssi.set_adjustment(adjustment_rssi)
        self.spn_rssi.set_digits(0)
        self.spn_rssi.set_tooltip_text(
            "Typical range: -90 (far) to -50 (near)."
            " Choose a threshold like -75 dBm for conservative locking."
        )
        gen_grid.attach(self.spn_rssi, 2, 3, 2, 1)

        # Grace period
        lbl_grace = Gtk.Label(label="Grace period (sec):")
        lbl_grace.set_xalign(0)
        lbl_grace.set_tooltip_text(
            "How long RSSI must stay below the threshold (or device unseen)"
            " before the screen locks. Helps avoid brief signal dips."
        )
        gen_grid.attach(lbl_grace, 0, 4, 2, 1)

        adjustment_grace = Gtk.Adjustment(value=initial.grace_period_sec, lower=1, upper=60, step_increment=1)
        self.spn_grace = Gtk.SpinButton()
        self.spn_grace.set_adjustment(adjustment_grace)
        self.spn_grace.set_digits(0)
        self.spn_grace.set_tooltip_text(
            "Seconds to tolerate weak/no signal before locking (e.g., 8 seconds)."
        )
        gen_grid.attach(self.spn_grace, 2, 4, 2, 1)

        # Autostart at login
        self.chk_autostart = Gtk.CheckButton.new_with_label("Start at login")
        self.chk_autostart.set_tooltip_text(
            "Enable to launch Bluetooth Screen Lock automatically when you sign in."
        )
        self.chk_autostart.set_active(initial.autostart)
        gen_grid.attach(self.chk_autostart, 0, 5, 4, 1)

        # Autostart delay
        lbl_delay = Gtk.Label(label="Start delay (sec):")
        lbl_delay.set_xalign(0)
        lbl_delay.set_tooltip_text(
            "Delay after login before starting the app."
        )
        gen_grid.attach(lbl_delay, 0, 6, 2, 1)

        adjustment_delay = Gtk.Adjustment(value=max(0, int(getattr(initial, 'start_delay_sec', 0))), lower=0, upper=600, step_increment=1)
        self.spn_delay = Gtk.SpinButton()
        self.spn_delay.set_adjustment(adjustment_delay)
        self.spn_delay.set_digits(0)
        self.spn_delay.set_tooltip_text("0 for no delay. Typical values: 5–30 seconds.")
        self.spn_delay.set_sensitive(self.chk_autostart.get_active())
        gen_grid.attach(self.spn_delay, 2, 6, 2, 1)

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
        adv_grid.attach(lbl_hyst, 0, 0, 2, 1)

        adjustment_hyst = Gtk.Adjustment(value=max(0, int(getattr(initial, 'hysteresis_db', 5))), lower=0, upper=20, step_increment=1)
        self.spn_hyst = Gtk.SpinButton()
        self.spn_hyst.set_adjustment(adjustment_hyst)
        self.spn_hyst.set_digits(0)
        self.spn_hyst.set_tooltip_text("Extra dB to require for 'near'. 0 disables hysteresis.")
        adv_grid.attach(self.spn_hyst, 2, 0, 2, 1)

        # Stale RSSI timeout
        lbl_stale = Gtk.Label(label="Stale RSSI timeout (sec):")
        lbl_stale.set_xalign(0)
        lbl_stale.set_tooltip_text(
            "If the device isn't detected for this many seconds, treat RSSI as unknown.\n"
            "Prevents stale high RSSI from blocking 'away'."
        )
        adv_grid.attach(lbl_stale, 0, 1, 2, 1)

        adjustment_stale = Gtk.Adjustment(value=max(1, int(getattr(initial, 'stale_after_sec', 6))), lower=1, upper=60, step_increment=1)
        self.spn_stale = Gtk.SpinButton()
        self.spn_stale.set_adjustment(adjustment_stale)
        self.spn_stale.set_digits(0)
        self.spn_stale.set_tooltip_text("Seconds before RSSI is considered stale/unknown.")
        adv_grid.attach(self.spn_stale, 2, 1, 2, 1)

        # Re-lock delay
        lbl_relock = Gtk.Label(label="Re-lock delay (sec):")
        lbl_relock.set_xalign(0)
        lbl_relock.set_tooltip_text(
            "Do not auto-lock for this many seconds after the device becomes NEAR (e.g., after unlocking)."
        )
        adv_grid.attach(lbl_relock, 0, 2, 2, 1)

        adjustment_relock = Gtk.Adjustment(value=max(0, int(getattr(initial, 're_lock_delay_sec', 0))), lower=0, upper=1800, step_increment=1)
        self.spn_relock = Gtk.SpinButton()
        self.spn_relock.set_adjustment(adjustment_relock)
        self.spn_relock.set_digits(0)
        self.spn_relock.set_tooltip_text("0 disables the cooldown. Typical: 10–120 seconds.")
        adv_grid.attach(self.spn_relock, 2, 2, 2, 1)

        # Scan interval
        lbl_scan = Gtk.Label(label="Scan interval (sec):")
        lbl_scan.set_xalign(0)
        lbl_scan.set_tooltip_text(
            "How often to poll for BLE advertisements.\n"
            "Shorter = more responsive, but higher Bluetooth/CPU activity.\n"
            "Longer = lower overhead, but slower to react."
        )
        # Advanced: scanning cadence
        adv_grid.attach(lbl_scan, 0, 4, 2, 1)

        adjustment_scan = Gtk.Adjustment(value=float(getattr(initial, 'scan_interval_sec', 2.0)), lower=1.0, upper=10.0, step_increment=0.1)
        self.spn_scan = Gtk.SpinButton()
        self.spn_scan.set_adjustment(adjustment_scan)
        self.spn_scan.set_digits(1)
        self.spn_scan.set_tooltip_text("Typical: 1.0–3.0s. Use smaller for faster detection, larger for efficiency.")
        adv_grid.attach(self.spn_scan, 2, 4, 2, 1)

        # Near dwell (seconds) — Near Command
        lbl_near_dwell = Gtk.Label(label="Near dwell (sec):")
        lbl_near_dwell.set_xalign(0)
        lbl_near_dwell.set_tooltip_text(
            "Minimum seconds the device must remain NEAR before the near command runs. 0 = immediate."
        )
        near_grid.attach(lbl_near_dwell, 0, 4, 2, 1)

        adjustment_near_dwell = Gtk.Adjustment(value=max(0, int(getattr(initial, 'near_dwell_sec', 0))), lower=0, upper=600, step_increment=1)
        self.spn_near_dwell = Gtk.SpinButton()
        self.spn_near_dwell.set_adjustment(adjustment_near_dwell)
        self.spn_near_dwell.set_digits(0)
        self.spn_near_dwell.set_tooltip_text("Seconds to stay NEAR before running near_command. 0 disables dwell.")
        near_grid.attach(self.spn_near_dwell, 2, 4, 2, 1)

        # Cycle rate limit (minutes) — Advanced
        lbl_cycle_rl = Gtk.Label(label="Cycle rate limit (min):")
        lbl_cycle_rl.set_xalign(0)
        lbl_cycle_rl.set_tooltip_text(
            "Global rate limit: allow at most one lock+unlock cycle per this many minutes. 0 = unlimited."
        )
        adv_grid.attach(lbl_cycle_rl, 0, 7, 2, 1)

        adjustment_cycle_rl = Gtk.Adjustment(value=max(0, int(getattr(initial, 'cycle_rate_limit_min', 0))), lower=0, upper=240, step_increment=1)
        self.spn_cycle_rl = Gtk.SpinButton()
        self.spn_cycle_rl.set_adjustment(adjustment_cycle_rl)
        self.spn_cycle_rl.set_digits(0)
        self.spn_cycle_rl.set_tooltip_text("0 disables; typical values: 1–10 minutes.")
        adv_grid.attach(self.spn_cycle_rl, 2, 7, 2, 1)

        # Near debounce (consecutive scans) — Advanced
        lbl_near_debounce = Gtk.Label(label="Near debounce (scans):")
        lbl_near_debounce.set_xalign(0)
        lbl_near_debounce.set_tooltip_text(
            "Require this many consecutive scans above the near trigger (threshold + hysteresis)\n"
            "before treating the device as NEAR. Mitigates brief spikes/spoofing."
        )
        adv_grid.attach(lbl_near_debounce, 0, 5, 2, 1)

        adjustment_near_debounce = Gtk.Adjustment(
            value=max(1, int(getattr(initial, 'near_consecutive_scans', 2))), lower=1, upper=10, step_increment=1
        )
        self.spn_near_debounce = Gtk.SpinButton()
        self.spn_near_debounce.set_adjustment(adjustment_near_debounce)
        self.spn_near_debounce.set_digits(0)
        self.spn_near_debounce.set_tooltip_text("1 = immediate; 2–3 recommended.")
        adv_grid.attach(self.spn_near_debounce, 2, 5, 2, 1)

        # Near Command tab headers
        hdr_exec = Gtk.Label()
        try:
            hdr_exec.set_markup("<b>Execution</b>")
        except Exception:
            hdr_exec.set_text("Execution")
        hdr_exec.set_xalign(0)
        near_grid.attach(hdr_exec, 0, 0, 4, 1)

        # Near command — Near Command
        lbl_near_cmd = Gtk.Label(label="Command when device is near:")
        lbl_near_cmd.set_xalign(0)
        lbl_near_cmd.set_tooltip_text(
            "Optional shell command to run once when the device becomes NEAR (RSSI above threshold + hysteresis).\n"
            "Examples: 'gnome-screensaver-command -d' or a custom script path."
        )
        near_grid.attach(lbl_near_cmd, 0, 1, 2, 1)

        self.txt_near_cmd = Gtk.Entry()
        self.txt_near_cmd.set_placeholder_text("e.g., gnome-screensaver-command -d")
        self.txt_near_cmd.set_hexpand(True)
        near_grid.attach(self.txt_near_cmd, 2, 1, 2, 1)

        # Near command shell checkbox — Near Command
        self.chk_near_shell = Gtk.CheckButton.new_with_label("Run command in shell (advanced)")
        self.chk_near_shell.set_tooltip_text(
            "If enabled, executes the command via the system shell. This allows pipes, redirection, etc.,\n"
            "but is less safe. Only enable if you trust the command and understand the risks."
        )
        self.chk_near_shell.set_active(bool(getattr(initial, 'near_shell', False)))
        # Place checkbox in Near Command tab
        near_grid.attach(self.chk_near_shell, 2, 2, 1, 1)

        # Shared dialog used by both the toggle handler and the Learn more button
        def _show_near_shell_warning() -> None:
            dlg = Gtk.MessageDialog(
                transient_for=self,
                modal=True,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK,
                text="Shell execution enabled for near command",
            )
            dlg.format_secondary_text(
                "Running the near command via a shell can be risky (expands metacharacters, pipes, etc.).\n"
                "Safer alternative: disable 'Run command in shell' and provide an absolute path to an executable.\n\n"
                "When shell mode is used, this app runs: /bin/sh -c '<your command>' and sets PATH to '/usr/bin:/bin'.\n"
                "This reduces ambiguity but still allows shell features. Enable only if you trust the command."
            )
            try:
                dlg.run()
            finally:
                dlg.destroy()

        # Immediate one-time warning on toggle to ON
        def _on_near_shell_toggled(btn: Gtk.CheckButton) -> None:
            try:
                if btn.get_active() and not self._near_shell_warned_session:
                    _show_near_shell_warning()
                    self._near_shell_warned_session = True
            except Exception:
                logger.exception("Failed to show near_shell toggle warning")

        self.chk_near_shell.connect("toggled", _on_near_shell_toggled)

        # Learn more button next to the checkbox (left column of the same row)
        btn_learn = Gtk.Button()
        # Always show text; add icon if available
        btn_learn.set_label("Learn more")
        try:
            img = Gtk.Image.new_from_icon_name("dialog-information", Gtk.IconSize.BUTTON)
            btn_learn.set_image(img)
            try:
                btn_learn.set_always_show_image(True)
            except Exception:
                pass
        except Exception:
            # If icon resolution fails, we still have the text label
            pass
        btn_learn.set_tooltip_text("Learn more about shell execution risks and PATH used")
        def _on_learn_clicked(_btn: Gtk.Button) -> None:
            try:
                _show_near_shell_warning()
            except Exception:
                logger.exception("Failed to show 'Learn more' dialog")
        btn_learn.connect("clicked", _on_learn_clicked)
        # Place Learn more button to the right of the checkbox in Near Command tab
        near_grid.attach(btn_learn, 3, 2, 1, 1)

        # File logging toggle (Advanced)
        self.chk_file_logging = Gtk.CheckButton.new_with_label("Write log file")
        self.chk_file_logging.set_tooltip_text(
            "Enable writing logs to a rotating file in addition to stdout/stderr."
        )
        self.chk_file_logging.set_active(bool(getattr(initial, 'file_logging_enabled', False)))
        adv_grid.attach(self.chk_file_logging, 0, 11, 2, 1)

        # Timing header and controls
        hdr_timing = Gtk.Label()
        try:
            hdr_timing.set_markup("<b>Timing</b>")
        except Exception:
            hdr_timing.set_text("Timing")
        hdr_timing.set_xalign(0)
        near_grid.attach(hdr_timing, 0, 3, 4, 1)

        # Near command timeout (seconds) — Near Command
        lbl_timeout = Gtk.Label(label="Near command timeout (sec):")
        lbl_timeout.set_xalign(0)
        lbl_timeout.set_tooltip_text(
            "Maximum time to allow the near command to run. 0 disables the timeout."
        )
        near_grid.attach(lbl_timeout, 0, 5, 2, 1)

        adjustment_timeout = Gtk.Adjustment(value=max(0, int(getattr(initial, 'near_timeout_sec', 0))), lower=0, upper=3600, step_increment=1)
        self.spn_timeout = Gtk.SpinButton()
        self.spn_timeout.set_adjustment(adjustment_timeout)
        self.spn_timeout.set_digits(0)
        self.spn_timeout.set_tooltip_text("0 = no timeout. Typical values: 5–30 seconds.")
        near_grid.attach(self.spn_timeout, 2, 5, 2, 1)

        # Near command kill grace (seconds) — Near Command
        lbl_kill_grace = Gtk.Label(label="Near command kill grace (sec):")
        lbl_kill_grace.set_xalign(0)
        lbl_kill_grace.set_tooltip_text(
            "After timeout, the app sends SIGTERM and waits this long before SIGKILL."
        )
        near_grid.attach(lbl_kill_grace, 0, 6, 2, 1)

        adjustment_kill_grace = Gtk.Adjustment(value=max(1, int(getattr(initial, 'near_kill_grace_sec', 5))), lower=1, upper=60, step_increment=1)
        self.spn_kill_grace = Gtk.SpinButton()
        self.spn_kill_grace.set_adjustment(adjustment_kill_grace)
        self.spn_kill_grace.set_digits(0)
        self.spn_kill_grace.set_tooltip_text("Wait time after SIGTERM before forcing SIGKILL.")
        near_grid.attach(self.spn_kill_grace, 2, 6, 2, 1)

        # Path hint label
        self.lbl_log_path = Gtk.Label()
        self.lbl_log_path.set_xalign(0)
        self.lbl_log_path.set_selectable(True)
        self.lbl_log_path.set_tooltip_text("Default log file location")
        try:
            self.lbl_log_path.set_text(f"Log file: {default_log_path()}")
        except Exception:
            logger.exception("Failed to compute default log path")
            self.lbl_log_path.set_text("Log file: <unknown>")
        adv_grid.attach(self.lbl_log_path, 2, 11, 2, 1)

        # Buttons row under the notebook
        btn_box = Gtk.Box(spacing=10)
        btn_box.set_halign(Gtk.Align.END)
        root.attach(btn_box, 0, 1, 1, 1)

        btn_cancel = Gtk.Button(label="Cancel")
        btn_cancel.connect("clicked", lambda _b: self.close())
        btn_box.pack_start(btn_cancel, False, False, 0)

        btn_save = Gtk.Button(label="Save")
        btn_save.get_style_context().add_class("suggested-action")
        btn_save.connect("clicked", self._on_save)
        btn_box.pack_start(btn_save, False, False, 0)

        self._populate_initial(initial)

        # Initialize inline name-fallback warning visibility
        self._update_name_fallback_banner()

        # start RSSI monitor if we already have a selected device
        if self._selected_mac:
            self._start_rssi_monitor(self._selected_mac)

        # ensure monitor stops when window closes
        self.connect("destroy", lambda *_: self._stop_rssi_monitor())

    def _update_name_fallback_banner(self) -> None:
        try:
            name = (self._selected_name or "").strip()
            mac = (self._selected_mac or "").strip() if self._selected_mac else ""
            if name and not mac:
                self.lbl_name_fallback.set_markup(
                    "<b>Warning:</b> Name-only matching enabled; prefer MAC to avoid spoofing/false positives."
                )
                self.lbl_name_fallback.show()
            else:
                self.lbl_name_fallback.hide()
        except Exception:
            logger.exception("Failed to update name fallback banner")

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
            self._update_name_fallback_banner()
            return
        name, mac = self._device_list[idx]
        self._selected_mac = mac
        self._selected_name = name
        logger.debug("Device selection changed: %s (%s)", name, mac)
        # restart RSSI monitor for new device
        self._stop_rssi_monitor()
        if mac:
            self._start_rssi_monitor(mac)
        # Update banner on valid selection change
        self._update_name_fallback_banner()

    def _on_save(self, _btn: Gtk.Button) -> None:
        logger.info("Settings save requested")
        self.hide()

    def get_result(self) -> SettingsResult:
        """Collect current UI values into a `SettingsResult`."""
        return SettingsResult(
            device_mac=self._selected_mac,
            device_name=self._selected_name,
            rssi_threshold=int(self.spn_rssi.get_value()),
            grace_period_sec=int(self.spn_grace.get_value()),
            autostart=bool(self.chk_autostart.get_active()),
            start_delay_sec=int(self.spn_delay.get_value()),
            near_command=(self.txt_near_cmd.get_text() or None),
            near_shell=bool(self.chk_near_shell.get_active()),
            hysteresis_db=int(self.spn_hyst.get_value()),
            stale_after_sec=int(self.spn_stale.get_value()),
            re_lock_delay_sec=int(self.spn_relock.get_value()),
            scan_interval_sec=max(1.0, float(self.spn_scan.get_value())),
            near_consecutive_scans=max(1, int(self.spn_near_debounce.get_value())),
            file_logging_enabled=bool(self.chk_file_logging.get_active()),
            near_dwell_sec=max(0, int(self.spn_near_dwell.get_value())),
            cycle_rate_limit_min=max(0, int(self.spn_cycle_rl.get_value())),
            near_shell_warned=bool(self._near_shell_warned_session),
            near_timeout_sec=max(0, int(self.spn_timeout.get_value())),
            near_kill_grace_sec=max(1, int(self.spn_kill_grace.get_value())),
        )

    def _on_scan(self, _btn: Gtk.Button) -> None:
        """Discover nearby BLE devices and populate the combo box."""
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
                    # Ensure banner visibility refreshes after scan UI updates
                    self._update_name_fallback_banner()
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
                backoff = 1.0
                try:
                    last_update = 0.0
                    while self._rssi_monitor_running:
                        scanner = None
                        try:
                            scanner = BleakScanner()

                            # Allow fallback to name substring if MACs rotate
                            target_mac = (mac or "").upper()
                            name_sub = (self._selected_name or "").strip().lower()

                            def on_detect(device, advertisement_data):
                                try:
                                    dev_addr = (getattr(device, "address", "") or "").upper()
                                    dev_name = (getattr(device, "name", "") or "").strip()

                                    matched = False
                                    if target_mac:
                                        matched = (dev_addr == target_mac)
                                    if not matched and name_sub:
                                        matched = (name_sub in dev_name.lower()) if dev_name else False
                                    if not matched:
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
                            backoff = 1.0
                            try:
                                while self._rssi_monitor_running:
                                    await asyncio.sleep(1.0)
                            finally:
                                try:
                                    if scanner is not None:
                                        await scanner.stop()
                                except Exception:
                                    logger.exception("Error stopping RSSI scanner")
                        except Exception as e:
                            logger.warning("RSSI monitor error: %s; retrying in %.1fs", str(e), backoff)
                            await asyncio.sleep(backoff)
                            backoff = min(backoff * 2.0, 30.0)
                            continue
                finally:
                    GLib.idle_add(lambda: self._set_rssi_label(None))
                    # Allow future restarts if the thread exits
                    self._rssi_monitor_running = False

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
