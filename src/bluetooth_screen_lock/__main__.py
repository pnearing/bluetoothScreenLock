#!/usr/bin/env python3
import logging
import os
import sys

from .app import App

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

def _setup_logging() -> None:
    """Configure logging so DEBUG/INFO go to stdout and WARNING+ to stderr."""
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

    root.setLevel(LOG_LEVEL)
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


def main() -> int:
    _setup_logging()
    app = App()
    app.run()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
