"""Join the on-disk HomeKit state with the live registries into one model.

The join is the product. Everything the UI shows — duplicates, orphans,
round-trips, capacity — falls out of having filters, aid allocations, entities,
devices and config entries in the same object graph at the same time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from . import entityfilter
from .entityfilter import EntityRow
from .registry import Registries
from .storage import AidFile, HomeKitEntry, system_unique_id

_LOGGER = logging.getLogger(__name__)

# Integrations that put a device on HomeKit natively, before Home Assistant ever
# sees it. Re-exporting one of these through a HomeKit bridge is what makes a
# device show up twice in Home.app.
NATIVE_HOMEKIT_DOMAINS: frozenset[str] = frozenset(
    {
        "homekit_controller",  # anything already paired over HAP
        "hue",
        "lifx",
        "nanoleaf",
        "ecobee",
        "netatmo",
    }
)

# Helper integrations whose entities wrap other entities. Publishing one of these
# alongside its own members gives you a phantom accessory in Home.app.
GROUP_HELPER_PLATFORMS: frozenset[str] = frozenset(
    {"group", "light_group", "switch_as_x", "template"}
)

DEAD_STATES: frozenset[str] = frozenset({"unavailable", "unknown"})


@dataclass
class Publication:
    """One entity, published by one HomeKit entry.

    The two sources can disagree, and the disagreement is informative:

    ``via_filter and via_aid``
        Steady state — the filter wants it and HomeKit has published it.
    ``via_filter and not via_aid``
        Newly matched; HomeKit will allocate an aid on the next bridge start.
    ``via_aid and not via_filter``
        **Stale.** The filter no longer admits this entity, but the allocation
        is still on disk holding a slot. Usually means the entity gained an
        ``entity_category``, was hidden, or the filter was narrowed after the
        accessory was created.
    """

    entry_id: str
    bridge_title: str
    aid: int | None = None
    accessory_type: str | None = None
    via_filter: bool = False
    via_aid: bool = False

    @property
    def is_stale(self) -> bool:
        return self.via_aid and not self.via_filter


@dataclass
class EntityView:
    """Everything the master view knows about one entity."""

    entity_id: str
    name: str | None = None
    domain: str = ""
    area: str | None = None
    state: str | None = None
    platform: str = ""
    entity_category: str | None = None
    hidden: bool = False
    source_domain: str | None = None  # integration behind the entity's device
    source_title: str | None = None
    publications: list[Publication] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def publisher_count(self) -> int:
        return len(self.publications)

    @property
    def is_published(self) -> bool:
        return bool(self.publications)

    @property
    def live_publications(self) -> list[Publication]:
        """Publications the current filter still stands behind."""
        return [p for p in self.publications if p.via_filter]

    @property
    def stale_publications(self) -> list[Publication]:
        return [p for p in self.publications if p.is_stale]

    @property
    def severity(self) -> int:
        """Sort key: worst first."""
        order = {
            "duplicate": 0,
            "stale": 1,
            "orphan": 2,
            "round_trip": 3,
            "group": 4,
            "diagnostic": 5,
            "dead": 6,
        }
        return min((order.get(f, 9) for f in self.flags), default=9)


@dataclass
class BridgeView:
    entry_id: str
    title: str
    mode: str
    capacity: int | None
    filter_summary: str
    open_ended: bool
    predicted: list[str] = field(default_factory=list)  # from the filter
    allocated: list[str] = field(default_factory=list)  # from the .aids file
    unresolved_aids: int = 0  # allocations we could not map to an entity
    aid_file_seen: bool = False

    @property
    def accessory_count(self) -> int:
        """Prefer the real allocation count; fall back to the filter's prediction."""
        return len(self.allocated) if self.aid_file_seen else len(self.predicted)

    @property
    def count_is_exact(self) -> bool:
        return self.aid_file_seen

    @property
    def utilisation(self) -> float | None:
        if not self.capacity:
            return None
        return self.accessory_count / self.capacity


@dataclass
class Finding:
    severity: str  # critical | warning | info
    code: str
    title: str
    detail: str
    entities: list[str] = field(default_factory=list)
    bridges: list[str] = field(default_factory=list)


@dataclass
class Snapshot:
    bridges: list[BridgeView] = field(default_factory=list)
    entities: list[EntityView] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    generated_at: str = ""
    warnings: list[str] = field(default_factory=list)


def _aid_key_index(registries: Registries) -> dict[str, str]:
    """Map every possible HomeKit storage key back to its entity_id.

    HomeKit prefers ``platform.domain.unique_id`` and falls back to the
    ``entity_id``, so index both forms.
    """
    index: dict[str, str] = {}
    for entity_id, ent in registries.entities.items():
        index[entity_id] = entity_id
        if ent.unique_id and ent.platform:
            index[system_unique_id(ent.platform, ent.domain, ent.unique_id)] = entity_id
    return index


