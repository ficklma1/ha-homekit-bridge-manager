"""Dev harness: run the real server against a synthetic Home Assistant.

Generates a ``.storage`` directory and a set of registries shaped like a large
multi-ecosystem instance (the 15-entry HomeKit setup this add-on was designed
against), then serves the real aiohttp app so the UI can be exercised without a
live Home Assistant.

    python3 tools/dev_server.py [--port 8099] [--dump snapshot.json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import hass  # noqa: E402
from app.config import Config  # noqa: E402
from app.registry import ConfigEntryRow, DeviceRow, EntityRow, Registries  # noqa: E402
from app.server import create_app  # noqa: E402
from app.storage import system_unique_id  # noqa: E402

random.seed(7)

# (title, mode, filter) — mirrors a real 15-entry HomeKit configuration.
BRIDGES = [
    ("sw01", "HASS Bridge: Switches", "bridge", {"include_domains": ["switch"]}),
    ("li01", "HASS Bridge: Lights", "bridge", {"include_domains": ["light"]}),
    ("se01", "HASS Bridge: Sensors", "bridge", {"include_domains": ["sensor"]}),
    ("bs01", "HASS Bridge: Binary Sensors", "bridge", {"include_domains": ["binary_sensor"]}),
    ("sc01", "HASS Bridge: Scenes", "bridge", {"include_domains": ["scene"]}),
    ("ib01", "HASS Bridge: Input Boolean", "bridge", {"include_domains": ["input_boolean"]}),
    ("sr01", "HASS Bridge: Scripts", "bridge", {"include_domains": ["script"]}),
    ("fa01", "HASS Bridge: Fans", "bridge", {"include_domains": ["fan"]}),
    ("al01", "HASS Bridge: Alarms", "bridge", {"include_domains": ["alarm_control_panel"]}),
    ("sd01", "HASS Bridge: Smoke Detector", "bridge",
     {"include_entities": ["binary_sensor.main_detector_smoke_alarm",
                           "binary_sensor.main_detector_too_much_smoke"]}),
    ("gar01", "HASS Bridge: Garage", "accessory",
     {"include_entities": ["cover.msg100_5218_garage_door"]}),
    ("mp01", "Media RX-A3000 MAIN", "accessory",
     {"include_entities": ["media_player.rx_a3000_main"]}),
    ("mp02", "Media Garage:21075", "accessory",
     {"include_entities": ["media_player.garage"]}),
    ("mp03", "75in Roku TV", "accessory",
     {"include_entities": ["media_player.westinghouse_ce05x_6b6d"]}),
    ("mp04", "50in Roku TV", "accessory",
     {"include_entities": ["media_player.roku_g846x_11a1"]}),
]

AREAS = ["Theater", "Kitchen", "Master bedroom", "Sunroom", "Garage",
         "Nancy's office", "Mark's office", "Guest bedroom", "Deck"]

# platform -> how many entities of each domain it contributes
ECOSYSTEMS = [
    ("lutron_caseta", "lutron_caseta", {"light": 14, "switch": 12}),
    ("isy994", "isy994", {"light": 18, "switch": 30, "sensor": 8}),
    ("hue", "hue", {"light": 22, "sensor": 4, "binary_sensor": 3}),
    ("lifx", "lifx", {"light": 3}),
    ("nanoleaf", "nanoleaf", {"light": 4}),
    ("homekit_controller", "homekit_controller",
     {"light": 6, "switch": 8, "sensor": 14, "binary_sensor": 6,
      "cover": 1, "media_player": 2}),
    ("tplink", "tplink", {"light": 5, "switch": 14}),
    ("shelly", "shelly", {"switch": 4, "sensor": 6}),
    ("zwave_js", "zwave_js", {"switch": 16, "sensor": 40, "binary_sensor": 12, "fan": 3}),
    ("kidde", "kidde", {"binary_sensor": 8, "sensor": 10, "switch": 4}),
    ("govee", "govee", {"light": 3}),
    ("localtuya", "localtuya", {"switch": 6, "light": 2}),
    ("frigate", "frigate", {"binary_sensor": 5, "switch": 4, "sensor": 6}),
    ("alarmdecoder", "alarmdecoder", {"binary_sensor": 6, "alarm_control_panel": 1}),
    ("ecobee", "ecobee", {"sensor": 12, "binary_sensor": 2}),
    ("wiim", "wiim", {"media_player": 3, "switch": 3}),
    ("group", "group", {"light": 6, "switch": 2}),
    ("template", "template", {"binary_sensor": 4, "sensor": 5}),
    ("input_boolean", "input_boolean", {"input_boolean": 30}),
    ("homeassistant_scene", "homeassistant", {"scene": 59}),
    ("script", "script", {"script": 9}),
]

# Entities that should be config/diagnostic — HomeKit drops these by default.
DIAGNOSTIC_HINTS = ("identify", "led", "recordings", "review_detections", "beep")


def build_world():
    """Create registries plus the aid allocations a real bridge would have made."""
    reg = Registries()
    counters: dict[str, int] = {}
    named: dict[str, list[str]] = {}

    for entry_id, domain in [
        (f"ce_{p}", d) for p, d, _ in ECOSYSTEMS
    ]:
        reg.config_entries[entry_id] = ConfigEntryRow(
            entry_id=entry_id, domain=domain, title=domain.replace("_", " ").title()
        )
    for i, area in enumerate(AREAS):
        reg.areas[f"area_{i}"] = area

    for platform, source_domain, domains in ECOSYSTEMS:
        for domain, count in domains.items():
            for _ in range(count):
                n = counters[domain] = counters.get(domain, 0) + 1
                suffix = random.choice(DIAGNOSTIC_HINTS) if random.random() < 0.10 else ""
                slug = f"{platform}_{domain}_{n}" + (f"_{suffix}" if suffix else "")
                entity_id = f"{domain}.{slug}"
                device_id = f"dev_{platform}_{n}"
                reg.devices[device_id] = DeviceRow(
                    device_id=device_id,
                    name=slug.replace("_", " ").title(),
                    config_entries=[f"ce_{platform}"],
                    area_id=random.choice(list(reg.areas)),
                )
                reg.entities[entity_id] = EntityRow(
                    entity_id=entity_id,
                    platform=platform,
                    unique_id=f"uid-{platform}-{domain}-{n}",
                    entity_category="config" if suffix else None,
                    device_id=device_id,
                    name=slug.replace("_", " ").title(),
                )
                # A realistic scattering of unavailable entities.
                reg.states[entity_id] = (
                    "unavailable" if random.random() < 0.09 else "on"
                )
                named.setdefault(domain, []).append(entity_id)

    # The specific entities the accessory-mode bridges name.
    for entity_id, platform, source in [
        ("cover.msg100_5218_garage_door", "homekit_controller", "homekit_controller"),
        ("media_player.westinghouse_ce05x_6b6d", "homekit_controller", "homekit_controller"),
        ("media_player.roku_g846x_11a1", "homekit_controller", "homekit_controller"),
        ("media_player.rx_a3000_main", "yamaha_ynca", "yamaha_ynca"),
        ("media_player.garage", "androidtv", "androidtv"),
        ("binary_sensor.main_detector_smoke_alarm", "kidde", "kidde"),
        ("binary_sensor.main_detector_too_much_smoke", "kidde", "kidde"),
    ]:
        device_id = f"dev_named_{entity_id}"
        reg.config_entries.setdefault(
            f"ce_{platform}",
            ConfigEntryRow(entry_id=f"ce_{platform}", domain=source, title=source.title()),
        )
        reg.devices[device_id] = DeviceRow(
            device_id=device_id,
            config_entries=[f"ce_{platform}"],
            area_id=random.choice(list(reg.areas)),
        )
        reg.entities[entity_id] = EntityRow(
            entity_id=entity_id,
            platform=platform,
            unique_id=f"uid-named-{entity_id}",
            device_id=device_id,
        )
        reg.states[entity_id] = "off"

    return reg


def write_storage(reg: Registries, storage: Path) -> None:
    from app import entityfilter
    from app.entityfilter import EntityFilter

    entries = [
        {"entry_id": eid, "domain": "homekit", "title": title,
         "options": {"mode": mode, "filter": filt}}
        for eid, title, mode, filt in BRIDGES
    ]
    (storage / "core.config_entries").write_text(
        json.dumps({"version": 1, "data": {"entries": entries}})
    )

    all_entities = list(reg.entities.values())
    for eid, _title, _mode, filt in BRIDGES:
        spec = EntityFilter.from_options(filt)
        included = entityfilter.evaluate(spec, all_entities).included
        allocations = {}
        for aid, entity_id in enumerate(included, start=2):
            ent = reg.entities[entity_id]
            allocations[
                system_unique_id(ent.platform, ent.domain, ent.unique_id)
            ] = aid
        # Two kinds of real-world cruft the detectors should catch:
        # an allocation whose entity is gone, and one the filter now rejects.
        if eid == "sw01":
            allocations["tplink.switch.uid-deleted-1"] = 900
            allocations["tplink.switch.uid-deleted-2"] = 901
            for suppressed in [
                e.entity_id for e in all_entities
                if e.domain == "switch" and e.entity_category
            ][:5]:
                ent = reg.entities[suppressed]
                allocations[
                    system_unique_id(ent.platform, ent.domain, ent.unique_id)
                ] = 910 + len(allocations)
        (storage / f"homekit.{eid}.aids").write_text(
            json.dumps({"version": 1, "data": {"allocations": allocations}})
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--dump", help="write the snapshot JSON here and exit")
    args = parser.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="hkbm-dev-"))
    storage = tmp / ".storage"
    storage.mkdir()

    world = build_world()
    write_storage(world, storage)
    print(f"Synthetic instance: {len(world.entities)} entities -> {storage}")
    print(
        "WARNING: every entity id below is fabricated. Do not reason about your "
        "own Home Assistant from this data."
    )

    async def fake_fetch(self, timeout: float = 30.0) -> Registries:  # noqa: ANN001
        return world

    hass.HassWebSocket.fetch_registries = fake_fetch  # type: ignore[method-assign]

    config = Config(
        storage_dir=storage,
        hass_url="http://stub",
        supervisor_token="stub",
        refresh_seconds=86400,
        port=args.port,
        log_level="INFO",
        # Everything this harness serves is invented. Say so, loudly, all the
        # way through to the browser.
        synthetic=True,
    )

    if args.dump:
        from app.service import AuditService, to_json

        service = AuditService(config)
        snapshot = asyncio.run(service.refresh())
        Path(args.dump).write_text(json.dumps(to_json(snapshot, None), indent=2))
        print(f"Wrote {args.dump}")
        return

    from aiohttp import web

    web.run_app(create_app(config), port=args.port)


if __name__ == "__main__":
    main()
