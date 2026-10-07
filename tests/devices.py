"""Fake VNA devices that emulate wire behaviour for the driver tests."""

from __future__ import annotations

import struct


class FakeV1Device:
    """Emulates the NanoVNA V1 text protocol."""

    def __init__(self) -> None:
        self.data_payload = b""  # lines pushed on "data"

    def attach(self, port) -> None:  # called after MockSerialPort is created
        pass  # device is silent at connect

    def on_write(self, data: bytes, port) -> None:
        if data == b"version\n":
            port.set_read_data(b"NanoVNA H\r\n")
        elif data.startswith(b"sweep "):
            pass  # no response
        elif data == b"data\n":
            port.set_read_data(self.data_payload)


class FakeV2Device:
    """Emulates the NanoVNA V2/LiteVNA register protocol."""

    def __init__(self, variant: int = 2) -> None:
        self.variant = variant
        self.fifo_payload = b""  # bytes pushed on READFIFO

    def attach(self, port) -> None:
        pass

    def on_write(self, data: bytes, port) -> None:
        if data == bytes(8):
            return  # protocol reset
        if len(data) >= 2 and data[0] == 0x10 and data[1] == 0xF0:
            port.set_read_data(bytes([self.variant]))
        elif len(data) >= 3 and data[0] == 0x18:
            port.set_read_data(self.fifo_payload)


class FakeXDevice:
    """Emulates the NanoVNA-X shell protocol, including input echo.

    Framing follows the device's execution classes: deferred commands
    (sweep, scan) produce a prompt right after the echoed input, BEFORE
    their output; the scan output is terminated by a second prompt. The
    version reply is a bare semver string (no device name).
    """

    PROMPT = b"ch> "
    BANNER = b"\r\nNanoVNA-X Shell\r\n"

    def __init__(self, banner: bool = True, version_string: bytes = b"0.9.102") -> None:
        self.banner_enabled = banner
        self.version_string = version_string
        # Canned sweep result used by scan replies:
        # one point at 1 MHz, S11 = 0.5 - 0.5j, S21 = 0.1 - 0.1j.
        self.scan_points = (1_000_000,)
        self.scan_s11 = (complex(0.5, -0.5),)
        self.scan_s21 = (complex(0.1, -0.1),)

    def attach(self, port) -> None:
        # Session start: the one-line banner (SD-card builds only), then
        # the shell loop's first prompt. Without the banner — just the prompt.
        prefix = self.BANNER if self.banner_enabled else b""
        port.set_read_data(prefix + self.PROMPT)

    def on_write(self, data: bytes, port) -> None:
        port.set_read_data(data)  # the shell echoes typed input
        line = data.split(b"\r\n", 1)[0].decode("utf-8", "replace").strip()
        if line.startswith("version"):
            # Inline command: reply immediately after the echo, one prompt.
            port.set_read_data(self.version_string + b"\r\n" + self.PROMPT)
        elif line.startswith("sweep "):
            # Deferred command with no output: only the early prompt.
            port.set_read_data(self.PROMPT)
        elif line.startswith("scan "):
            # Deferred command with output: early prompt, payload, final prompt.
            port.set_read_data(self.PROMPT + self._emit_scan(line) + self.PROMPT)
        else:
            name = line.split(" ", 1)[0] if line else ""
            port.set_read_data(f"{name}?\r\n".encode() + self.PROMPT)

    def _emit_scan(self, line: str) -> bytes:
        parts = line.split()
        # parts: scan start stop [points] [mask]
        mask = int(parts[4], 0) if len(parts) >= 5 else 0
        points = len(self.scan_points)
        if mask == 0:
            return b""  # no output bits selected: the deferred scan prints nothing
        out = bytearray()
        if mask & 0x80:
            out += struct.pack("<HH", mask, points)
            for i, freq in enumerate(self.scan_points):
                if mask & 0x01:
                    out += struct.pack("<I", int(freq))
                if mask & 0x02:
                    s = self.scan_s11[i]
                    out += struct.pack("<ff", s.real, s.imag)
                if mask & 0x04:
                    s = self.scan_s21[i]
                    out += struct.pack("<ff", s.real, s.imag)
        else:
            lines = []
            for i, freq in enumerate(self.scan_points):
                fields: list[str] = []
                if mask & 0x01:
                    fields.append(str(int(freq)))
                if mask & 0x02:
                    s = self.scan_s11[i]
                    fields += [f"{s.real:.6f}", f"{s.imag:.6f}"]
                if mask & 0x04:
                    s = self.scan_s21[i]
                    fields += [f"{s.real:.6f}", f"{s.imag:.6f}"]
                lines.append(" ".join(fields))
            out = ("\r\n".join(lines) + "\r\n").encode() if lines else b""
        return bytes(out)  # payload only; on_write adds the prompts around it
