"""Driver implementation for the NanoVNA-X shell protocol."""

from __future__ import annotations

import logging
import struct

from .driver_base import CHUNK_READ_TIMEOUT, BaseDriver
from .errors import IdentificationError, ProtocolError
from .models import SweepConfig, VNAData

logger = logging.getLogger(__name__)

X_BANNER_DEADLINE = 2.0
X_VERSION_FALLBACK_DEADLINE = 1.5
X_COMMAND_TIMEOUT = 5.0
X_SCAN_BASE = 10.0
X_SCAN_PER_POINT = 0.02
X_PROMPT_RESYNC_TIMEOUT = 1.0

BINARY_SCAN_MASK = 0x87  # binary | frequency | S11 | S21
RECORD_SIZE = 20  # uint32 freq + float32[2] s11 + float32[2] s21


class XDriver(BaseDriver):
    """Driver implementation for NanoVNA-X shell protocol."""

    PROMPT = b"ch> "

    def identify(self) -> str:
        # The shell prints a prompt as soon as the session is active; builds
        # with an SD card prefix it with a one-line banner carrying the
        # device name. Detection keys off that first prompt.
        buf = self._accumulate_until(lambda b: XDriver.PROMPT in b, X_BANNER_DEADLINE)
        if buf is not None and b"nanovna" in buf.lower():
            self.model = self._model_from_banner(buf)
            return self.model
        # No named banner (or no shell at all): probe with the version
        # query. The reply is a bare semver, so the prompt plus our own
        # echoed command is what identifies the shell.
        self._flush_input()
        self.port.write(b"version\r\n")
        buf = self._accumulate_until(self._looks_like_x_version_reply, X_VERSION_FALLBACK_DEADLINE)
        if buf is None:
            raise IdentificationError(
                "x: no shell prompt received on connection or after the version query"
            )
        version_line = self._version_line_from_reply(buf)
        self.model = f"NanoVNA-X {version_line}" if version_line else "NanoVNA-X"
        return self.model

    @staticmethod
    def _looks_like_x_version_reply(buf: bytes) -> bool:
        return b"version" in buf and XDriver.PROMPT in buf

    @staticmethod
    def _model_from_banner(buf: bytes) -> str:
        for line in buf.splitlines():
            if b"nanovna" in line.lower():
                return line.decode("utf-8", "replace").strip()
        return "NanoVNA-X Shell"

    @staticmethod
    def _version_line_from_reply(buf: bytes) -> str:
        for line in buf.splitlines():
            text = line.decode("utf-8", "replace").strip()
            if not text or text == "version" or XDriver.PROMPT in line:
                continue
            return text
        return ""

    def set_sweep(self, config: SweepConfig) -> None:
        """Configure the continuous sweep on the device.

        The sweep command is deferred to the measurement thread; the prompt
        that follows the echo does not mean the engine applied the settings
        yet. A later scan command runs after it (FIFO order on the device).
        """
        self.config = config
        self._flush_input()
        cmd = f"sweep {int(config.start)} {int(config.stop)} {config.points}\r\n".encode()
        self.port.write(cmd)
        payload = self._read_until_prompt(X_COMMAND_TIMEOUT)
        if payload.strip():
            logger.debug("x: sweep command output: %r", payload[:120])

    def scan(self) -> VNAData:
        """Run a one-shot sweep and return the binary payload (mask 0x87)."""
        config = self._require_config()
        end = self._clock() + X_SCAN_BASE + X_SCAN_PER_POINT * config.points
        self._flush_input()
        cmd = (
            f"scan {int(config.start)} {int(config.stop)} {config.points} "
            f"{BINARY_SCAN_MASK:#04x}\r\n"
        ).encode()
        self.port.write(cmd)
        self._consume_echo(end - self._clock())
        # The deferred command's early prompt, when present, precedes the
        # payload by exactly four bytes; detect and discard it.
        first = self._read_exact(4, end - self._clock())
        header = first if first != XDriver.PROMPT else self._read_exact(4, end - self._clock())
        mask, count = struct.unpack("<HH", header)
        if mask != BINARY_SCAN_MASK:
            raise ProtocolError(
                f"x: device returned mask {mask:#04x}, expected {BINARY_SCAN_MASK:#04x}"
            )
        if count != config.points:
            raise ProtocolError(f"x: device returned {count} points, expected {config.points}")
        raw = self._read_exact(count * RECORD_SIZE, end - self._clock())
        data = VNAData(frequencies=[0.0] * count, s11=[0j] * count, s21=[0j] * count)
        for i in range(count):
            offset = i * RECORD_SIZE
            (freq,) = struct.unpack_from("<I", raw, offset)
            s11_re, s11_im, s21_re, s21_im = struct.unpack_from("<4f", raw, offset + 4)
            data.frequencies[i] = float(freq)
            data.s11[i] = complex(s11_re, s11_im)
            data.s21[i] = complex(s21_re, s21_im)
        try:
            self._read_until_prompt(X_PROMPT_RESYNC_TIMEOUT)  # trailing prompt resync
        except ProtocolError:
            logger.debug("x: no trailing prompt after the binary payload; continuing")
        return data

    def _consume_echo(self, deadline_seconds: float) -> None:
        """Discard the echoed command line; the shell echoes typed input.

        Bytes are consumed one at a time up to the newline so that any payload
        arriving in the same burst stays intact for the header read. The echo
        is bounded by the shell's 64-character input limit.
        """
        end = self._clock() + deadline_seconds
        with self._read_timeout(CHUNK_READ_TIMEOUT):
            while True:
                if self._clock() >= end:
                    raise ProtocolError("x: timed out reading the command echo")
                if self.port.read(1) == b"\n":
                    return

    def _read_until_prompt(self, deadline_seconds: float) -> str:
        """Read until the shell prompt; return the payload before it."""
        buf = self._accumulate_until(lambda b: XDriver.PROMPT in b, deadline_seconds)
        if buf is None:
            raise ProtocolError("x: timed out waiting for the shell prompt")
        idx = buf.rfind(XDriver.PROMPT)
        return buf[:idx].decode("utf-8", "replace")


__all__ = ["XDriver"]
