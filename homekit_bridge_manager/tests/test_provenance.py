"""Provenance must survive the trip from Config to the browser payload.

v1.0 shipped a UI that rendered synthetic dev-harness data identically to live
data. Entity ids that came from tools/dev_server.py were read as if they
described a real house. These tests pin the labelling end to end.
"""

from __future__ import annotations

from pathlib import Path

from app.config import Config, _domain_set
from app.model import Snapshot
from app.service import to_json


def _config(**kw) -> Config:
    base = dict(
        storage_dir=Path("/nonexistent"),
        hass_url="http://stub",
        supervisor_token="stub",
        refresh_seconds=300,
        port=8099,
        log_level="INFO",
    )
    base.update(kw)
    return Config(**base)


def test_live_is_the_default_everywhere() -> None:
    assert Snapshot().source == "live"
    assert Snapshot().is_synthetic is False
    assert _config().synthetic is False
    assert _config().assume_not_in_homekit == frozenset()


def test_snapshot_reports_synthetic() -> None:
    snap = Snapshot(source="synthetic")
    assert snap.is_synthetic is True


def test_to_json_carries_source_to_the_browser() -> None:
    live = to_json(Snapshot(generated_at="2026-09-17T21:00:00+00:00"), None)
    assert live["source"] == "live"

    fake = to_json(
        Snapshot(source="synthetic", generated_at="2026-09-17T21:00:00+00:00"), None
    )
    assert fake["source"] == "synthetic"


def test_to_json_reports_dismissed_integrations() -> None:
    snap = Snapshot(assume_not_in_homekit=frozenset({"hue", "lifx"}))
    payload = to_json(snap, None)
    assert payload["assume_not_in_homekit"] == ["hue", "lifx"]


def test_dev_server_declares_itself_synthetic() -> None:
    """The harness is the only thing allowed to set this."""
    source = Path("tools/dev_server.py").read_text()
    assert "synthetic=True" in source


def test_option_parsing_is_forgiving() -> None:
    assert _domain_set("hue,lifx") == frozenset({"hue", "lifx"})
    assert _domain_set("Hue, LIFX  nanoleaf") == frozenset({"hue", "lifx", "nanoleaf"})
    assert _domain_set("") == frozenset()
    assert _domain_set("   ") == frozenset()
