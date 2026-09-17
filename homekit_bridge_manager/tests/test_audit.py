"""End-to-end tests over a fixture that mirrors Mark's real instance.

Every bridge, mode and filter shape here was taken from a live audit of a
15-entry HomeKit setup: ten bridges, five accessory-mode entries, two bridges
sharing the binary_sensor domain, and three devices imported over HomeKit
Controller and re-exported.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import detectors, model  # noqa: E402
from app.entityfilter import EntityRow  # noqa: E402
from app.registry import ConfigEntryRow, DeviceRow, Registries  # noqa: E402
from app.storage import (  # noqa: E402
    read_aid_files,
    read_config_entries,
    system_unique_id,
)

# --------------------------------------------------------------------------- #
# Fixture: the .storage directory
# --------------------------------------------------------------------------- #

BRIDGES = [
    # entry_id, title, mode, filter
    ("sw01", "HASS Bridge: Switches", "bridge",
     {"include_domains": ["switch"], "exclude_entities": ["switch.ignore_me"]}),
    ("li01", "HASS Bridge: Lights", "bridge",
     {"include_domains": ["light"], "exclude_entities": []}),
    ("bs01", "HASS Bridge: Binary Sensors", "bridge",
     {"include_entities": [
         "binary_sensor.front_door",
         "binary_sensor.main_detector_smoke_alarm",
         "binary_sensor.mac_mini_ipad_active",
     ]}),
    ("sd01", "HASS Bridge: Smoke Detector", "bridge",
     {"include_entities": ["binary_sensor.main_detector_smoke_alarm"]}),
    ("gar01", "HASS Bridge: Garage", "accessory",
     {"include_entities": ["cover.msg100_5218_garage_door"]}),
    ("roku75", "75in Roku TV", "accessory",
     {"include_entities": ["media_player.westinghouse_ce05x_6b6d"]}),
]

# entity_id, platform, unique_id, category, hidden, device_id, state
ENTITIES = [
    ("light.string_lights", "lutron_caseta", "u-light-1", None, False, "dev_lutron", "on"),
    ("light.hue_color_downlight_1", "hue", "u-hue-1", None, False, "dev_hue", "on"),
    ("light.theater_hue_lights", "group", "u-grp-1", None, False, None, "on"),
    ("switch.master_bathroom_exhaust_fan", "isy994", "u-sw-1", None, False, "dev_isy", "off"),
    ("switch.main_detector_identifying", "kidde", "u-sw-2", "config", False, "dev_kidde", "off"),
    ("switch.smart_plug_led", "tplink", "u-sw-3", "config", False, "dev_tplink", "off"),
    ("switch.ignore_me", "tplink", "u-sw-4", None, False, "dev_tplink", "off"),
    ("switch.tapo_smart_bulb_overheated", "tplink", "u-sw-5", None, False, "dev_tplink", "unavailable"),
    ("binary_sensor.front_door", "alarmdecoder", "u-bs-1", None, False, "dev_ad", "off"),
    ("binary_sensor.main_detector_smoke_alarm", "kidde", "u-bs-2", None, False, "dev_kidde", "off"),
    ("binary_sensor.mac_mini_ipad_active", "mobile_app", "u-bs-3", None, False, None, "unavailable"),
    ("cover.msg100_5218_garage_door", "homekit_controller", "u-cv-1", None, False, "dev_meross", "closed"),
    ("media_player.westinghouse_ce05x_6b6d", "homekit_controller", "u-mp-1", None, False, "dev_roku", "off"),
]

DEVICES = {
    "dev_lutron": "lutron_caseta",
    "dev_hue": "hue",
    "dev_isy": "isy994",
    "dev_kidde": "kidde",
    "dev_tplink": "tplink",
    "dev_ad": "alarmdecoder",
    "dev_meross": "homekit_controller",
    "dev_roku": "homekit_controller",
}

# Which entities each bridge has actually allocated an aid for.
ALLOCATIONS = {
    "sw01": [
        "switch.master_bathroom_exhaust_fan",
        "switch.main_detector_identifying",
        "switch.smart_plug_led",
        "switch.tapo_smart_bulb_overheated",
    ],
    "li01": [
        "light.string_lights",
        "light.hue_color_downlight_1",
        "light.theater_hue_lights",
    ],
    "bs01": [
        "binary_sensor.front_door",
        "binary_sensor.main_detector_smoke_alarm",
        "binary_sensor.mac_mini_ipad_active",
    ],
    "sd01": ["binary_sensor.main_detector_smoke_alarm"],
    "gar01": ["cover.msg100_5218_garage_door"],
    "roku75": ["media_player.westinghouse_ce05x_6b6d"],
}


@pytest.fixture
def storage_dir(tmp_path: Path) -> Path:
    storage = tmp_path / ".storage"
    storage.mkdir()

    entries = [
        {
            "entry_id": entry_id,
            "domain": "homekit",
            "title": title,
            "options": {"mode": mode, "filter": filt},
        }
        for entry_id, title, mode, filt in BRIDGES
    ]
    # A non-HomeKit entry, to prove we skip it.
    entries.append({"entry_id": "mqtt1", "domain": "mqtt", "title": "Mosquitto", "options": {}})

    (storage / "core.config_entries").write_text(
        json.dumps({"version": 1, "data": {"entries": entries}})
    )

    lookup = {e[0]: e for e in ENTITIES}
    for entry_id, entity_ids in ALLOCATIONS.items():
        allocations = {}
        for aid, entity_id in enumerate(entity_ids, start=2):
            _, platform, unique_id, *_ = lookup[entity_id]
            domain = entity_id.split(".")[0]
            allocations[system_unique_id(platform, domain, unique_id)] = aid
        # One dangling allocation on the switches bridge: an entity that is gone.
        if entry_id == "sw01":
            allocations["tplink.switch.u-deleted-99"] = 99
        (storage / f"homekit.{entry_id}.aids").write_text(
            json.dumps({"version": 1, "data": {"allocations": allocations}})
        )

    return storage


@pytest.fixture
def registries() -> Registries:
    reg = Registries()
    for entity_id, platform, unique_id, category, hidden, device_id, state in ENTITIES:
        reg.entities[entity_id] = EntityRow(
            entity_id=entity_id,
            platform=platform,
            unique_id=unique_id,
            entity_category=category,
            hidden=hidden,
            device_id=device_id,
            area_id="area_x",
        )
        reg.states[entity_id] = state
    for device_id, domain in DEVICES.items():
        reg.devices[device_id] = DeviceRow(
            device_id=device_id, config_entries=[f"ce_{domain}"], area_id="area_x"
        )
        reg.config_entries[f"ce_{domain}"] = ConfigEntryRow(
            entry_id=f"ce_{domain}", domain=domain, title=domain.title()
        )
    reg.areas["area_x"] = "Test Area"
    return reg


@pytest.fixture
def snapshot(storage_dir: Path, registries: Registries):
    entries = read_config_entries(storage_dir)
    aids = read_aid_files(storage_dir)
    return detectors.run(model.build(entries, aids, registries))


# --------------------------------------------------------------------------- #
# Storage layer
# --------------------------------------------------------------------------- #

def test_reads_only_homekit_entries(storage_dir: Path):
    entries = read_config_entries(storage_dir)
    assert len(entries) == len(BRIDGES)
    assert {e.entry_id for e in entries} == {b[0] for b in BRIDGES}


def test_filters_survive_the_round_trip(storage_dir: Path):
    entries = {e.entry_id: e for e in read_config_entries(storage_dir)}
    switches = entries["sw01"].entity_filter
    assert switches.include_domains == frozenset({"switch"})
    assert switches.exclude_entities == frozenset({"switch.ignore_me"})
    assert switches.is_open_ended is True

    smoke = entries["sd01"].entity_filter
    assert smoke.is_open_ended is False  # named entities only


def test_aid_files_parse_and_sort(storage_dir: Path):
    aids = read_aid_files(storage_dir)
    assert set(aids) == set(ALLOCATIONS)
    sw = aids["sw01"]
    assert sw.aid_count == len(ALLOCATIONS["sw01"]) + 1  # plus the dangling one
    assert [a.aid for a in sw.allocations] == sorted(a.aid for a in sw.allocations)


def test_accessory_mode_has_no_capacity(storage_dir: Path):
    entries = {e.entry_id: e for e in read_config_entries(storage_dir)}
    assert entries["gar01"].capacity is None
    assert entries["sw01"].capacity == 150


# --------------------------------------------------------------------------- #
# The join
# --------------------------------------------------------------------------- #

def test_aid_keys_resolve_back_to_entity_ids(snapshot):
    lights = next(b for b in snapshot.bridges if b.entry_id == "li01")
    assert set(lights.allocated) == set(ALLOCATIONS["li01"])
    assert lights.count_is_exact is True


def test_dangling_allocation_is_counted_not_resolved(snapshot):
    switches = next(b for b in snapshot.bridges if b.entry_id == "sw01")
    assert switches.unresolved_aids == 1


def test_excluded_entity_is_not_published(snapshot):
    assert not any(e.entity_id == "switch.ignore_me" for e in snapshot.entities)


def test_area_falls_back_to_the_device(snapshot):
    ent = next(e for e in snapshot.entities if e.entity_id == "light.string_lights")
    assert ent.area == "Test Area"


# --------------------------------------------------------------------------- #
# Detectors
# --------------------------------------------------------------------------- #

def _codes(snapshot) -> set[str]:
    return {f.code for f in snapshot.findings}


def test_finds_the_cross_bridge_duplicate(snapshot):
    dupe = next(
        e for e in snapshot.entities
        if e.entity_id == "binary_sensor.main_detector_smoke_alarm"
    )
    assert "duplicate" in dupe.flags
    assert dupe.publisher_count == 2
    assert "duplicate_publication" in _codes(snapshot)


def test_finds_round_trips_from_homekit_controller(snapshot):
    finding = next(f for f in snapshot.findings if f.code == "round_trip")
    # Both HomeKit Controller imports, plus the Hue light — Hue puts the bulb on
    # HomeKit itself, so re-exporting it is the same round trip.
    assert set(finding.entities) == {
        "cover.msg100_5218_garage_door",
        "media_player.westinghouse_ce05x_6b6d",
        "light.hue_color_downlight_1",
    }


def test_hue_light_is_a_round_trip_too(snapshot):
    hue = next(e for e in snapshot.entities if e.entity_id == "light.hue_color_downlight_1")
    assert "round_trip" in hue.flags


def test_group_helper_is_flagged(snapshot):
    grp = next(e for e in snapshot.entities if e.entity_id == "light.theater_hue_lights")
    assert "group" in grp.flags
    assert "group_helper" in _codes(snapshot)


def test_config_entities_are_not_live_published(snapshot):
    """A config entity caught only by a domain include is dropped by HomeKit.

    Both of these have a stale aid on disk, so they still appear as rows — but
    the current filter does not stand behind either, so neither is live.
    """
    for entity_id in ("switch.main_detector_identifying", "switch.smart_plug_led"):
        ent = next(e for e in snapshot.entities if e.entity_id == entity_id)
        assert ent.live_publications == []
        assert ent.stale_publications != []


def test_stale_allocations_are_flagged(snapshot):
    """Allocated on disk, rejected by the filter today — the accessory lingers."""
    ent = next(
        e for e in snapshot.entities if e.entity_id == "switch.main_detector_identifying"
    )
    assert "stale" in ent.flags
    assert "config" in ent.notes[0]

    finding = next(f for f in snapshot.findings if f.code == "stale_allocation")
    assert set(finding.entities) == {
        "switch.main_detector_identifying",
        "switch.smart_plug_led",
    }


def test_stale_entities_are_not_counted_as_duplicates(snapshot):
    """A stale allocation is its own problem, not a cross-bridge duplicate."""
    dupes = [f for f in snapshot.findings if f.code == "duplicate_publication"]
    flagged = {eid for f in dupes for eid in f.entities}
    assert "switch.main_detector_identifying" not in flagged


def test_diagnostic_leak_only_fires_on_live_publications(snapshot):
    """Nothing in this fixture names a config entity explicitly, so no leak."""
    assert "diagnostic_leak" not in _codes(snapshot)


def test_unavailable_published_entity_is_flagged_dead(snapshot):
    dead = next(
        e for e in snapshot.entities
        if e.entity_id == "binary_sensor.mac_mini_ipad_active"
    )
    assert "dead" in dead.flags
    assert "dead_accessory" in _codes(snapshot)


def test_orphan_aid_is_reported(snapshot):
    assert "orphan_aid" in _codes(snapshot)


def test_mode_mismatch_catches_the_garage_entry(snapshot):
    finding = next(f for f in snapshot.findings if f.code == "mode_mismatch")
    assert "HASS Bridge: Garage" in finding.bridges


def test_findings_are_sorted_worst_first(snapshot):
    order = ["critical", "warning", "info"]
    seen = [order.index(f.severity) for f in snapshot.findings]
    assert seen == sorted(seen)


def test_clean_entity_has_no_flags(snapshot):
    clean = next(e for e in snapshot.entities if e.entity_id == "binary_sensor.front_door")
    assert clean.flags == []
