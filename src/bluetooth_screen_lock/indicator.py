import threading
import subprocess
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
    def __init__(
        self,
        app_id: str,
        on_open_settings: Callable[[], None],
        on_quit: Callable[[], None],
    ) -> None:
        self._app_id = app_id
        self._on_open_settings = on_open_settings
        self._on_quit = on_quit

        self._status_label = Gtk.MenuItem(label="Status: Idle")
        self._status_label.set_sensitive(False)

        self._settings_item = Gtk.MenuItem(label="Settings…")
        self._settings_item.connect("activate", self._on_settings)

        self._quit_item = Gtk.MenuItem(label="Quit")
        self._quit_item.connect("activate", self._on_quit_activate)

        menu = Gtk.Menu()
        menu.append(self._status_label)
        menu.append(Gtk.SeparatorMenuItem())
        menu.append(self._settings_item)
        menu.append(self._quit_item)
        menu.show_all()

        self._indicator = AppIndicator.Indicator.new(
            self._app_id,
            "network-wireless",  # use an available icon name
            AppIndicator.IndicatorCategory.APPLICATION_STATUS,
        )
        self._indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self._indicator.set_menu(menu)
        logger.debug("TrayIndicator initialized with app_id=%s", app_id)

    def _on_settings(self, _item: Gtk.MenuItem) -> None:
        logger.info("Settings menu clicked")
        self._on_open_settings()

    def _on_quit_activate(self, _item: Gtk.MenuItem) -> None:
        logger.info("Quit menu clicked")
        self._on_quit()

    def set_status(self, text: str) -> None:
        def update() -> None:
            self._status_label.set_label(f"Status: {text}")
        GLib.idle_add(update)
        logger.debug("Status updated: %s", text)
