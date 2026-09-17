"""The rules. Each one tags entities and emits findings for the audit panel.

Order matters only for presentation: ``run`` applies every detector, then sorts
findings by severity.
"""

from __future__ import annotations

from .model import (
    CONFIRMED_HOMEKIT_DOMAINS,
    DEAD_STATES,
    GROUP_HELPER_PLATFORMS,
    HOMEKIT_CAPABLE_DOMAINS,
    EntityView,
    Finding,
    Snapshot,
)

def plural(count: int, singular: str, many: str | None = None) -> str:
    """"1 accessory" / "3 accessories" — cheaper to read than "accessory(ies)"."""
    word = singular if count == 1 else (many or singular + "s")
    return f"{count} {word}"


# Fraction of a bridge's 150-accessory capacity at which we start nagging.
WARN_AT = 0.60
CRITICAL_AT = 0.85


def detect_duplicates(snapshot: Snapshot) -> list[Finding]:
    """The same entity published by two or more HomeKit entries.

    This is a confirmed duplicate rather than a guess whenever both bridges hold
    an aid allocation for it — Home.app is showing two tiles for one thing.
    """
    findings: list[Finding] = []
    offenders: list[EntityView] = []
    pairs: dict[tuple[str, ...], list[str]] = {}

    for ent in snapshot.entities:
        live = ent.live_publications
        if len(live) < 2:
            continue
        ent.flags.append("duplicate")
        bridges = tuple(sorted(p.bridge_title for p in live))
        confirmed = sum(1 for p in live if p.aid is not None) >= 2
        ent.notes.append(
            "Published by " + ", ".join(bridges)
            + (" — both hold a live accessory id." if confirmed else " — filters overlap.")
        )
        offenders.append(ent)
        pairs.setdefault(bridges, []).append(ent.entity_id)

    for bridges, entity_ids in sorted(pairs.items(), key=lambda kv: -len(kv[1])):
        findings.append(
            Finding(
                severity="critical",
                code="duplicate_publication",
                title=plural(len(entity_ids), "entity", "entities")
                + " published by both "
                + " and ".join(bridges),
                detail=(
                    "Each appears twice in Home.app with two different accessory "
                    "ids. Neither bridge can see the other's filter, so Home "
                    "Assistant will never warn you about this."
                ),
                entities=sorted(entity_ids),
                bridges=list(bridges),
            )
        )
    return findings


def detect_stale_allocations(snapshot: Snapshot) -> list[Finding]:
    """Accessories still on a bridge that its filter would no longer admit.

    The entity exists and the aid is allocated, but re-evaluating the filter
    says no. It happens when an integration gives an entity an
    ``entity_category`` in a later release, when an entity gets hidden, or when
    a filter is narrowed after the fact. HomeKit does not retroactively remove
    the accessory, so it lingers in Home.app and keeps its slot.
    """
    offenders = []
    for ent in snapshot.entities:
        stale = ent.stale_publications
        if not stale or ent.live_publications:
            continue
        ent.flags.append("stale")
        reason = ent.entity_category or ("hidden" if ent.hidden else "filter narrowed")
        ent.notes.append(
            "Still allocated on "
            + ", ".join(p.bridge_title for p in stale)
            + f", but the current filter excludes it ({reason}). Restarting the "
            "bridge will not remove it."
        )
        offenders.append(ent)

    if not offenders:
        return []
    return [
        Finding(
            severity="warning",
            code="stale_allocation",
            title=plural(len(offenders), "accessory", "accessories")
            + " outlived the filter that created "
            + ("it" if len(offenders) == 1 else "them"),
            detail=(
                "These hold live accessory ids that the bridge's current filter "
                "would not grant today. They still appear in Home.app and still "
                "count against the 150-accessory ceiling."
            ),
            entities=sorted(e.entity_id for e in offenders),
        )
    ]


