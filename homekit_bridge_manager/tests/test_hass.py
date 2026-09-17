"""Regression tests for the websocket client.

v1.0.0 gathered every registry command onto one socket, which aiohttp rejects
with "Concurrent call to receive() is not allowed". The add-on started fine and
then failed on every scan. These tests pin the sequencing.
"""

from __future__ import annotations

import asyncio

import pytest

from app.hass import HassWebSocket


class ReentrancyError(AssertionError):
    """Raised when two coroutines read the same socket at once."""


class FakeWebSocket:
    """Stands in for aiohttp's websocket, refusing overlapping reads.

    Mirrors the real contract: receive() is single-reader. Any overlap raises,
    exactly as aiohttp does.
    """

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self._pending: list[dict] = []
        self._reading = False
        self.max_concurrent_readers = 0

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)
        self._pending.append({"id": payload["id"], "success": True, "result": []})

    async def receive_json(self) -> dict:
        if self._reading:
            raise ReentrancyError("Concurrent call to receive() is not allowed")
        self._reading = True
        self.max_concurrent_readers = max(self.max_concurrent_readers, 1)
        try:
            await asyncio.sleep(0)  # give any rival coroutine a chance to interleave
            if not self._pending:
                raise AssertionError("receive_json() with nothing queued")
            return self._pending.pop(0)
        finally:
            self._reading = False


def test_fetch_all_never_reads_concurrently() -> None:
    ws = FakeWebSocket()
    client = HassWebSocket("http://supervisor/core", "token")

    entities, devices, areas, entries, states = asyncio.run(client._fetch_all(ws))

    assert (entities, devices, areas, entries, states) == ([], [], [], [], [])
    assert ws.max_concurrent_readers == 1


def test_fetch_all_issues_every_registry_command_in_order() -> None:
    ws = FakeWebSocket()
    client = HassWebSocket("http://supervisor/core", "token")

    asyncio.run(client._fetch_all(ws))

    assert [msg["type"] for msg in ws.sent] == [
        "config/entity_registry/list",
        "config/device_registry/list",
        "config/area_registry/list",
        "config_entries/get",
        "get_states",
    ]


def test_message_ids_are_unique() -> None:
    ws = FakeWebSocket()
    client = HassWebSocket("http://supervisor/core", "token")

    asyncio.run(client._fetch_all(ws))

    ids = [msg["id"] for msg in ws.sent]
    assert len(ids) == len(set(ids))


def test_command_surfaces_home_assistant_errors() -> None:
    class FailingWebSocket(FakeWebSocket):
        async def send_json(self, payload: dict) -> None:
            self.sent.append(payload)
            self._pending.append(
                {
                    "id": payload["id"],
                    "success": False,
                    "error": {"code": "unauthorized", "message": "nope"},
                }
            )

    ws = FailingWebSocket()
    client = HassWebSocket("http://supervisor/core", "token")

    with pytest.raises(RuntimeError, match="unauthorized"):
        asyncio.run(client._fetch_all(ws))
