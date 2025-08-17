# Module: bluetooth_screen_lock.app

GNOME/GTK tray application controller. Recent changes:

- __Locking__: Prefer session-scoped DBus (GNOME/KDE/freedesktop); scoped `loginctl` only as last resort.
- __Autostart__: dirfd-anchored, atomic writes; updates flags and optional start delay.
- __Near command__: safer execution when `near_shell=False` (absolute path, non-symlink, executable; no PATH lookup).
- __Re-lock delay__: optional cooldown after unlock; unlock detection via session signals.
- __Near dwell__: optional sustained NEAR requirement before running the near command.
- __Cycle rate limit__: optional global limit to one lock+unlock cycle per configured minutes.
- __DBus timeouts__: finite default call timeouts to avoid UI hangs when services stall.
- __Autostart hardening__: delay wrapper uses absolute binaries; falls back gracefully when helpers are unavailable.

```{automodule} bluetooth_screen_lock.app
:members:
:undoc-members:
:show-inheritance:
```