def detect_round_trips(snapshot: Snapshot) -> list[Finding]:
    """Devices already on HomeKit, re-exported through a bridge.

    Split by what Home Assistant can actually prove:

    ``homekit_controller``
        Provable. The integration exists only because the accessory was paired
        over HAP, so re-exporting it is definitionally a second copy.

    HomeKit-capable brands (hue, lifx, nanoleaf, ecobee, netatmo)
        An inference. The brand ships HomeKit support, but nothing inside Home
        Assistant can see whether this user paired this bridge in Home.app. If
        they never did, the device is not on HomeKit twice and the finding is
        wrong. Reported as a question, and dismissible per integration.
    """
    confirmed: list[EntityView] = []
    likely: list[EntityView] = []
    dismissed: set[str] = set()

    for ent in snapshot.entities:
        live = ent.live_publications
        if not live:
            continue

        source = (ent.source_domain or "").lower()
        label = ent.source_title or ent.source_domain or source
        bridges = ", ".join(p.bridge_title for p in live)

        if source in CONFIRMED_HOMEKIT_DOMAINS:
            ent.flags.append("round_trip")
            ent.notes.append(
                f"Paired over HAP via {label}, then re-exported by {bridges}. "
                "Home.app shows this device twice."
            )
            confirmed.append(ent)
        elif source in HOMEKIT_CAPABLE_DOMAINS:
            if source in snapshot.assume_not_in_homekit:
                dismissed.add(source)
                continue
            ent.flags.append("round_trip_likely")
            ent.notes.append(
                f"{label} ships HomeKit support. If that bridge is paired in "
                f"Home.app, {bridges} publishes a second copy of this device. "
                "If it is not paired, this row is fine."
            )
            likely.append(ent)

    findings: list[Finding] = []

    if confirmed:
        by_source = sorted({e.source_domain or "?" for e in confirmed})
        findings.append(
            Finding(
                severity="critical",
                code="round_trip",
                title=plural(len(confirmed), "accessory", "accessories")
                + " imported from HomeKit, then sent back",
                detail=(
                    "These are paired over HAP (" + ", ".join(by_source) + "), so "
                    "Home Assistant knows they are already on HomeKit. Re-exporting "
                    "them through a bridge means Home.app shows each device twice — "
                    "once natively, once as a Home Assistant accessory."
                ),
                entities=sorted(e.entity_id for e in confirmed),
            )
        )

    if likely:
        by_source = sorted({e.source_domain or "?" for e in likely})
        findings.append(
            Finding(
                severity="warning",
                code="round_trip_likely",
                title=plural(len(likely), "accessory", "accessories")
                + " may already be on HomeKit natively",
                detail=(
                    "These come from integrations that ship HomeKit support ("
                    + ", ".join(by_source)
                    + "). Home Assistant cannot see inside Home.app, so this is a "
                    "question, not a verdict: if you paired that bridge with Apple "
                    "Home, each device is showing up twice. If you did not, these "
                    "are fine — list the integration under assume_not_in_homekit "
                    "in the add-on options to stop asking."
                ),
                entities=sorted(e.entity_id for e in likely),
            )
        )

    if dismissed:
        findings.append(
            Finding(
                severity="info",
                code="round_trip_dismissed",
                title="Round-trip checks skipped for "
                + ", ".join(sorted(dismissed)),
                detail=(
                    "You have told the add-on these integrations are not paired in "
                    "Home.app, so their entities are not flagged as round-trips. "
                    "Remove them from assume_not_in_homekit to check again."
                ),
            )
        )

    return findings


def detect_group_helpers(snapshot: Snapshot) -> list[Finding]:
    """Group/template helpers published alongside the entities they contain."""
    offenders = []
    for ent in snapshot.entities:
        if not ent.live_publications:
            continue
        if ent.platform not in GROUP_HELPER_PLATFORMS:
            continue
        ent.flags.append("group")
        ent.notes.append(
            f"A {ent.platform} helper — in Home.app it looks like an extra device "
            "that mysteriously moves other devices."
        )
        offenders.append(ent)

    if not offenders:
        return []
    return [
        Finding(
            severity="warning",
            code="group_helper",
            title=plural(len(offenders), "group/template helper")
            + " exported alongside their members",
            detail=(
                "Open-ended domain filters sweep helper entities up with real ones. "
                "Excluding the helpers usually makes Home.app much less confusing."
            ),
            entities=sorted(e.entity_id for e in offenders),
        )
    ]


def detect_diagnostic_leaks(snapshot: Snapshot) -> list[Finding]:
    """Config/diagnostic entities that were explicitly included anyway.

    HomeKit drops these by default, so anything here got named in an include
    list or matched a glob — usually by accident.
    """
    offenders = [
        ent
        for ent in snapshot.entities
        if ent.live_publications and (ent.entity_category or ent.hidden)
    ]
    for ent in offenders:
        ent.flags.append("diagnostic")
        ent.notes.append(
            f"{ent.entity_category or 'hidden'} entity — HomeKit only publishes these "
            "when they are named explicitly or matched by a glob."
        )
    if not offenders:
        return []
    return [
        Finding(
            severity="warning",
            code="diagnostic_leak",
            title=plural(len(offenders), "config/diagnostic entity", "config/diagnostic entities")
            + " exposed to HomeKit",
            detail=(
                "Identify buttons, LED toggles and similar clutter the Home.app room "
                "views and burn accessory slots that count against the 150 limit."
            ),
            entities=sorted(e.entity_id for e in offenders),
        )
    ]


