"""aiohttp server, mounted behind Home Assistant ingress.

Ingress serves the add-on under a generated path prefix and passes it in the
``X-Ingress-Path`` header. The page uses relative URLs throughout so it works
both behind ingress and on the direct port.
"""

from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web

from .config import Config
from .service import AuditService, to_json

_LOGGER = logging.getLogger(__name__)
WEB_DIR = Path(__file__).parent / "web"


async def handle_index(request: web.Request) -> web.Response:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    return web.Response(text=html, content_type="text/html")


async def handle_snapshot(request: web.Request) -> web.Response:
    service: AuditService = request.app["service"]
    return web.json_response(to_json(service.snapshot, service.error))


async def handle_refresh(request: web.Request) -> web.Response:
    service: AuditService = request.app["service"]
    await service.refresh_safely()
    return web.json_response(to_json(service.snapshot, service.error))


async def handle_health(request: web.Request) -> web.Response:
    service: AuditService = request.app["service"]
    healthy = service.snapshot is not None
    return web.json_response(
        {"ok": healthy, "error": service.error},
        status=200 if healthy else 503,
    )


def create_app(config: Config) -> web.Application:
    app = web.Application()
    service = AuditService(config)
    app["service"] = service

    app.add_routes(
        [
            web.get("/", handle_index),
            web.get("/api/snapshot", handle_snapshot),
            web.post("/api/refresh", handle_refresh),
            web.get("/health", handle_health),
            web.static("/static", WEB_DIR / "static"),
        ]
    )

    async def on_startup(_: web.Application) -> None:
        await service.start()

    async def on_cleanup(_: web.Application) -> None:
        await service.stop()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def main() -> None:
    config = Config.from_env()
    logging.basicConfig(
        level=config.log_level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    _LOGGER.info(
        "HomeKit Bridge Manager starting — storage=%s refresh=%ss port=%s",
        config.storage_dir,
        config.refresh_seconds,
        config.port,
    )
    web.run_app(create_app(config), port=config.port, print=None)


if __name__ == "__main__":
    main()
