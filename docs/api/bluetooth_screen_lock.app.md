# Module: bluetooth_screen_lock.app

GNOME/GTK tray application controller. Recent changes:

- __Locking__: Prefer session-scoped DBus (GNOME/KDE/freedesktop); scoped `loginctl` only as last resort.
- __Autostart__: dirfd-anchored, atomic writes; updates flags and optional start delay.
- __Near command__: safer execution when `near_shell=False` (absolute path, non-symlink, executable; no PATH lookup).
- __Re-lock delay__: optional cooldown after unlock; unlock detection via session signals.

```{automodule} bluetooth_screen_lock.app
:members:
:undoc-members:
:show-inheritance:
```
