"""Example HTTP server mirroring the Go reference implementation."""
from __future__ import annotations

import time

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, Histogram, generate_latest

from ..driver import VNAPool
from ..models import SweepConfig

app = FastAPI(title="PyVNA Server", version="1.0.0")
scan_duration = Histogram(
    "pyvna_scan_duration_seconds",
    "Duration of VNA scan operations",
    labelnames=("port",),
)
pool = VNAPool()


@app.on_event("shutdown")
def _shutdown() -> None:  # pragma: no cover - network integration
    pool.close_all()


@app.get("/api/v1/scan", response_class=PlainTextResponse)
def scan(port: str | None = None) -> str:
    if not port:
        raise HTTPException(status_code=400, detail="query parameter 'port' is required")
    try:
        vna = pool.get(port)
    except Exception as exc:  # pragma: no cover - hardware dependent
        raise HTTPException(status_code=500, detail=f"device error: {exc}") from exc

    sweep = SweepConfig(start=1e6, stop=900e6, points=101)
    try:
        vna.set_sweep(sweep)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to configure sweep: {exc}") from exc

    start = time.perf_counter()
    try:
        data = vna.get_data()
    except Exception as exc:  # pragma: no cover - hardware dependent
        raise HTTPException(status_code=500, detail=f"scan failed: {exc}") from exc
    duration = time.perf_counter() - start
    scan_duration.labels(port=port).observe(duration)
    return data.to_touchstone()


@app.get("/metrics")
def metrics() -> Response:
    payload = generate_latest()  # pragma: no cover - simple passthrough
    return Response(content=payload, media_type=CONTENT_TYPE_LATEST)


def main() -> None:  # pragma: no cover - requires uvicorn
    import uvicorn

    uvicorn.run("pyvna.server.main:app", host="0.0.0.0", port=8080, reload=False)


if __name__ == "__main__":  # pragma: no cover - script entry point
    main()
