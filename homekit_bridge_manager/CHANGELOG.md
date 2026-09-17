# Changelog

## 1.0.1

Fixes a crash that made every scan fail with
`RuntimeError: Concurrent call to receive() is not allowed`.

- The registry fetch gathered five websocket commands onto a single
  connection. aiohttp allows only one reader per socket, so the add-on
  started cleanly and then failed on first scan. Commands are now issued in
  sequence.
- Added `tests/test_hass.py`, which drives the fetch against a websocket
  stub that refuses overlapping reads — the 1.0.0 code fails it.

## 1.0.0

First release. Read-only audit — the add-on never writes to Home Assistant.

- Reads real bridge filters from `.storage/core.config_entries` (the websocket
  API does not expose them) and real accessory allocations from
  `.storage/homekit.<entry_id>.aids`.
- Faithful port of Home Assistant's `entityfilter`, plus HomeKit's own
  hidden / `entity_category` / supported-domain rules.
- Detectors: cross-bridge duplicates, round-trip re-exports, stale allocations,
  group-helper exports, diagnostic leaks, orphaned accessory ids, dead
  accessories, capacity pressure, and bridge/accessory mode mismatches.
- Master exposure view: every published entity and every bridge publishing it.
- Capacity meters against HAP's 150-accessory ceiling.
