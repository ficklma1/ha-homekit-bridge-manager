"""Minimal Home Assistant websocket client.

Pulls the entity / device / area registries and the current state machine. Uses
the Supervisor proxy and ``SUPERVISOR_TOKEN``, so no long-lived access token ever
goes into the add-on's config.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from .registry import ConfigEntryRow, DeviceRow, EntityRow, Registries

_LOGGER = logging.getLogger(__name__)


class HassWebSocket:
    """One-shot websocket session: connect, authenticate, fetch, disconnect."""

    def __init__(self, base_url: str, token: str) -> None:
        self._url = base_url.rstrip("/") + "/api/websocket"
        self._token = token
        self._msg_id = 0

    async def fetch_registries(self, timeout: float = 30.0) -> Registries:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout)
        ) as session:
            async with session.ws_connect(self._url, heartbeat=15) as ws:
                await self._authenticate(ws)
                entities, devices, areas, entries, states = await asyncio.gather(
                    self._command(ws, "config/entity_registry/list"),
                    self._command(ws, "config/device_registry/list"),
                    self._command(ws, "config/area_registry/list"),
                    self._command(ws, "config_entries/get"),
                    self._command(ws, "get_states"),
                )
        return _build_registries(entities, devices, areas, entries, states)

    async def _authenticate(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        hello = await ws.receive_json()
        if hello.get("type") != "auth_required":
            raise RuntimeError(f"Unexpected greeting from Home Assistant: {hello}")
        await ws.send_json({"type": "auth", "access_token": self._token})
        reply = await ws.receive_json()
        if reply.get("type") != "auth_ok":
            raise RuntimeError(f"Home Assistant rejected the Supervisor token: {reply}")

    async def _command(
        self, ws: aiohttp.ClientWebSocketResponse, command: str, **payload: Any
    ) -> list[dict]:
        self._msg_id += 1
        msg_id = self._msg_id
        await ws.send_json({"id": msg_id, "type": command, **payload})
        # Replies can interleave; read until ours comes back.
        while True:
            message = await ws.receive_json()
            if message.get("id") != msg_id:
                continue
            if not message.get("success", True):
                raise RuntimeError(f"{command} failed: {message.get('error')}")
            return message.get("result") or []


def _build_registries(
    entities: list[dict],
    devices: list[dict],
    areas: list[dict],
    entries: list[dict],
    states: list[dict],
) -> Registries:
    reg = Registries()

    for raw in entities:
        entity_id = raw["entity_id"]
        reg.entities[entity_id] = EntityRow(
            entity_id=entity_id,
            platform=raw.get("platform") or "",
            unique_id=raw.get("unique_id"),
            hidden=bool(raw.get("hidden_by")),
            entity_category=raw.get("entity_category"),
            device_id=raw.get("device_id"),
            area_id=raw.get("area_id"),
            name=raw.get("name") or raw.get("original_name"),
            disabled=bool(raw.get("disabled_by")),
        )

    for raw in devices:
        reg.devices[raw["id"]] = DeviceRow(
            device_id=raw["id"],
            name=raw.get("name_by_user") or raw.get("name"),
            manufacturer=raw.get("manufacturer"),
            model=raw.get("model"),
            area_id=raw.get("area_id"),
            config_entries=list(raw.get("config_entries") or ()),
            via_device_id=raw.get("via_device_id"),
        )

    for raw in areas:
        reg.areas[raw["area_id"]] = raw.get("name") or raw["area_id"]

    for raw in entries:
        reg.config_entries[raw["entry_id"]] = ConfigEntryRow(
            entry_id=raw["entry_id"],
            domain=raw.get("domain") or "",
            title=raw.get("title") or "",
        )

    for raw in states:
        reg.states[raw["entity_id"]] = raw.get("state") or "unknown"

    # The state machine can hold entities the registry never saw (YAML
    # template entities, for one). HomeKit publishes those too, so synthesise
    # a bare row rather than losing them.
    for entity_id in reg.states:
        if entity_id not in reg.entities:
            reg.entities[entity_id] = EntityRow(entity_id=entity_id)

    _LOGGER.info(
        "Registries: %d entities, %d devices, %d areas, %d config entries",
        len(reg.entities),
        len(reg.devices),
        len(reg.areas),
        len(reg.config_entries),
    )
    return reg
