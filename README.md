# PyVNA

PyVNA is a Python 3.12+ port of the GoVNA project for interacting with low-cost
vector network analyzers such as the NanoVNA family.  The code mirrors the
architecture of the original Go implementation while adopting idiomatic Python
constructs and dependencies.

The package exposes a driver abstraction for different hardware revisions,
calibration primitives, utilities for working with VNA data and a small HTTP
service that demonstrates how to perform scans via a REST API while exporting
Prometheus metrics.

## Requirements

* Python 3.12 or newer
* Optional: hardware access to a supported VNA device connected via a serial
  port

Install the project in editable mode together with the HTTP server and test
dependencies:

```bash
pip install -e ".[test]"
```

Run the example server:

```bash
python -m pyvna.server.main
```

Execute the unit test suite:

```bash
pytest
```

## Project layout

* `pyvna/driver.py` – driver factory and VNA pool management
* `pyvna/driver_v1.py` – implementation for text-based NanoVNA V1 protocol
* `pyvna/driver_v2.py` – implementation for the binary NanoVNA V2/LiteVNA
  protocol
* `pyvna/calibration.py` – calibration plans, profiles and error-term handling
* `pyvna/vna.py` – high level VNA façade and data utilities
* `pyvna/util/serial_port.py` – serial port abstraction that allows mocking in
  tests
* `pyvna/server/main.py` – HTTP example mirroring the Go reference server
* `tests/test_vna.py` – unit tests ported from the Go suite
