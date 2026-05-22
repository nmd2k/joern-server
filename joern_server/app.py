"""FastAPI application factory."""

from __future__ import annotations

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


@asynccontextmanager
async def _lifespan(app: FastAPI):
    if not hasattr(app.state, "app_state") or app.state.app_state is None:
        app.state.app_state = AppState.from_settings(Settings.from_env())
    yield


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
