# Module: bluetooth_screen_lock.monitor

Asynchronous BLE RSSI proximity monitor using `bleak`.

Behavior highlights:

- __Hysteresis__: NEAR requires `rssi_threshold + hysteresis_db`.
- __Debounce__: NEAR after `near_consecutive_scans` consecutive qualifying scans.
- __Away conditions__: RSSI below threshold for `grace_period_sec`, or unseen for `stale_after_sec + unseen_grace_sec`.
- __Scan interval__: dynamically applies `scan_interval_sec`, min 1.0s.

```{automodule} bluetooth_screen_lock.monitor
:members:
:undoc-members:
:show-inheritance:
```
