"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI

from joern_server.api.routers.cleanup import router as cleanup_router
from joern_server.api.routers.graph import router as graph_router
from joern_server.api.routers.health import router as health_router
from joern_server.api.routers.parse import router as parse_router
from joern_server.api.routers.parse_repo import router as parse_repo_router
from joern_server.api.routers.query import router as query_router
from joern_server.config import Settings
from joern_server.state import AppState
from joern_server.upstream import joern as upstream

logger = logging.getLogger(__name__)

_probe_interval_sec = 10


async def _background_probe_loop(state: AppState) -> None:
    """Periodically probe Joern and update Prometheus gauges."""
    while True:
        try:
            http_ok = upstream.check_joern_tcp(
                state.settings.internal_host,
                state.settings.internal_port,
                timeout_sec=2.0,
            )
            if state.metrics is not None:
                state.metrics.set_gauge("joern_proxy_joern_up", 1.0 if http_ok else 0.0)
        except Exception:
            logger.debug("background probe failed", exc_info=True)
        await asyncio.sleep(_probe_interval_sec)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    if not hasattr(app.state, "app_state") or app.state.app_state is None:
        app.state.app_state = AppState.from_settings(Settings.from_env())
    state: AppState = app.state.app_state

    probe_task: asyncio.Task | None = None
    if state.metrics is not None:
        probe_task = asyncio.create_task(_background_probe_loop(state))

    yield

    if probe_task is not None:
        probe_task.cancel()
        try:
            await probe_task
        except asyncio.CancelledError:
            pass


def create_app(state: Optional[AppState] = None) -> FastAPI:
    app = FastAPI(title="Joern Server", lifespan=_lifespan)
    if state is not None:
        app.state.app_state = state
    app.include_router(health_router)
    app.include_router(query_router)
    app.include_router(parse_router)
    app.include_router(parse_repo_router)
    app.include_router(graph_router)
    app.include_router(cleanup_router)
    return app


app = create_app()
