# Module: bluetooth_screen_lock.__main__

This is the CLI entry point. It configures logging and starts the GTK tray app.

- __Logging__: INFO by default, override with `LOG_LEVEL` env.
- __Flag__: `--debug` forces DEBUG logging (overrides `LOG_LEVEL`).
- __Streams__: DEBUG/INFO to stdout; WARNING+ to stderr.

Usage examples:

```bash
bluetooth-screen-lock --debug
LOG_LEVEL=WARNING bluetooth-screen-lock
```

```{automodule} bluetooth_screen_lock.__main__
:members:
:undoc-members:
:show-inheritance:
```