def _area_of(ent: EntityRow, registries: Registries) -> str | None:
    """An entity's area, falling back to its device's area."""
    if ent.area_id:
        return registries.areas.get(ent.area_id)
    if ent.device_id:
        device = registries.devices.get(ent.device_id)
        if device and device.area_id:
            return registries.areas.get(device.area_id)
    return None


def _summarise(entry: HomeKitEntry) -> str:
    """One-line description of what a bridge's filter actually does."""
    spec = entry.entity_filter
    parts: list[str] = []
    if spec.include_domains:
        parts.append("all " + ", ".join(sorted(spec.include_domains)))
    if spec.include_entity_globs:
        parts.append(f"{len(spec.include_entity_globs)} include glob(s)")
    if spec.include_entities:
        parts.append(f"{len(spec.include_entities)} named entity(ies)")
    if not parts:
        parts.append("everything")
    summary = "include " + " + ".join(parts)
    excludes: list[str] = []
    if spec.exclude_domains:
        excludes.append(", ".join(sorted(spec.exclude_domains)))
    if spec.exclude_entity_globs:
        excludes.append(f"{len(spec.exclude_entity_globs)} glob(s)")
    if spec.exclude_entities:
        excludes.append(f"{len(spec.exclude_entities)} named")
    if excludes:
        summary += ", except " + " + ".join(excludes)
    return summary


def _source_of(ent: EntityRow, registries: Registries) -> tuple[str | None, str | None]:
    """Resolve the integration that actually owns an entity's device."""
    if ent.device_id:
        device = registries.devices.get(ent.device_id)
        if device:
            for entry_id in device.config_entries:
                entry = registries.config_entries.get(entry_id)
                if entry:
                    return entry.domain, entry.title
    if ent.platform:
        return ent.platform, None
    return None, None


def build(
    entries: list[HomeKitEntry],
    aid_files: dict[str, AidFile],
    registries: Registries,
) -> Snapshot:
    snapshot = Snapshot()
    key_index = _aid_key_index(registries)
    all_entities = list(registries.entities.values())

    # entity_id -> publications
    published: dict[str, list[Publication]] = {}

    for entry in entries:
        if entry.disabled_by:
            continue

        result = entityfilter.evaluate(entry.entity_filter, all_entities)
        aid_file = aid_files.get(entry.entry_id)

        allocated: list[str] = []
        unresolved = 0
        aid_by_entity: dict[str, tuple[int, str | None]] = {}

        if aid_file:
            for alloc in aid_file.allocations:
                entity_id = key_index.get(alloc.storage_key)
                if entity_id is None:
                    unresolved += 1
                    continue
                allocated.append(entity_id)
                aid_by_entity[entity_id] = (alloc.aid, alloc.accessory_type)

        # An entity counts as published if either source says so: the aid file is
        # the record of what HomeKit did, the filter is what it intends to do.
        predicted_set = set(result.included)
        allocated_set = set(allocated)
        for entity_id in predicted_set | allocated_set:
            aid, acc_type = aid_by_entity.get(entity_id, (None, None))
            published.setdefault(entity_id, []).append(
                Publication(
                    entry_id=entry.entry_id,
                    bridge_title=entry.title,
                    aid=aid,
                    accessory_type=acc_type,
                    via_filter=entity_id in predicted_set,
                    via_aid=entity_id in allocated_set,
                )
            )

        snapshot.bridges.append(
            BridgeView(
                entry_id=entry.entry_id,
                title=entry.title,
                mode=entry.mode,
                capacity=entry.capacity,
                filter_summary=_summarise(entry),
                open_ended=entry.entity_filter.is_open_ended,
                predicted=result.included,
                allocated=sorted(allocated),
                unresolved_aids=unresolved,
                aid_file_seen=aid_file is not None,
            )
        )

        if aid_file is None:
            snapshot.warnings.append(
                f"No .aids file for {entry.title!r} — counts for it are predicted "
                "from its filter, not read from disk."
            )

    # Build the entity rows. Everything published, plus anything registry-known
    # that a bridge dropped for an interesting reason.
    for entity_id, publications in sorted(published.items()):
        ent = registries.entities.get(entity_id) or EntityRow(entity_id=entity_id)
        source_domain, source_title = _source_of(ent, registries)
        snapshot.entities.append(
            EntityView(
                entity_id=entity_id,
                name=ent.name,
                domain=ent.domain,
                area=_area_of(ent, registries),
                state=registries.states.get(entity_id),
                platform=ent.platform,
                entity_category=ent.entity_category,
                hidden=ent.hidden,
                source_domain=source_domain,
                source_title=source_title,
                publications=sorted(publications, key=lambda p: p.bridge_title),
            )
        )

    snapshot.bridges.sort(key=lambda b: (-b.accessory_count, b.title.lower()))
    return snapshot
