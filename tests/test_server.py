"""Tests for the example HTTP server."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from prometheus_client import CONTENT_TYPE_LATEST

import pyvna.server.main as server_main
from pyvna.models import VNAData
from pyvna.vna import VNA
from tests.test_vna import StubDriver


def _sweep_data() -> VNAData:
    # Canonical samples: S11 = 0.5 - 0.5j, S21 = 0.1 - 0.1j on three points.
    return VNAData(
        frequencies=[1e6, 2e6, 3e6],
        s11=[complex(0.5, -0.5)] * 3,
        s21=[complex(0.1, -0.1)] * 3,
    )


class FakePool:
    """Stand-in for VNAPool returning a ready VNA or a canned failure."""

    def __init__(self, vna: VNA | None = None, error: Exception | None = None) -> None:
        self._vna = vna
        self._error = error
        self.close_all_calls = 0

    def get(self, port_path: str) -> VNA:
        if self._error is not None:
            raise self._error
        assert self._vna is not None
        return self._vna

    def release(self, port_path: str) -> bool:
        return self._vna is not None

    def close_all(self) -> None:
        self.close_all_calls += 1


@pytest.fixture()
def install_pool(monkeypatch: pytest.MonkeyPatch):
    def _install(pool: FakePool) -> None:
        monkeypatch.setattr(server_main, "pool", pool)

    return _install


@pytest.fixture()
def client(install_pool):
    pool = FakePool(vna=VNA(StubDriver([_sweep_data(), _sweep_data()])))
    install_pool(pool)
    with TestClient(server_main.app) as test_client:
        yield test_client


def test_scan_returns_touchstone(client) -> None:
    response = client.get(
        "/api/v1/scan",
        params={"port": "COM9", "start": 1e6, "stop": 3e6, "points": 3, "trace": "s21"},
    )
    assert response.status_code == 200
    body = response.text
    assert body.startswith("! PyVNA data export")
    assert "# Hz S RI R 50" in body
    # The trace selects the S21 column of the standard RI file.
    data_lines = [line for line in body.splitlines() if line and not line.startswith(("!", "#"))]
    assert data_lines == [
        "1000000.000000 0.100000 -0.100000",
        "2000000.000000 0.100000 -0.100000",
        "3000000.000000 0.100000 -0.100000",
    ]


def test_scan_missing_port(client) -> None:
    response = client.get(
        "/api/v1/scan",
        params={"start": 1e6, "stop": 3e6, "points": 3},
    )
    assert response.status_code == 422


@pytest.mark.parametrize("points", [0, 500])
def test_scan_invalid_points(client, points: int) -> None:
    response = client.get(
        "/api/v1/scan",
        params={"port": "COM9", "start": 1e6, "stop": 3e6, "points": points},
    )
    assert response.status_code == 422


def test_scan_invalid_start(client) -> None:
    # 1000 Hz is below the family minimum of 50 kHz.
    response = client.get(
        "/api/v1/scan",
        params={"port": "COM9", "start": 1000, "stop": 3e6, "points": 3},
    )
    assert response.status_code == 422


def test_scan_device_error(install_pool) -> None:
    install_pool(FakePool(error=RuntimeError("port not found")))
    with TestClient(server_main.app) as client:
        response = client.get(
            "/api/v1/scan",
            params={"port": "COM9", "start": 1e6, "stop": 3e6, "points": 3},
        )
    assert response.status_code == 500
    assert "device error" in response.json()["detail"]


def test_metrics_content_type(client) -> None:
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(CONTENT_TYPE_LATEST)
