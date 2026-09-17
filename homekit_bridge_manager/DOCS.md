# HomeKit Bridge Manager

A Home Assistant add-on that gives you one view across **every** HomeKit bridge.

Home Assistant's HomeKit integration is written as if you have one bridge. Every
screen is scoped to a single config entry: open a bridge, edit its filter, close
it. That's fine at one bridge. At ten it stops working, and three failure modes
appear that Home Assistant will never tell you about.

**Duplicates are structurally invisible.** A bridge's filter only sees its own
entities. Two bridges can publish the same entity and neither will ever know.
You find out when Home.app has two tiles for one smoke alarm.

**The 150-accessory ceiling has no gauge.** HAP caps a bridge at 150 unique
accessory ids. Home Assistant does not show your count, does not warn you as you
approach it, and when you cross it the *entire bridge* goes unresponsive in
Home.app — not just the 151st accessory.

**Round trips are silent.** If you import a device over HomeKit Controller (or
run Hue, LIFX, Nanoleaf, Homebridge…) and then re-export it through a bridge,
Home.app shows the same physical device twice — once natively, once as a Home
Assistant accessory. Nothing in Home Assistant models both directions at once.

This add-on reads the two sources of truth that the API does not expose, joins
them with the live registries, and tells you what is actually going on.

## What it finds

| Detector | What it catches |
|---|---|
| **Duplicate publication** | One entity published by two or more bridges — confirmed when both hold a live accessory id |
| **Round trip (confirmed)** | A `homekit_controller` device — paired over HAP as a matter of record — re-exported through a bridge |
| **Round trip (likely)** | A device from a HomeKit-capable brand (hue, lifx, nanoleaf, ecobee, netatmo). Whether you paired it with Apple Home is invisible to Home Assistant, so this is raised as a question and can be dismissed per integration |
| **Stale allocation** | An accessory id still on disk for an entity the bridge's *current* filter would reject |
| **Orphaned aid** | An allocation whose entity no longer exists at all, still holding a slot |
| **Capacity** | Bridges past 60% / 85% of the 150 ceiling, flagged as growing if the filter is open-ended |
| **Group helper** | Group and template helpers exported alongside the entities they contain |
| **Diagnostic leak** | Config/diagnostic entities that got named into an include list by accident |
| **Dead accessory** | Published entities that are currently unavailable — "No Response" in Home.app |
| **Mode mismatch** | Entries named like a bridge but configured `mode: accessory` |

Plus a **master exposure view**: every published entity, every bridge publishing
it, its accessory id, and the integration it originally came from — filterable by
domain, by flag, or by free text.

## Read-only, by design

The add-on maps the config directory `:ro`:

```yaml
map:
  - homeassistant_config:ro
```

It cannot modify Home Assistant's `.storage` even if it wanted to. v1 audits and
reports; change plans arrive in v1.1 as reviewable files you apply yourself.

## How it works

Two things it reads that the Home Assistant API will not give you:

**`.storage/core.config_entries`** — the real `options.filter` for each bridge.
The websocket `config_entries/get` command omits options entirely, and probing
the options flow only returns its first step (mode / include-exclude mode /
domains), never the entity lists.

**`.storage/homekit.<entry_id>.aids`** — the ground truth for what a bridge has
*actually* published: a map of storage key → accessory id. Keys are the entity's
system unique id (`platform.domain.unique_id`) where it has one, and the
`entity_id` where it doesn't, matching `homekit/aidmanager.py` upstream. The
add-on resolves both forms back to entity ids using the entity registry.

Those get joined with the entity, device, area and config-entry registries pulled
over the websocket API, using `SUPERVISOR_TOKEN` through the Supervisor proxy —
no long-lived access token in your add-on config.

`app/entityfilter.py` is a faithful port of Home Assistant's
`homeassistant.helpers.entityfilter`, including all four include/exclude cases
and glob handling, plus HomeKit's own rules on top: supported domains only, and
hidden / `entity_category` entities dropped unless explicitly named or
glob-matched. The tests pin this behaviour — if upstream changes, they should
fail.

## Install

1. Settings → Add-ons → Add-on Store → ⋮ → **Repositories**
2. Add this repository's URL
3. Install **HomeKit Bridge Manager**, start it, open it from the sidebar

### Options

| Option | Default | What it does |
|---|---|---|
| `refresh_seconds` | `300` | How often to re-scan |
| `log_level` | `info` | Add-on log verbosity |
| `assume_not_in_homekit` | `[]` | Integrations you have **not** paired with Apple Home |

`assume_not_in_homekit` exists because "likely" round-trips are an inference.
The add-on can see that you run the Hue integration; it cannot see whether your
Hue bridge is in the Home app. If it isn't, list the integration and those
findings stop:

```yaml
assume_not_in_homekit:
  - hue
  - lifx
```

Confirmed round-trips (`homekit_controller`) are never suppressed by this — they
are provable from Home Assistant's own data.

## Development

```bash
cd homekit_bridge_manager
pip install -r requirements.txt pytest

pytest tests/ -q                       # 32 tests, no network needed
python3 tools/dev_server.py            # synthetic 450-entity instance on :8099
python3 tools/dev_server.py --dump s.json
```

`tools/dev_server.py` generates a `.storage` directory and registries shaped like
a large multi-ecosystem install (14 integrations, 15 HomeKit entries, deliberate
duplicates, stale allocations and orphans), then serves the real app against it.
No Home Assistant required.

**Everything it serves is invented.** Entity ids like
`binary_sensor.kidde_binary_sensor_24` are generated from a global per-domain
counter and describe nobody's house. The harness stamps the snapshot
`source: synthetic`, the UI renders a banner saying so, and the subtitle reads
`SYNTHETIC DATA`. Do not read your own setup out of a dev-server screenshot.

The core — `entityfilter`, `storage`, `model`, `detectors` — has no third-party
imports, so it is testable without a network stack. `hass.py` is the only module
that touches aiohttp.

## Roadmap

**v1.1** — change plans. Staged include/exclude edits exported as reviewable
YAML with an `effect:` line per operation, an aid snapshot taken first, and a
capacity preflight. Removal is called out as destructive where it is: an entity
without a `unique_id` that loses its aid comes back as a *new* accessory, losing
its Home.app room, favourite and automations.

**v1.2** — drift detection. Snapshot filters and aid maps to SQLite on each scan,
then report what changed and when.

**v2** — the other exposure surfaces. Assist, Alexa and Google exposure live in
three unrelated systems with three separate UIs and no shared view. Folding them
into the master view is what turns this from a HomeKit utility into an exposure
control plane.

## Licence

MIT.
