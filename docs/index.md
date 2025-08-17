# Bluetooth Screen Lock Documentation

Welcome to the documentation for Bluetooth Screen Lock — a GNOME/GTK tray app that locks your screen based on Bluetooth proximity using RSSI.

- GNOME/GTK tray (no Qt)
- Tabbed Settings dialog: General, Near Command, Advanced
- RSSI-based proximity with grace period and hysteresis
- Optional Near Command on return (Execution and Timing controls: dwell, timeout, kill grace)
- Autostart support with optional start delay

## Get Started

- See the [User Guide](user-guide.md) for installation and configuration.
- Browse the [API Reference](api/index.md) for module documentation.

## Contents

- [User Guide](user-guide.md)
- [API Reference](api/index.md)

```{toctree}
:maxdepth: 2
:hidden:

user-guide
api/index
```

## Logging

Enable the "Write log file" toggle in Settings to write logs to a rotating file in addition to stdout/stderr. See details in the User Guide's Logging section.
