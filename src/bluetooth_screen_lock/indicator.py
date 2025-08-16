"""GTK/Ayatana tray indicator for Bluetooth Screen Lock.

This module provides the `TrayIndicator` wrapper around AppIndicator/Ayatana
indicators to expose a simple status menu with:
- Live status line
- Optional warning line
- Manual "Lock now" action
- Enable/disable automatic locking toggle
- Settings and Quit entries

It targets GNOME/GTK trays (no Qt), trying both `AppIndicator3` and
`AyatanaAppIndicator3` at import time.
"""

import logging
from typing import Callable, Optional

import gi

gi.require_version("Gtk", "3.0")
try:
    gi.require_version("AppIndicator3", "0.1")
except ValueError:
    # On some systems the typelib name is AyatanaAppIndicator3
    try:
        gi.require_version("AyatanaAppIndicator3", "0.1")
    except ValueError:
        pass

from gi.repository import Gtk, GLib

# Try to import one of the indicators
try:
    from gi.repository import AppIndicator3 as AppIndicator
except ImportError:
    from gi.repository import AyatanaAppIndicator3 as AppIndicator  # type: ignore

logger = logging.getLogger(__name__)


class TrayIndicator:
    """System tray menu/indicator for the app.

    Parameters
    ----------
    app_id : str
        Icon/theme id used by the indicator.
    on_open_settings : Callable[[], None]
        Callback invoked when the user clicks "Settings".
    on_quit : Callable[[], None]
        Callback invoked when the user clicks "Quit".
    on_lock_now : Optional[Callable[[], None]]
        Optional callback to trigger an immediate lock action.
    on_toggle_locking : Optional[Callable[[bool], None]]
        Optional callback invoked when the lock toggle state changes.
    locking_enabled : bool
        Initial state of the lock toggle.
    """
    def __init__(
        self,
        app_id: str,
        on_open_settings: Callable[[], None],
        on_quit: Callable[[], None],
        on_lock_now: Optional[Callable[[], None]] = None,
        on_toggle_locking: Optional[Callable[[bool], None]] = None,
        locking_enabled: bool = True,
    ) -> None:
        self._app_id = app_id
        self._on_open_settings = on_open_settings
        self._on_quit = on_quit
        self._on_lock_now = on_lock_now
        self._on_toggle_locking = on_toggle_locking

        self._status_label = Gtk.MenuItem(label="Status: Idle")
        self._status_label.set_sensitive(False)

        # Optional warning banner (non-interactive)
        self._warning_item = Gtk.MenuItem(label="")
        self._warning_item.set_sensitive(False)
        self._warning_item.set_visible(False)

        self._lock_item = Gtk.MenuItem(label="Lock now")
        self._lock_item.connect("activate", self._on_lock_now_activate)

        # Toggle for enabling/disabling automatic locking
        self._lock_toggle = Gtk.CheckMenuItem(label="Enable locking")
        self._lock_toggle.set_active(bool(locking_enabled))
        self._lock_toggle.connect("toggled", self._on_lock_toggle)

        self._settings_item = Gtk.MenuItem(label="Settings…")
        self._settings_item.connect("activate", self._on_settings)

        self._quit_item = Gtk.MenuItem(label="Quit")
        self._quit_item.connect("activate", self._on_quit_activate)

        menu = Gtk.Menu()
        menu.append(self._status_label)
        menu.append(self._warning_item)
        menu.append(Gtk.SeparatorMenuItem())
        menu.append(self._lock_item)
        menu.append(self._lock_toggle)
        menu.append(self._settings_item)
        menu.append(self._quit_item)
        menu.show_all()

        self._indicator = AppIndicator.Indicator.new(
            self._app_id,
            "bluetooth-screen-lock",  # use themed app icon name
            AppIndicator.IndicatorCategory.APPLICATION_STATUS,
        )
        self._indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self._indicator.set_menu(menu)
        logger.debug("TrayIndicator initialized with app_id=%s", app_id)

    def _on_settings(self, _item: Gtk.MenuItem) -> None:
        """Open the app's settings window via the provided callback."""
        logger.info("Settings menu clicked")
        self._on_open_settings()

    def _on_quit_activate(self, _item: Gtk.MenuItem) -> None:
        """Quit the application via the provided callback."""
        logger.info("Quit menu clicked")
        self._on_quit()

    def set_status(self, text: str) -> None:
        """Update the status line in the tray menu in a thread-safe way."""
        def update() -> None:
            self._status_label.set_label(f"Status: {text}")
        GLib.idle_add(update)
        logger.debug("Status updated: %s", text)

    def set_warning(self, text: Optional[str]) -> None:
        """Show or hide a warning line in the menu."""
        def update() -> None:
            if text:
                self._warning_item.set_label(f"Warning: {text}")
                self._warning_item.set_visible(True)
            else:
                self._warning_item.set_visible(False)
        GLib.idle_add(update)

    def _on_lock_now_activate(self, _item: Gtk.MenuItem) -> None:
        """Handle clicks on the "Lock now" menu item."""
        logger.info("Lock now menu clicked")
        try:
            if self._on_lock_now is not None:
                # Trigger app-provided lock
                self._on_lock_now()
            # Reflect manual action in status regardless of backend outcome
            self.set_status("Locked (manual)")
        except Exception:
            logger.exception("Lock now action failed")

    def _on_lock_toggle(self, item: Gtk.CheckMenuItem) -> None:
        """Handle the enable/disable automatic locking toggle."""
        enabled = item.get_active()
        logger.info("Locking toggled: %s", "enabled" if enabled else "disabled")
        try:
            if self._on_toggle_locking is not None:
                self._on_toggle_locking(enabled)
        except Exception:
            logger.exception("Failed to handle locking toggle")

    def set_locking_enabled(self, enabled: bool) -> None:
        """Programmatically set the toggle state (thread-safe)."""
        def update() -> None:
            self._lock_toggle.set_active(bool(enabled))
        GLib.idle_add(update)

    def set_lock_available(self, available: bool) -> None:
        """Enable/disable the "Lock now" item and set a helpful tooltip.
        Call this with False when no device is configured.
        """
        def update() -> None:
            self._lock_item.set_sensitive(bool(available))
            tip = None if available else "Configure a device in Settings to enable manual lock."
            try:
                # MenuItems don't have set_tooltip_text in all themes; guard call
                if hasattr(self._lock_item, "set_tooltip_text"):
                    self._lock_item.set_tooltip_text(tip)
            except Exception:
                pass
        GLib.idle_add(update)
