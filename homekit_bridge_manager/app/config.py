"""Runtime configuration, all from the add-on's environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    storage_dir: Path
    hass_url: str
    supervisor_token: str
    refresh_seconds: int
    port: int
    log_level: str
    # Integrations the user has told us are NOT paired in Home.app. Round-trip
    # detection for a HomeKit-capable brand is an inference, not a fact, so the
    # user gets to overrule it.
    assume_not_in_homekit: frozenset[str] = frozenset()
    # True only for the dev harness. Stamped onto the snapshot so the UI can
    # say out loud that it is showing invented data.
    synthetic: bool = False

    @classmethod
    def from_env(cls) -> "Config":
        config_dir = Path(os.environ.get("HA_CONFIG_DIR", "/homeassistant"))
        return cls(
            storage_dir=Path(os.environ.get("HA_STORAGE_DIR", config_dir / ".storage")),
            # The Supervisor proxies the core API for add-ons; no user token needed.
            hass_url=os.environ.get("HA_URL", "http://supervisor/core"),
            supervisor_token=os.environ.get("SUPERVISOR_TOKEN", ""),
            refresh_seconds=int(os.environ.get("REFRESH_SECONDS", "300")),
            port=int(os.environ.get("PORT", "8099")),
            log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
            assume_not_in_homekit=_domain_set(
                os.environ.get("ASSUME_NOT_IN_HOMEKIT", "")
            ),
        )


def _domain_set(raw: str) -> frozenset[str]:
    """Parse a comma- or space-separated integration list from the add-on options."""
    return frozenset(
        part.strip().lower() for part in raw.replace(",", " ").split() if part.strip()
    )
