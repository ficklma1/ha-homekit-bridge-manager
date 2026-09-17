"""Orchestration: gather, join, detect, cache.

v1 is strictly read-only. Nothing here writes to Home Assistant or to disk
outside the add-on's own data directory.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from datetime import datetime, timezone

from . import detectors, model
from .config import Config
from .hass import HassWebSocket
from .storage import read_aid_files, read_config_entries

_LOGGER = logging.getLogger(__name__)


class AuditService:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._snapshot: model.Snapshot | None = None
        self._error: str | None = None
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None

    @property
    def snapshot(self) -> model.Snapshot | None:
        return self._snapshot

    @property
    def error(self) -> str | None:
        return self._error

    async def refresh(self) -> model.Snapshot:
        """Rebuild the snapshot. Serialised so concurrent callers share one pass."""
        async with self._lock:
            config = self._config
            _LOGGER.info("Refreshing audit from %s", config.storage_dir)

            # Blocking file reads go to a thread so the event loop stays free.
            entries, aid_files = await asyncio.gather(
                asyncio.to_thread(read_config_entries, config.storage_dir),
                asyncio.to_thread(read_aid_files, config.storage_dir),
            )

            client = HassWebSocket(config.hass_url, config.supervisor_token)
            registries = await client.fetch_registries()

            snapshot = detectors.run(model.build(entries, aid_files, registries))
            snapshot.generated_at = datetime.now(timezone.utc).isoformat(
                timespec="seconds"
            )

            self._snapshot = snapshot
            self._error = None
            _LOGGER.info(
                "Audit complete: %d bridges, %d published entities, %d findings",
                len(snapshot.bridges),
                len(snapshot.entities),
                len(snapshot.findings),
            )
            return snapshot

    async def refresh_safely(self) -> None:
        try:
            await self.refresh()
        except Exception as err:  # noqa: BLE001 — surfaced in the UI, not swallowed
            self._error = f"{type(err).__name__}: {err}"
            _LOGGER.exception("Audit refresh failed")

    async def start(self) -> None:
        await self.refresh_safely()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._config.refresh_seconds)
            await self.refresh_safely()


def to_json(snapshot: model.Snapshot | None, error: str | None) -> dict:
    """Shape the snapshot for the browser."""
    if snapshot is None:
        return {"ok": False, "error": error or "No audit has run yet."}

    def entity_payload(ent: model.EntityView) -> dict:
        return {
            "entity_id": ent.entity_id,
            "name": ent.name,
            "domain": ent.domain,
            "area": ent.area,
            "state": ent.state,
            "platform": ent.platform,
            "source_domain": ent.source_domain,
            "source_title": ent.source_title,
            "entity_category": ent.entity_category,
            "flags": ent.flags,
            "notes": ent.notes,
            "publications": [
                {
                    "bridge": p.bridge_title,
                    "entry_id": p.entry_id,
                    "aid": p.aid,
                    "stale": p.is_stale,
                    "via_filter": p.via_filter,
                }
                for p in ent.publications
            ],
        }

    return {
        "ok": True,
        "generated_at": snapshot.generated_at,
        "warnings": snapshot.warnings,
        "bridges": [
            {
                **{
                    k: v
                    for k, v in dataclasses.asdict(b).items()
                    if k not in {"predicted", "allocated"}
                },
                "accessory_count": b.accessory_count,
                "count_is_exact": b.count_is_exact,
                "utilisation": b.utilisation,
            }
            for b in snapshot.bridges
        ],
        "findings": [dataclasses.asdict(f) for f in snapshot.findings],
        "entities": [entity_payload(e) for e in snapshot.entities],
        "totals": {
            "bridges": sum(1 for b in snapshot.bridges if b.mode != "accessory"),
            "accessories_total": sum(b.accessory_count for b in snapshot.bridges),
            "entries": len(snapshot.bridges),
            "critical": sum(1 for f in snapshot.findings if f.severity == "critical"),
            "warning": sum(1 for f in snapshot.findings if f.severity == "warning"),
            "info": sum(1 for f in snapshot.findings if f.severity == "info"),
        },
    }
