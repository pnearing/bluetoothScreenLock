"""BLE RSSI-based proximity monitor.

Implements scanning via `bleak.BleakScanner` and calls user-provided callbacks
when the target device transitions to AWAY (locks after grace) or NEAR (above
threshold + hysteresis for N consecutive scans). Also emits live RSSI updates
for UI display.
"""

import asyncio
import time
import logging
from dataclasses import dataclass
from typing import Callable, Optional

from bleak import BleakScanner

logger = logging.getLogger(__name__)


def _redact_mac(mac: Optional[str]) -> str:
    """Return a partially redacted MAC for INFO logs, e.g., AA:BB:..:..:EE:FF.
    Falls back to <redacted> if the input is malformed; <none> for empty.
    """
    try:
        if not mac:
            return "<none>"
        # Strip non-alphanumeric and uppercase
        s = "".join(ch for ch in str(mac) if ch.isalnum()).upper()
        if len(s) >= 12:
            return f"{s[0:2]}:{s[2:4]}:..:..:{s[-4:-2]}:{s[-2:]}"
        return "<redacted>"
    except Exception:
        return "<redacted>"


@dataclass
class MonitorConfig:
    """Runtime configuration for `ProximityMonitor`.

    device_mac and/or device_name determine the target; MAC is preferred. Name
    equality is only used as a fallback when MAC is unset.
    """
    device_mac: Optional[str]
    rssi_threshold: int  # dBm
    grace_period_sec: int
    device_name: Optional[str] = None
    hysteresis_db: int = 5
    stale_after_sec: int = 6
    unseen_grace_sec: int = 8
    scan_interval_sec: float = 2.0
    # Require N consecutive scans above the near trigger before treating as NEAR
    near_consecutive_scans: int = 2


