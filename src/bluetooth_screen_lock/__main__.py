#!/usr/bin/env python3
"""Command-line entry point for Bluetooth Screen Lock.

Initializes logging and launches the GTK tray application.

Defaults to INFO logging (customizable via LOG_LEVEL env var). A `--debug`
runtime flag forces DEBUG logging regardless of LOG_LEVEL.
"""
import argparse
import logging
import os
import sys

from .app import App

def _setup_logging(level: str | int) -> None:
    """Configure logging so DEBUG/INFO go to stdout and WARNING+ to stderr.

    Args:
        level: Logging level name or numeric value for the root logger.
    """
    class MaxLevelFilter(logging.Filter):
        def __init__(self, max_level: int) -> None:
            super().__init__()
            self.max_level = max_level

        def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
            return record.levelno <= self.max_level

    root = logging.getLogger()
    if root.handlers:
        for h in list(root.handlers):
            root.removeHandler(h)

    root.setLevel(level)
    fmt = logging.Formatter(
        fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging.DEBUG)
    stdout_handler.addFilter(MaxLevelFilter(logging.INFO))
    stdout_handler.setFormatter(fmt)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)
    stderr_handler.setFormatter(fmt)

    root.addHandler(stdout_handler)
    root.addHandler(stderr_handler)

    logging.captureWarnings(True)


def main(argv: list[str] | None = None) -> int:
    """Set up logging and run the GTK tray app.

    Returns 0 on normal exit.
    """
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(description="Bluetooth Screen Lock")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable DEBUG logging (overrides LOG_LEVEL)",
    )
    args = parser.parse_args(argv)

    # Determine logging level: --debug overrides LOG_LEVEL, else use env with INFO default
    level_name = "DEBUG" if args.debug else os.getenv("LOG_LEVEL", "INFO").upper()
    _setup_logging(level_name)
    app = App()
    app.run()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
