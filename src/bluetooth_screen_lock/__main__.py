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
import stat
from logging.handlers import RotatingFileHandler

from .app import App
from .config import load_config, default_log_path, STATE_DIR


LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

def _setup_logging(
    level: str | int,
    *,
    file_logging: bool = False,
    file_path: str | None = None,
    file_max_bytes: int = 5_242_880,
    file_backups: int = 3,
) -> None:
    """Configure logging handlers.

    - DEBUG/INFO to stdout.
    - WARNING+ to stderr.
    - Optional rotating file handler when `file_logging` is True.

    Args:
        level: Root logging level name or numeric value.
        file_logging: Enable file logging if True.
        file_path: Destination log file path; if None, a default is resolved.
        file_max_bytes: Rotation threshold in bytes.
        file_backups: Number of rotated backups to keep.
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

    # Optional rotating file handler
    if file_logging:
        try:
            path = file_path or default_log_path()
            # Ensure parent directory exists when using XDG state dir
            try:
                # If the chosen path is under our STATE_DIR, ensure it exists securely
                if path.startswith(STATE_DIR + os.sep) or os.path.dirname(path) == STATE_DIR:
                    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
                    try:
                        st = os.lstat(STATE_DIR)
                        if stat.S_ISLNK(st.st_mode):
                            raise RuntimeError(f"Refusing to use symlinked state dir: {STATE_DIR}")
                        os.chmod(STATE_DIR, 0o700)
                    except Exception:
                        pass
            except Exception:
                pass
            fh = RotatingFileHandler(path, maxBytes=int(file_max_bytes), backupCount=int(file_backups))
            fh.setLevel(level if isinstance(level, int) else logging._nameToLevel.get(str(level).upper(), logging.INFO))
            fh.setFormatter(fmt)
            root.addHandler(fh)
            # Best-effort hardening: ensure the log file itself is not world-readable.
            # Rationale: When $XDG_STATE_HOME does not exist, we fall back to a log file
            # directly under the user's home directory (e.g., ~/bluetooth-screen-lock.log).
            # Logging's RotatingFileHandler honors the process umask, which commonly results
            # in 0644. Logs may include device identifiers or timing information, so we
            # explicitly chmod the file to 0600 after the handler creates/opens it.
            try:
                os.chmod(path, 0o600)
            except Exception:
                # Do not fail if we cannot change the mode; continue with best-effort security.
                logging.getLogger(__name__).debug("Could not chmod log file to 0600", exc_info=True)
        except Exception:
            # Do not fail startup if file handler cannot be created
            logging.getLogger(__name__).warning("File logging requested but could not be initialized", exc_info=True)

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
    level_name = "DEBUG" if args.debug else LOG_LEVEL
    if level_name not in logging._nameToLevel:
        print(f"Invalid log level: {level_name}, defaulting to INFO", file=sys.stderr)
        level_name = "INFO"

    # Load config early so logging can attach file handler if enabled
    try:
        cfg = load_config()
    except Exception:
        cfg = None

    enable_file = bool(getattr(cfg, "file_logging_enabled", False)) if cfg else False
    file_path = getattr(cfg, "file_log_path", None) if cfg else None
    file_max = int(getattr(cfg, "file_log_max_bytes", 5_242_880)) if cfg else 5_242_880
    file_bak = int(getattr(cfg, "file_log_backups", 3)) if cfg else 3

    _setup_logging(
        level_name,
        file_logging=enable_file,
        file_path=file_path,
        file_max_bytes=file_max,
        file_backups=file_bak,
    )
    app = App()
    app.run()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
