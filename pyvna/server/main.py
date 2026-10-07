"""Example HTTP server exposing VNA scans as Touchstone data.

The server binds to the loopback interface by default; expose it behind an
authenticated reverse proxy when used over a network.
"""

from __future__ import annotations

import argparse
import logging
import time
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Histogram, generate_latest

from .. import __version__
from ..driver import VNAPool
from ..models import SweepConfig

logger = logging.getLogger(__name__)

pool = VNAPool()
scan_duration = Histogram(
    "pyvna_scan_duration_seconds",
    "Duration of VNA scan operations",
    labelnames=("port",),
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    pool.close_all()


app = FastAPI(title="PyVNA Server", version=__version__, lifespan=lifespan)


@app.get("/api/v1/scan", response_class=PlainTextResponse)
def scan(
    port: str = Query(description="Serial port of the VNA, e.g. COM5 or /dev/ttyACM0"),
    start: float = Query(default=1e6, ge=SweepConfig.MIN_START_HZ, le=SweepConfig.MAX_STOP_HZ),
    stop: float = Query(default=900e6, ge=SweepConfig.MIN_START_HZ, le=SweepConfig.MAX_STOP_HZ),
    points: int = Query(default=101, ge=1, le=SweepConfig.MAX_POINTS),
    trace: Literal["s11", "s21"] = Query("s11"),
) -> str:
    try:
        vna = pool.get(port)
    except Exception as exc:
        logger.warning("device error on %s: %s", port, exc)
        raise HTTPException(status_code=500, detail=f"device error: {exc}") from exc

    try:
        sweep = SweepConfig(start=start, stop=stop, points=points)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        vna.set_sweep(sweep)
        started = time.perf_counter()
        data = vna.get_data()
        scan_duration.labels(port=port).observe(time.perf_counter() - started)
    except Exception as exc:
        logger.warning("scan failed on %s: %s", port, exc)
        raise HTTPException(status_code=500, detail=f"scan failed: {exc}") from exc
    return data.to_touchstone(trace)


@app.get("/metrics")
def metrics() -> Response:
    payload = generate_latest()
    return Response(content=payload, media_type=CONTENT_TYPE_LATEST)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the PyVNA example server.")
    parser.add_argument(
        "--host", default="127.0.0.1", help="Interface to bind (loopback by default)"
    )
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    import uvicorn

    uvicorn.run("pyvna.server.main:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
