# Changelog

## 1.1.0

Two honesty fixes. Neither changes what the add-on can see; both change how
confidently it speaks.

- **Round-trip detection is split by confidence.** `homekit_controller` devices
  are paired over HAP as a matter of record, so those stay `critical` and now
  say why. The HomeKit-capable brands — hue, lifx, nanoleaf, ecobee, netatmo —
  were being asserted as fact on the strength of a hardcoded brand list, which
  told users their lights were duplicated when they may never have paired that
  bridge with Apple Home. Those are now a `warning` phrased as a question.
- **New `assume_not_in_homekit` option.** Name the integrations you have not
  paired with Apple Home and their "likely" findings stop. Confirmed
  round-trips are never suppressed by it.
- **Synthetic data announces itself.** `tools/dev_server.py` stamps the
  snapshot `source: synthetic`; the UI renders a banner and marks the subtitle.
  Fixture entity ids were previously indistinguishable from live ones, which is
  a good way to spend an afternoon debugging a house that does not exist.

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
