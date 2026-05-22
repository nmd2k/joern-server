"""FastAPI test harness helpers."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from joern_server.app import create_app
from joern_server.state import AppState


def make_test_state(tmp_path: Path, **overrides: object) -> AppState:
    return AppState.for_test(tmp_path, **overrides)


def create_test_app(state: AppState | None = None, *, tmp_path: Path | None = None):
    if state is None:
        if tmp_path is None:
            raise ValueError("create_test_app requires state or tmp_path")
        state = make_test_state(tmp_path)
    return create_app(state=state)


def make_test_client(
    state: AppState | None = None,
    *,
    tmp_path: Path | None = None,
) -> TestClient:
    app = create_test_app(state=state, tmp_path=tmp_path)
    return TestClient(app)