class ProximityMonitor:
    """Asynchronous BLE proximity evaluator using `BleakScanner`.

    Behavior:
    - Uses advertisement RSSI when device is detected; invalidates stale values
      after `stale_after_sec`.
    - Triggers NEAR when RSSI exceeds `rssi_threshold + hysteresis_db` for
      `near_consecutive_scans` cycles; triggers AWAY when RSSI stays below
      threshold for `grace_period_sec`, or when unseen for
      `stale_after_sec + unseen_grace_sec`.
    - Invokes callbacks on the GTK thread via the owner if they dispatch.

    Attributes:
        _config (MonitorConfig): Runtime configuration.
        _on_away (Callable[[], None]): Callback for AWAY state.
        _on_near (Optional[Callable[[Optional[int]], None]]): Callback for NEAR state.
        _on_rssi (Optional[Callable[[Optional[int]], None]]): Callback for live RSSI updates.
    """

    def __init__(
        self,
        config: MonitorConfig,
        on_away: Callable[[], None],
        on_near: Optional[Callable[[Optional[int]], None]] = None,
        on_rssi: Optional[Callable[[Optional[int]], None]] = None,
    ) -> None:
        """Create a monitor with callbacks for state changes.

        on_away is required; on_near and on_rssi are optional.
        """
        self._config = config
        self._on_away = on_away
        self._on_near = on_near
        self._on_rssi = on_rssi
        self._scan_interval = max(1.0, float(getattr(self._config, "scan_interval_sec", 2.0)))

        self._task: Optional[asyncio.Task] = None
        self._running = False

        self._last_seen_ts: float = 0.0
        self._last_rssi: Optional[int] = None
        # Tracks when RSSI first fell below threshold while still being detected
        self._below_since_ts: Optional[float] = None
        # Count of consecutive scans above near trigger (for debounce)
        self._near_consec_count: int = 0
        # Count consecutive BLE scanner errors for de-spamming
        self._ble_error_count: int = 0
        logger.debug(
            "ProximityMonitor created: device=%s threshold=%s dBm grace=%ss interval=%.1fs",
            self._config.device_mac, self._config.rssi_threshold, self._config.grace_period_sec, self._scan_interval
        )

    async def _scan_loop(self) -> None:
        logger.info("BLE scan loop starting for %s (name~=%s)", _redact_mac(self._config.device_mac), self._config.device_name)
        logger.debug("BLE scan loop starting for device=%s name~=%s", self._config.device_mac, self._config.device_name)

        # Outer retry loop to handle adapter off / intermittent BlueZ errors gracefully
        backoff = 1.0
        while self._running:
            scanner = None
            try:
                scanner = BleakScanner()

                # Use detection callback to capture RSSI from advertisements
                def on_detect(device, advertisement_data):
                    try:
                        target_mac = (self._config.device_mac or "").upper()
                        name_sub = (self._config.device_name or "").strip().lower()
                        dev_addr = (getattr(device, "address", "") or "").upper()
                        dev_name = (getattr(device, "name", "") or "").strip()

                        matched = False
                        if target_mac:
                            matched = (dev_addr == target_mac)
                        # Only use name fallback when no MAC is configured
                        if not target_mac and name_sub and not matched:
                            # Fallback: exact name equality (case-insensitive)
                            matched = (dev_name.lower() == name_sub) if dev_name else False

                        if matched:
                            self._last_seen_ts = time.time()
                            # Prefer RSSI from advertisement data; fallback to device.rssi if present
                            rssi_val = getattr(advertisement_data, "rssi", None)
                            if rssi_val is None:
                                rssi_val = getattr(device, "rssi", None)
                            self._last_rssi = rssi_val
                            logger.debug("Detected %s (%s) RSSI=%s dBm", dev_addr, dev_name or "", rssi_val)
                    except Exception:
                        logger.exception("Detection callback error")

                scanner.register_detection_callback(on_detect)
                await scanner.start()
                # Reset backoff after a successful start
                backoff = 1.0
                # Reset error counter on successful start to allow initial warnings if issues reoccur
                self._ble_error_count = 0

                try:
                    while self._running:
                        now = time.time()
                        rssi = self._last_rssi

                        # Invalidate stale RSSI if we haven't seen the device recently
                        since_seen = None
                        if self._last_seen_ts:
                            since_seen = now - self._last_seen_ts
                            stale_after = max(float(self._config.stale_after_sec), 1.0)
                            if since_seen > stale_after:
                                # Consider RSSI unknown if not seen for a while
                                rssi = None
                                self._last_rssi = None

                        # Emit current RSSI (or None if unknown) so UI can reflect live status
                        if self._on_rssi is not None:
                            try:
                                self._on_rssi(rssi)
                            except Exception:
                                logger.exception("on_rssi callback failed")

                        # Evaluate proximity
                        away = False
                        if self._last_seen_ts == 0:
                            # never seen yet; do nothing until grace period passes without sighting
                            pass
                        else:
                            if since_seen is None:
                                since_seen = now - self._last_seen_ts
                            if rssi is not None:
                                # Determine NEAR trigger using hysteresis to reduce flapping
                                near_trigger = self._config.rssi_threshold + max(0, int(self._config.hysteresis_db))
                                # Maintain consecutive-above-near counter
                                if rssi > near_trigger:
                                    self._near_consec_count += 1
                                else:
                                    self._near_consec_count = 0
                                # Only trigger NEAR after N consecutive qualifying scans
                                need = max(1, int(getattr(self._config, "near_consecutive_scans", 2)))
                                if self._on_near and self._near_consec_count >= need:
                                    try:
                                        self._on_near(rssi)
                                    except Exception:
                                        logger.exception("on_near callback failed")
                                    # Keep the counter capped to avoid overflow; retain state as 'near'
                                    self._near_consec_count = need

                                # Start or reset the below-threshold timer independent of since_seen
                                if rssi <= self._config.rssi_threshold:
                                    if self._below_since_ts is None:
                                        self._below_since_ts = now
                                else:
                                    # If signal is comfortably above threshold + hysteresis, clear timer
                                    if rssi > near_trigger:
                                        self._below_since_ts = None

                                # If RSSI has stayed weak long enough, mark away
                                if self._below_since_ts is not None:
                                    weak_duration = now - self._below_since_ts
                                    if weak_duration >= float(self._config.grace_period_sec):
                                        logger.info(
                                            "Away condition met: RSSI stayed <= %s dBm for %.1fs (grace=%ss)",
                                            self._config.rssi_threshold,
                                            weak_duration,
                                            self._config.grace_period_sec,
                                        )
                                        away = True
                            else:
                                # not currently seen; fallback on not-seen duration
                                # Also reset near debounce counter when unseen
                                self._near_consec_count = 0
                                unseen_required = float(self._config.stale_after_sec) + float(getattr(self._config, "unseen_grace_sec", self._config.grace_period_sec))
                                if since_seen >= unseen_required:
                                    logger.info(
                                        "Away condition met: device unseen for %.1fs >= stale(%ss)+unseen_grace(%ss)",
                                        since_seen, self._config.stale_after_sec, getattr(self._config, "unseen_grace_sec", self._config.grace_period_sec)
                                    )
                                    away = True

                        if away:
                            try:
                                self._on_away()
                            except Exception:
                                logger.exception("on_away callback failed")
                            # After triggering away, wait a bit to avoid repeat triggers
                            self._below_since_ts = None
                            await asyncio.sleep(self._config.grace_period_sec)

                        # Refresh scan interval in case config changed
                        self._scan_interval = max(1.0, float(getattr(self._config, "scan_interval_sec", self._scan_interval)))
                        await asyncio.sleep(self._scan_interval)
                finally:
                    logger.debug("Stopping BLE scanner")
                    try:
                        if scanner is not None:
                            await scanner.stop()
                    except Exception:
                        logger.exception("Error while stopping BLE scanner")
            except Exception as e:
                # Common when Bluetooth is turned off or adapter is not ready
                self._ble_error_count += 1
                if self._ble_error_count <= 3:
                    # Warn for the first few occurrences without stack to keep logs clean
                    logger.warning("BLE scanner error: %s; retrying in %.1fs", str(e), backoff)
                else:
                    # After that, only log detailed exception at DEBUG to avoid log-spam
                    logger.debug("BLE scanner error (suppressed to DEBUG); retrying in %.1fs", backoff, exc_info=True)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 30.0)
                continue

    def start(self) -> None:
        """Start the scan loop in the current event loop as a Task."""
        if self._running:
            return
        self._running = True
        loop = asyncio.get_event_loop()
        self._task = loop.create_task(self._scan_loop())
        logger.info("ProximityMonitor started")

    async def stop_async(self) -> None:
        """Stop the scan loop and await task completion."""
        if not self._running:
            return
        self._running = False
        if self._task:
            try:
                await self._task
            finally:
                self._task = None
        logger.info("ProximityMonitor stopped")

    def update_config(self, config: MonitorConfig) -> None:
        """Swap configuration and reset internal state safely."""
        self._config = config
        # Reset state when config changes
        self._last_seen_ts = 0.0
        self._last_rssi = None
        self._below_since_ts = None
        self._near_consec_count = 0
        # Update scan interval from config
        self._scan_interval = max(1.0, float(getattr(self._config, "scan_interval_sec", self._scan_interval)))
        logger.info("Monitor config updated: device=%s threshold=%s dBm grace=%ss",
                    _redact_mac(self._config.device_mac), self._config.rssi_threshold, self._config.grace_period_sec)
        logger.debug("Monitor config updated (full): device=%s threshold=%s dBm grace=%ss",
                     self._config.device_mac, self._config.rssi_threshold, self._config.grace_period_sec)
