"""Readers for the two on-disk sources of truth.

Neither of these is reachable through the Home Assistant API, which is the whole
reason this is an add-on:

``.storage/core.config_entries``
    Holds each HomeKit entry's real ``options.filter``. The websocket
    ``config_entries/get`` command omits options entirely, and probing the
    options flow only returns its first step (mode / include_exclude_mode /
    domains) — never the entity lists.

``.storage/homekit.<entry_id>.aids``
    The ground truth for what a bridge has *actually* published: a map of
    storage key -> accessory id. Two bridges holding an allocation for the same
    entity is a confirmed duplicate, not an inference.

Both are opened read-only. The add-on maps ``homeassistant_config:ro``.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from .entityfilter import EntityFilter

_LOGGER = logging.getLogger(__name__)

AIDS_RE = re.compile(r"^homekit\.(?P<entry_id>[0-9a-zA-Z_-]+)\.aids$")


def _read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _unwrap(payload: dict) -> dict:
    """HA storage files wrap their payload in a versioned envelope."""
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


@dataclass
class HomeKitEntry:
    """A HomeKit config entry as it exists on disk."""

    entry_id: str
    title: str
    mode: str  # "bridge" | "accessory"
    entity_filter: EntityFilter
    entity_config: dict = field(default_factory=dict)
    disabled_by: str | None = None

    @property
    def is_bridge(self) -> bool:
        return self.mode != "accessory"

    @property
    def capacity(self) -> int | None:
        """HAP caps a bridge at 150 accessories. Accessory mode is always one."""
        return 150 if self.is_bridge else None


@dataclass
class AidAllocation:
    """One row of a ``homekit.<entry_id>.aids`` file."""

    storage_key: str
    aid: int
    accessory_type: str | None = None


@dataclass
class AidFile:
    entry_id: str
    path: Path
    allocations: list[AidAllocation] = field(default_factory=list)
    mtime: float = 0.0

    @property
    def aid_count(self) -> int:
        return len(self.allocations)


def read_config_entries(storage_dir: Path) -> list[HomeKitEntry]:
    """Parse every ``homekit`` config entry out of ``core.config_entries``."""
    path = storage_dir / "core.config_entries"
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} not found. Is homeassistant_config mapped into the add-on?"
        )

    payload = _unwrap(_read_json(path))
    entries: list[HomeKitEntry] = []

    for raw in payload.get("entries", []):
        if raw.get("domain") != "homekit":
            continue
        options = raw.get("options") or {}
        entries.append(
            HomeKitEntry(
                entry_id=raw["entry_id"],
                title=raw.get("title") or raw["entry_id"],
                mode=options.get("mode", "bridge"),
                entity_filter=EntityFilter.from_options(options.get("filter")),
                entity_config=options.get("entity_config") or {},
                disabled_by=raw.get("disabled_by"),
            )
        )

    entries.sort(key=lambda e: e.title.lower())
    _LOGGER.info("Read %d HomeKit config entries from %s", len(entries), path)
    return entries


def read_aid_files(storage_dir: Path) -> dict[str, AidFile]:
    """Read every ``homekit.*.aids`` file, keyed by entry_id.

    The allocation map is keyed by the entity's *system unique id*
    (``platform.domain.unique_id``) when it has one, and by its ``entity_id``
    when it does not — see ``homekit/aidmanager.py`` upstream. Resolving those
    keys back to entity_ids needs the entity registry, so it happens in
    :mod:`.model` rather than here.
    """
    files: dict[str, AidFile] = {}

    for path in sorted(storage_dir.glob("homekit.*.aids")):
        match = AIDS_RE.match(path.name)
        if not match:
            continue
        entry_id = match.group("entry_id")
        try:
            data = _unwrap(_read_json(path))
        except (OSError, json.JSONDecodeError) as err:
            _LOGGER.warning("Could not read %s: %s", path.name, err)
            continue

        allocations_raw = data.get("allocations") or {}
        types_raw = data.get("accessory_types") or {}
        allocations = [
            AidAllocation(
                storage_key=key,
                aid=int(aid),
                accessory_type=types_raw.get(key),
            )
            for key, aid in allocations_raw.items()
        ]
        allocations.sort(key=lambda a: a.aid)

        files[entry_id] = AidFile(
            entry_id=entry_id,
            path=path,
            allocations=allocations,
            mtime=path.stat().st_mtime,
        )

    _LOGGER.info("Read %d aid files from %s", len(files), storage_dir)
    return files


def system_unique_id(platform: str, domain: str, unique_id: str) -> str:
    """Rebuild HomeKit's storage key. Mirrors ``get_system_unique_id`` upstream."""
    return f"{platform}.{domain}.{unique_id}"