def detect_orphans(snapshot: Snapshot) -> list[Finding]:
    """Accessories whose entity is gone, or long-term unavailable."""
    gone: list[str] = []
    dead: list[str] = []

    for bridge in snapshot.bridges:
        if bridge.unresolved_aids:
            gone.append(f"{bridge.title} ({bridge.unresolved_aids})")

    for ent in snapshot.entities:
        if ent.live_publications and ent.state in DEAD_STATES:
            ent.flags.append("dead")
            ent.notes.append(
                f"Currently {ent.state} — shows as 'No Response' in Home.app while "
                "still holding its accessory id."
            )
            dead.append(ent.entity_id)

    findings: list[Finding] = []
    if gone:
        findings.append(
            Finding(
                severity="warning",
                code="orphan_aid",
                title="Accessory ids allocated to entities that no longer exist",
                detail=(
                    "These allocations sit in the bridge's .aids file with nothing "
                    "behind them: " + ", ".join(gone) + ". They still count toward "
                    "the 150-accessory ceiling."
                ),
                bridges=[g.split(" (")[0] for g in gone],
            )
        )
    if dead:
        findings.append(
            Finding(
                severity="warning",
                code="dead_accessory",
                title=plural(len(dead), "published entity", "published entities")
                + (" is" if len(dead) == 1 else " are")
                + " unavailable right now",
                detail=(
                    "Each is a 'No Response' tile in Home.app. If any have been dead "
                    "for weeks, they are the cheapest accessories to reclaim."
                ),
                entities=sorted(dead),
            )
        )
    return findings


def detect_capacity(snapshot: Snapshot) -> list[Finding]:
    """Bridges approaching HAP's hard ceiling of 150 accessories."""
    findings: list[Finding] = []
    for bridge in snapshot.bridges:
        util = bridge.utilisation
        if util is None:
            continue
        if util < WARN_AT:
            continue
        severity = "critical" if util >= CRITICAL_AT else "warning"
        growth = (
            " Its filter is open-ended, so it keeps adopting new entities on its own."
            if bridge.open_ended
            else ""
        )
        findings.append(
            Finding(
                severity=severity,
                code="capacity",
                title=(
                    f"{bridge.title} is at {bridge.accessory_count}/{bridge.capacity} "
                    f"accessories ({util:.0%})"
                ),
                detail=(
                    "HomeKit caps a bridge at 150 accessories. Crossing it takes the "
                    "whole bridge offline in Home.app, not just the 151st device."
                    + growth
                ),
                bridges=[bridge.title],
            )
        )
    return findings


def detect_mode_mismatch(snapshot: Snapshot) -> list[Finding]:
    """Entries named like a bridge but configured as a single accessory."""
    offenders = [
        b.title
        for b in snapshot.bridges
        if b.mode == "accessory" and "bridge" in b.title.lower()
    ]
    if not offenders:
        return []
    return [
        Finding(
            severity="info",
            code="mode_mismatch",
            title="Entry named like a bridge but running in accessory mode",
            detail=(
                "Harmless today, confusing in six months: "
                + ", ".join(offenders)
                + ". Accessory mode publishes exactly one unbridged accessory."
            ),
            bridges=offenders,
        )
    ]


DETECTORS = (
    detect_duplicates,
    detect_round_trips,
    detect_stale_allocations,
    detect_capacity,
    detect_group_helpers,
    detect_diagnostic_leaks,
    detect_orphans,
    detect_mode_mismatch,
)

_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


def run(snapshot: Snapshot) -> Snapshot:
    """Apply every detector in place and sort the findings."""
    findings: list[Finding] = []
    for detector in DETECTORS:
        findings.extend(detector(snapshot))

    findings.sort(key=lambda f: (_SEVERITY_ORDER.get(f.severity, 9), f.code))
    snapshot.findings = findings
    snapshot.entities.sort(key=lambda e: (e.severity, e.entity_id))
    return snapshot
