"""Unit tests for POST /parse."""

from http import HTTPStatus
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from tests.helpers.app import create_test_app, make_test_state


def test_parse_happy_path_mocks_subprocess(tmp_path) -> None:
    state = make_test_state(tmp_path, parse_bin="/bin/echo")
    client = TestClient(create_test_app(state=state))
    sample_id = "my-sample"
    cpg_path = tmp_path / "cpg-out" / sample_id

    def fake_run(*_args, **_kwargs):
        cpg_path.mkdir(parents=True, exist_ok=True)
        return MagicMock(returncode=0, stdout="ok", stderr="")

    payload = {
        "sample_id": sample_id,
        "source_code": "int main(){}",
        "language": "c",
        "overwrite": True,
    }
    with patch("joern_server.parse.runner.subprocess.run", side_effect=fake_run):
        response = client.post("/parse", json=payload)

    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["ok"] is True
    assert body["sample_id"] == sample_id
    assert body["cache_hit"] is False
    assert "source_hash" in body


def test_parse_missing_sample_id(tmp_path) -> None:
    client = TestClient(create_test_app(state=make_test_state(tmp_path)))
    response = client.post("/parse", json={"source_code": "x"})
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert "sample_id" in response.json()["error"]


def test_parse_missing_source_code(tmp_path) -> None:
    client = TestClient(create_test_app(state=make_test_state(tmp_path)))
    response = client.post("/parse", json={"sample_id": "s1"})
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert "source_code" in response.json()["error"]


def test_parse_records_metrics(tmp_path) -> None:
    state = make_test_state(tmp_path, parse_bin="/bin/echo")
    client = TestClient(create_test_app(state=state))
    sample_id = "metrics-sample"
    cpg_path = tmp_path / "cpg-out" / sample_id

    def fake_run(*_args, **_kwargs):
        cpg_path.mkdir(parents=True, exist_ok=True)
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("joern_server.parse.runner.subprocess.run", side_effect=fake_run):
        client.post(
            "/parse",
            json={"sample_id": sample_id, "source_code": "int x;", "overwrite": True},
        )

    metrics = client.get("/metrics")
    text = metrics.text
    assert "joern_proxy_parse_requests_total" in text
    assert "joern_proxy_parse_duration_seconds_sum" in text


def test_parse_idempotent_cache_hit(tmp_path) -> None:
    state = make_test_state(tmp_path)
    sample_id = "cached"
    cpg_path = tmp_path / "cpg-out" / sample_id
    cpg_path.mkdir(parents=True)
    source = "int main(){}"
    import hashlib

    source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    with state.sid_hash_lock:
        state.sid_to_hash[sample_id] = source_hash

    client = TestClient(create_test_app(state=state))
    with patch("joern_server.parse.runner.subprocess.run") as mock_run:
        response = client.post(
            "/parse",
            json={"sample_id": sample_id, "source_code": source},
        )
        mock_run.assert_not_called()

    assert response.status_code == HTTPStatus.OK
    assert response.json()["cache_hit"] is True
