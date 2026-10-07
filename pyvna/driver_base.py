"""Shared behaviour for all hardware drivers."""

from __future__ import annotations

import abc
import contextlib
import logging
import time
from collections.abc import Callable, Iterator

from .errors import DeviceError, ProtocolError
from .models import SweepConfig, VNAData
from .util.serial_port import SerialPortInterface

logger = logging.getLogger(__name__)

CHUNK_READ_TIMEOUT = 0.1
FLUSH_QUIET = 0.1
FLUSH_DEADLINE = 1.0


class BaseDriver(abc.ABC):
    """Base class for NanoVNA drivers: shared I/O discipline and state.

    Every read on the port is bounded by a finite timeout; a device that
    stops responding produces ProtocolError, never an indefinite hang.
    """

    def __init__(self, port: SerialPortInterface) -> None:
        self.port = port
        self.config: SweepConfig | None = None
        self.model: str | None = None
        # Injectable clock; tests substitute a controllable fake.
        self._clock: Callable[[], float] = time.monotonic

    @abc.abstractmethod
    def identify(self) -> str:
        """Probe the device and return its identification string."""

    @abc.abstractmethod
    def set_sweep(self, config: SweepConfig) -> None: ...

    @abc.abstractmethod
    def scan(self) -> VNAData: ...

    def close(self) -> None:
        self.port.close()

    def _require_config(self) -> SweepConfig:
        if self.config is None:
            raise DeviceError("sweep is not configured; call set_sweep() first")
        return self.config

    @contextlib.contextmanager
    def _read_timeout(self, seconds: float) -> Iterator[None]:
        previous = self.port.get_read_timeout()
        self.port.set_read_timeout(seconds)
        try:
            yield
        finally:
            self.port.set_read_timeout(previous)

    def _flush_input(self) -> None:
        """Discard pending input until the line stays quiet for FLUSH_QUIET."""
        end = self._clock() + FLUSH_DEADLINE
        with self._read_timeout(FLUSH_QUIET):
            while self._clock() < end:
                if not self.port.read(4096):
                    return

    def _read_exact(self, size: int, deadline_seconds: float) -> bytes:
        """Read exactly `size` bytes or raise ProtocolError on the deadline."""
        end = self._clock() + deadline_seconds
        buf = bytearray()
        with self._read_timeout(CHUNK_READ_TIMEOUT):
            while len(buf) < size:
                remaining = end - self._clock()
                if remaining <= 0:
                    raise ProtocolError(f"timed out reading {size} bytes (received {len(buf)})")
                self.port.set_read_timeout(min(remaining, CHUNK_READ_TIMEOUT))
                chunk = self.port.read(size - len(buf))
                if chunk:
                    buf.extend(chunk)
        return bytes(buf)

    def _accumulate_until(
        self, predicate: Callable[[bytes], bool], deadline_seconds: float
    ) -> bytes | None:
        """Read chunks until `predicate` accepts the accumulated buffer.

        Returns the accumulated buffer, or None when the deadline expires.
        """
        end = self._clock() + deadline_seconds
        buf = bytearray()
        with self._read_timeout(CHUNK_READ_TIMEOUT):
            while True:
                remaining = end - self._clock()
                if remaining <= 0:
                    return None
                self.port.set_read_timeout(min(remaining, CHUNK_READ_TIMEOUT))
                chunk = self.port.read(1024)
                if not chunk:
                    continue
                buf.extend(chunk)
                if predicate(bytes(buf)):
                    return bytes(buf)
