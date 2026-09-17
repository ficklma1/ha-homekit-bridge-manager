"""Plain data types for Home Assistant's registries.

Deliberately free of I/O and third-party imports so the model and the detectors
can be tested without a network stack. :mod:`.hass` does the fetching and
returns these.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .entityfilter import EntityRow

__all__ = ["DeviceRow", "ConfigEntryRow", "Registries", "EntityRow"]


@dataclass
class DeviceRow:
    device_id: str
    name: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    area_id: str | None = None
    config_entries: list[str] = field(default_factory=list)
    via_device_id: str | None = None


@dataclass
class ConfigEntryRow:
    entry_id: str
    domain: str
    title: str


@dataclass
class Registries:
    entities: dict[str, EntityRow] = field(default_factory=dict)
    devices: dict[str, DeviceRow] = field(default_factory=dict)
    areas: dict[str, str] = field(default_factory=dict)
    config_entries: dict[str, ConfigEntryRow] = field(default_factory=dict)
    states: dict[str, str] = field(default_factory=dict)
