"""Driver implementation for the NanoVNA-X shell protocol."""

from __future__ import annotations

import logging

from .driver_base import BaseDriver
from .errors import IdentificationError, ProtocolError
from .models import SweepConfig, VNAData

logger = logging.getLogger(__name__)

X_BANNER_DEADLINE = 2.0
X_VERSION_FALLBACK_DEADLINE = 1.5
X_COMMAND_TIMEOUT = 5.0
X_SCAN_BASE = 10.0
X_SCAN_PER_POINT = 0.02
X_PROMPT_RESYNC_TIMEOUT = 1.0

TEXT_SCAN_MASK = 0x07  # frequency + S11 + S21, textual output


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
        """Run a one-shot sweep and return the text payload (mask 0x07)."""
        config = self._require_config()
        end = self._clock() + X_SCAN_BASE + X_SCAN_PER_POINT * config.points
        self._flush_input()
        cmd = (
            f"scan {int(config.start)} {int(config.stop)} {config.points} {TEXT_SCAN_MASK:#04x}\r\n"
        ).encode()
        self.port.write(cmd)
        raw = bytearray()
        while True:
            chunk = self._accumulate_until(lambda b: XDriver.PROMPT in b, end - self._clock())
            if chunk is None:
                raise ProtocolError(f"x: timed out reading scan data (received {len(raw)} bytes)")
            raw.extend(chunk)
            text = bytes(raw).replace(XDriver.PROMPT, b"\n").decode("utf-8", "replace")
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            data = self._parse_text_data(lines, config.points)
            if data is not None:
                return data

    def _read_until_prompt(self, deadline_seconds: float) -> str:
        """Read until the shell prompt; return the payload before it."""
        buf = self._accumulate_until(lambda b: XDriver.PROMPT in b, deadline_seconds)
        if buf is None:
            raise ProtocolError("x: timed out waiting for the shell prompt")
        idx = buf.rfind(XDriver.PROMPT)
        return buf[:idx].decode("utf-8", "replace")

    def _parse_text_data(self, lines: list[str], expected_points: int) -> VNAData | None:
        """Parse scan text lines; None when a complete point set is absent."""
        data = VNAData(frequencies=[], s11=[], s21=[])
        for line_no, line in enumerate(lines, start=1):
            if line.startswith("scan ") or line.startswith("ch>"):
                continue  # skip the command echo and stray prompt fragments
            parts = line.split()
            if len(parts) != 5:
                raise ProtocolError(f"x: data line {line_no} has {len(parts)} values, expected 5")
            try:
                freq, s11_re, s11_im, s21_re, s21_im = (float(p) for p in parts)
            except ValueError as exc:
                raise ProtocolError(f"x: data line {line_no} is not numeric") from exc
            data.frequencies.append(freq)
            data.s11.append(complex(s11_re, s11_im))
            data.s21.append(complex(s21_re, s21_im))
        if len(data.frequencies) > expected_points:
            raise ProtocolError(
                f"x: device returned {len(data.frequencies)} points, expected {expected_points}"
            )
        if len(data.frequencies) < expected_points:
            return None
        return data


__all__ = ["XDriver"]
