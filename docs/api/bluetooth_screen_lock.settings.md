# Module: bluetooth_screen_lock.settings

A GTK3 preferences window that helps you configure proximity behavior and startup options.
It provides BLE device discovery, a live RSSI readout to aid choosing a threshold, and
advanced tuning. The dialog returns a `SettingsResult` reflecting the current values.

Highlights:

- __Device selection and scan__: Discover nearby BLE devices and choose one as the target.
- __Live RSSI display__: Shows current dBm for the selected device to guide the `rssi_threshold`.
- __RSSI threshold & grace__: Locks when RSSI stays below the threshold (or device unseen) for
  the configured `grace_period_sec`.
- __Autostart & start delay__: Enable launch at login and optionally delay startup (`start_delay_sec`).
- __Hysteresis__: Extra dB required to transition to NEAR, reducing flapping near the boundary.
- __Stale RSSI timeout__: Treats RSSI as unknown if not updated within `stale_after_sec` seconds.
- __Re-lock delay__: Cooldown after becoming NEAR to avoid immediate re-lock after unlocking.
- __Scan interval__: Controls BLE polling cadence vs responsiveness (`scan_interval_sec`).
- __Near debounce__: Require N consecutive scans above the near trigger (`near_consecutive_scans`).
- __Near command__: Optional command executed once on NEAR. A “Run in shell” toggle is available
  for advanced use; prefer absolute paths and avoid shell unless needed.
- __Near dwell__: Require the device to remain NEAR for N seconds before running the near command (`near_dwell_sec`).
- __Cycle rate limit__: Allow at most one lock+unlock cycle per M minutes to avoid churn (`cycle_rate_limit_min`).
- __Name-only fallback warning__: Inline banner appears if only a device name is used; prefer MACs
  to minimize spoofing/false positives.

```{automodule} bluetooth_screen_lock.settings
:members:
:undoc-members:
:show-inheritance:
```
