# PyVNA

Python driver library for low-cost vector network analyzers of the NanoVNA
family. Supported protocol families:

- NanoVNA V1 — line-based text protocol
- NanoVNA V2 / LiteVNA — binary register protocol
- NanoVNA-X — ChibiOS shell protocol (USB CDC serial)

## Requirements

- Python 3.12+
- Optional: a VNA connected via USB-serial for hardware use
- VNA devices might use the [NanoVNA-X](https://github.com/momentics/NanoVNA-X) firmware for supported devices; it is preferable

## Install

```bash
pip install pyvna                # core library (drivers + calibration)
pip install "pyvna[server]"      # adds the example HTTP server
pip install "pyvna[test,dev]"    # adds the test and lint toolchain
```

Editable development checkout:

```bash
pip install -e ".[server,test,dev]"
```

## Quickstart

```python
from pyvna import SweepConfig, VNAPool

pool = VNAPool()
vna = pool.get("COM5")            # or "/dev/ttyACM0"
print(vna.model)                  # identification string from the probe
vna.set_sweep(SweepConfig(start=1e6, stop=900e6, points=101))
data = vna.get_data()
print(data.to_touchstone("s11"))  # standard Touchstone RI export
pool.close_all()
```

## Calibration (SOL)

```python
from pyvna import (
    CalibrationPlan,
    CalibrationStandard,
    CalibrationStep,
    SweepConfig,
)

plan = CalibrationPlan(
    name="s11-sol",
    sweep=SweepConfig(start=1e6, stop=900e6, points=101),
    steps=[
        CalibrationStep(CalibrationStandard.OPEN),
        CalibrationStep(CalibrationStandard.SHORT),
        CalibrationStep(CalibrationStandard.LOAD),
    ],
)
profile = vna.acquire_calibration(plan, prompt=lambda std: print("connect:", std))
data = vna.get_data()             # S11 de-embedded with the three-term model
```

The profile is installed only after all standards are measured and the error
terms compute and validate; a cancelled run raises `CalibrationCancelledError`
and installs nothing. S21 passes through as measured — see the API docstrings.

## HTTP server

```bash
python -m pyvna.server.main [--host 127.0.0.1] [--port 8080]
```

- `GET /api/v1/scan?port=COM5&start=1e6&stop=900e6&points=101&trace=s11` -> Touchstone
- `GET /metrics` -> Prometheus text format

The server binds to loopback by default; put it behind an authenticated
reverse proxy for network use.

## Project layout

- `pyvna/__init__.py` — public API exports and package version
- `pyvna/errors.py` — exception hierarchy rooted at `PyVNAError`
- `pyvna/models.py` — `SweepConfig`, `VNAData`; Touchstone RI export, VSWR
- `pyvna/driver_base.py` — shared driver behaviour: bounded I/O discipline
- `pyvna/driver.py` — `Driver` protocol, probe factory, `VNAPool`
- `pyvna/driver_v1.py` — NanoVNA V1 line-based text protocol
- `pyvna/driver_v2.py` — NanoVNA V2 / LiteVNA binary register protocol
- `pyvna/driver_x.py` — NanoVNA-X ChibiOS shell protocol (USB CDC serial)
- `pyvna/calibration.py` — SOL calibration: standards, plans, profiles, error terms
- `pyvna/vna.py` — high-level VNA facade: sweep, scan, calibration
- `pyvna/util/serial_port.py` — serial port abstraction and path validation
- `pyvna/server/main.py` — example FastAPI server: Touchstone scans + Prometheus metrics
- `tests/` — unit test suite with mock ports and fake devices

## License

MIT (see LICENSE)
