# Module: bluetooth_screen_lock.config

Defines the persisted configuration schema and safe load/save helpers.

Highlights:

- __New fields__: `near_shell`, `hysteresis_db`, `stale_after_sec`, `scan_interval_sec`,
  `locking_enabled`, `re_lock_delay_sec`, `near_consecutive_scans`, `unseen_grace_sec`,
  `near_dwell_sec`, `cycle_rate_limit_min`.
- __Security__: dirfd-anchored atomic writes, enforce 0600 file and 0700 config dir.
- __Validation__: clamps key values (RSSI thresholds, grace periods, dwell seconds,
  cycle rate limit minutes, scan interval).

```{automodule} bluetooth_screen_lock.config
:members:
:undoc-members:
:show-inheritance:
```
