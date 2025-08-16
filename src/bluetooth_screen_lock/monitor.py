import asyncio
import time
import logging
from dataclasses import dataclass
from typing import Callable, Optional

from bleak import BleakScanner

logger = logging.getLogger(__name__)


@dataclass
class MonitorConfig:
    device_mac: Optional[str]
    rssi_threshold: int  # dBm
    grace_period_sec: int
    hysteresis_db: int = 5
    stale_after_sec: int = 6


class ProximityMonitor:
    """
    BLE RSSI-based proximity monitor using BleakScanner.
    - Prefers RSSI from advertisements for target MAC.
    - Fallback: if device not seen at all for grace period, consider away.
    Runs in an asyncio Task.
    """

    def __init__(
        self,
        config: MonitorConfig,
        on_away: Callable[[], None],
        on_near: Optional[Callable[[int], None]] = None,
        scan_interval: float = 2.0,
    ) -> None:
        self._config = config
        self._on_away = on_away
        self._on_near = on_near
        self._scan_interval = scan_interval

        self._task: Optional[asyncio.Task] = None
        self._running = False

        self._last_seen_ts: float = 0.0
        self._last_rssi: Optional[int] = None
        # Tracks when RSSI first fell below threshold while still being detected
        self._below_since_ts: Optional[float] = None
        logger.debug(
            "ProximityMonitor created: device=%s threshold=%s dBm grace=%ss interval=%.1fs",
            self._config.device_mac, self._config.rssi_threshold, self._config.grace_period_sec, self._scan_interval
        )

    async def _scan_loop(self) -> None:
        scanner = BleakScanner()
        logger.info("BLE scan loop starting for %s", self._config.device_mac)

        # Use detection callback to capture RSSI from advertisements
        def on_detect(device, advertisement_data):
            try:
                if not self._config.device_mac:
                    return
                if (device.address or "").upper() == self._config.device_mac.upper():
                    self._last_seen_ts = time.time()
                    # Prefer RSSI from advertisement data; fallback to device.rssi if present
                    rssi_val = getattr(advertisement_data, "rssi", None)
                    if rssi_val is None:
                        rssi_val = getattr(device, "rssi", None)
                    self._last_rssi = rssi_val
                    logger.debug("Detected %s RSSI=%s dBm", device.address, rssi_val)
            except Exception:
                logger.exception("Detection callback error")

        scanner.register_detection_callback(on_detect)
        await scanner.start()
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
                        if self._on_near and rssi > near_trigger:
                            try:
                                self._on_near(rssi)
                            except Exception:
                                logger.exception("on_near callback failed")

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
                        if since_seen >= self._config.grace_period_sec:
                            logger.info("Away condition met: device unseen for %.1fs >= %ss",
                                        since_seen, self._config.grace_period_sec)
                            away = True

                if away:
                    try:
                        self._on_away()
                    except Exception:
                        logger.exception("on_away callback failed")
                    # After triggering away, wait a bit to avoid repeat triggers
                    self._below_since_ts = None
                    await asyncio.sleep(self._config.grace_period_sec)

                await asyncio.sleep(self._scan_interval)
        finally:
            logger.debug("Stopping BLE scanner")
            await scanner.stop()

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        loop = asyncio.get_event_loop()
        self._task = loop.create_task(self._scan_loop())
        logger.info("ProximityMonitor started")

    async def stop_async(self) -> None:
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
        self._config = config
        # Reset state when config changes
        self._last_seen_ts = 0.0
        self._last_rssi = None
        self._below_since_ts = None
        logger.info("Monitor config updated: device=%s threshold=%s dBm grace=%ss",
                    self._config.device_mac, self._config.rssi_threshold, self._config.grace_period_sec)
