"""Faithful port of Home Assistant's entity filter + HomeKit's own extra rules.

Home Assistant's ``homeassistant.helpers.entityfilter`` decides which entities a
HomeKit bridge publishes. Reimplementing it is the only way to answer "what would
this bridge expose?" from outside the process, so this module mirrors its four
cases exactly. Keep it in sync with upstream; the tests pin the behaviour.

On top of the generic filter, the HomeKit integration applies two extra rules:

* only entities in ``SUPPORTED_DOMAINS`` are ever considered;
* hidden entities and entities with an ``entity_category`` (config / diagnostic)
  are dropped *unless* explicitly named in ``include_entities`` or matched by an
  ``include_entity_globs`` pattern.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from typing import Iterable

# Domains the HomeKit integration knows how to turn into an accessory.
SUPPORTED_DOMAINS: frozenset[str] = frozenset(
    {
        "alarm_control_panel",
        "automation",
        "binary_sensor",
        "button",
        "camera",
        "climate",
        "cover",
        "demo",
        "device_tracker",
        "fan",
        "humidifier",
        "input_boolean",
        "input_button",
        "input_select",
        "lawn_mower",
        "light",
        "lock",
        "media_player",
        "person",
        "remote",
        "scene",
        "script",
        "select",
        "sensor",
        "switch",
        "vacuum",
        "valve",
        "water_heater",
    }
)


def _domain_of(entity_id: str) -> str:
    return entity_id.partition(".")[0]


def _compile(patterns: Iterable[str]) -> list[re.Pattern[str]]:
    return [re.compile(fnmatch.translate(p)) for p in patterns]


def _matches(patterns: list[re.Pattern[str]], entity_id: str) -> bool:
    return any(p.match(entity_id) for p in patterns)


@dataclass(frozen=True)
class EntityFilter:
    """The ``filter`` dict stored on a HomeKit config entry's options."""

    include_domains: frozenset[str] = frozenset()
    include_entities: frozenset[str] = frozenset()
    include_entity_globs: tuple[str, ...] = ()
    exclude_domains: frozenset[str] = frozenset()
    exclude_entities: frozenset[str] = frozenset()
    exclude_entity_globs: tuple[str, ...] = ()

    @classmethod
    def from_options(cls, raw: dict | None) -> "EntityFilter":
        raw = raw or {}
        return cls(
            include_domains=frozenset(raw.get("include_domains") or ()),
            include_entities=frozenset(raw.get("include_entities") or ()),
            include_entity_globs=tuple(raw.get("include_entity_globs") or ()),
            exclude_domains=frozenset(raw.get("exclude_domains") or ()),
            exclude_entities=frozenset(raw.get("exclude_entities") or ()),
            exclude_entity_globs=tuple(raw.get("exclude_entity_globs") or ()),
        )

    @property
    def has_include(self) -> bool:
        return bool(
            self.include_entities or self.include_domains or self.include_entity_globs
        )

    @property
    def has_exclude(self) -> bool:
        return bool(
            self.exclude_entities or self.exclude_domains or self.exclude_entity_globs
        )

    @property
    def is_open_ended(self) -> bool:
        """True when the filter admits entities that do not exist yet.

        A domain- or glob-based rule with no explicit include list keeps adopting
        new entities as you create them. That is what makes a bridge creep toward
        the 150-accessory ceiling without anyone touching it.
        """
        if self.has_include:
            return bool(self.include_domains or self.include_entity_globs)
        return True  # exclude-only, or no filter at all: everything new is in


class CompiledFilter:
    """Callable form of :class:`EntityFilter`, mirroring ``generate_filter``."""

    def __init__(self, spec: EntityFilter) -> None:
        self.spec = spec
        self._inc_globs = _compile(spec.include_entity_globs)
        self._exc_globs = _compile(spec.exclude_entity_globs)

    def __call__(self, entity_id: str) -> bool:
        spec = self.spec
        domain = _domain_of(entity_id)
        have_include, have_exclude = spec.has_include, spec.has_exclude

        # Case 1 — no filter at all.
        if not have_include and not have_exclude:
            return True

        # Case 2 — includes only.
        if have_include and not have_exclude:
            return (
                entity_id in spec.include_entities
                or domain in spec.include_domains
                or _matches(self._inc_globs, entity_id)
            )

        # Case 3 — excludes only.
        if not have_include and have_exclude:
            return not (
                entity_id in spec.exclude_entities
                or domain in spec.exclude_domains
                or _matches(self._exc_globs, entity_id)
            )

        # Case 4 — both. Include domains/globs win, then excludes narrow them.
        if spec.include_domains or spec.include_entity_globs:
            if domain in spec.include_domains:
                return not (
                    entity_id in spec.exclude_entities
                    or _matches(self._exc_globs, entity_id)
                )
            if _matches(self._inc_globs, entity_id):
                return entity_id not in spec.exclude_entities
            return entity_id in spec.include_entities

        # Include entities only, alongside exclude domains/globs.
        if spec.exclude_domains or spec.exclude_entity_globs:
            if entity_id in spec.include_entities:
                return True
            return not (
                domain in spec.exclude_domains or _matches(self._exc_globs, entity_id)
            )

        # Both lists are plain entity lists: the include list is authoritative.
        return entity_id in spec.include_entities


@dataclass
class EntityRow:
    """Just enough of an entity registry entry to run the filter."""

    entity_id: str
    platform: str = ""
    unique_id: str | None = None
    hidden: bool = False
    entity_category: str | None = None
    device_id: str | None = None
    area_id: str | None = None
    name: str | None = None
    disabled: bool = False

    @property
    def domain(self) -> str:
        return _domain_of(self.entity_id)


@dataclass
class BridgeFilterResult:
    included: list[str] = field(default_factory=list)
    # entity_id -> why HomeKit dropped it, for entities the raw filter admitted
    suppressed: dict[str, str] = field(default_factory=dict)


def evaluate(spec: EntityFilter, entities: Iterable[EntityRow]) -> BridgeFilterResult:
    """Apply the generic filter plus HomeKit's own supported/hidden/category rules."""
    compiled = CompiledFilter(spec)
    inc_globs = _compile(spec.include_entity_globs)
    result = BridgeFilterResult()

    for ent in entities:
        if ent.disabled:
            continue
        if ent.domain not in SUPPORTED_DOMAINS:
            continue
        if not compiled(ent.entity_id):
            continue

        # HomeKit keeps hidden/categorised entities out unless you asked for them
        # by name or by glob — a domain include is not enough.
        explicit = ent.entity_id in spec.include_entities or _matches(
            inc_globs, ent.entity_id
        )
        if not explicit:
            if ent.hidden:
                result.suppressed[ent.entity_id] = "hidden"
                continue
            if ent.entity_category:
                result.suppressed[ent.entity_id] = ent.entity_category
                continue

        result.included.append(ent.entity_id)

    result.included.sort()
    return result
