"""Driver implementation for the NanoVNA-X shell protocol."""
from __future__ import annotations

import struct
import time
from dataclasses import dataclass, field

from .util.serial_port import SerialPortInterface
from .models import SweepConfig, VNAData


@dataclass
class XDriver:
    """Driver implementation for NanoVNA-X shell protocol."""
    port: SerialPortInterface
    config: SweepConfig = field(default_factory=lambda: SweepConfig(0.0, 0.0, 0))
    
    def identify(self) -> str:
        """Identify the device using the version command."""
        # First wait for any initial prompt if available
        self._flush_input()

        # Send version command
        self.port.write(b"version\r\n")

        # Read response until we see the prompt
        response = self._read_until_prompt()

        # The response structure should be: [optional banner] + version info + ch> prompt
        # Check if the response ends with the shell prompt which is characteristic of NanoVNA-X
        if not response.strip().endswith('ch>'):
            raise RuntimeError("x: device did not send expected shell prompt")

        # Look for lines that contain NanoVNA identification
        lines = [line.strip() for line in response.split('\n') if line.strip() and not line.strip().endswith('ch>') and line.strip() != 'ch>']

        for line in lines:
            if "nanovna" in line.lower() and "x" in line.lower():
                # If we find a line with nanovna-x, this indicates a NanoVNA-X device
                return line.strip()
            elif "nanovna" in line.lower():
                # If we find a general nanovna response with the shell prompt,
                # treat it as compatible (some versions might not explicitly say "X")
                return line.strip()

        raise RuntimeError("x: device did not identify as NanoVNA-X compatible")

    def set_sweep(self, config: SweepConfig) -> None:
        """Configure sweep parameters."""
        # Set the sweep parameters
        cmd = f"sweep {int(config.start)} {int(config.stop)} {config.points}\r\n"
        self.port.write(cmd.encode())
        
        # Wait for command to complete
        self._read_until_prompt()
        
        self.config = config

    def scan(self) -> VNAData:
        """Perform a scan and return data."""
        if self.config.points <= 0:
            raise RuntimeError("x: sweep not configured or zero points requested")
        
        # Request scan data with mask 0x03 (include freq, S11, S21)
        # Use the scan command with mask to get textual output
        cmd = f"scan {int(self.config.start)} {int(self.config.stop)} {self.config.points} 0x03\r\n"
        self.port.write(cmd.encode())
        
        # Read the data until prompt
        response = self._read_until_prompt()
        
        # Parse text response
        return self._parse_text_data(response)

    def close(self) -> None:
        """Close the connection."""
        self.port.close()

    def _read_until_prompt(self) -> str:
        """Read data until the shell prompt 'ch> ' is received."""
        buffer = bytearray()
        timeout = 5.0  # 5 second timeout
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            chunk = self.port.read(1)
            if not chunk:
                time.sleep(0.01)  # Small delay to avoid busy waiting
                continue
            
            buffer.extend(chunk)
            
            # Check if the end of buffer contains the prompt
            buf_str = buffer.decode('utf-8', errors='ignore')
            if buf_str.endswith('ch> '):
                # Return everything except the prompt
                result = buf_str[:-4]  # Remove 'ch> ' from end
                return result
        
        raise RuntimeError(f"x: timeout waiting for prompt after {timeout}s")

    def _parse_text_data(self, response: str) -> VNAData:
        """Parse text response from scan command."""
        data = VNAData(frequencies=[], s11=[], s21=[])
        
        lines = response.strip().split('\n')
        
        for line in lines:
            line = line.strip()
            if not line or line.startswith('scan ') or line.startswith('ch>'):
                continue  # Skip command echoes and prompts
            
            # Parse line: freq s11_real s11_imag s21_real s21_im
            parts = line.split()
            if len(parts) >= 5:
                try:
                    freq = float(parts[0])
                    s11_real = float(parts[1])
                    s11_imag = float(parts[2])
                    s21_real = float(parts[3])
                    s21_imag = float(parts[4])
                    
                    data.frequencies.append(freq)
                    data.s11.append(complex(s11_real, s11_imag))
                    data.s21.append(complex(s21_real, s21_imag))
                except ValueError:
                    continue  # Skip lines with invalid data
        
        if len(data.frequencies) != self.config.points:
            raise ValueError(
                f"x: expected {self.config.points} data points, got {len(data.frequencies)}"
            )
        
        return data

    def _flush_input(self) -> None:
        """Flush any pending input data."""
        self._flush_input_capture()  # Call capture version and discard the result

    def _flush_input_capture(self) -> bytearray:
        """Flush any pending input data and return it."""
        # Set a short timeout to read any pending data if the port supports it
        old_timeout = getattr(self.port, 'timeout', None)
        if hasattr(self.port, 'set_read_timeout'):
            self.port.set_read_timeout(0.1)

        flushed_data = bytearray()
        try:
            while True:
                chunk = self.port.read(1024)
                if not chunk:
                    break
                flushed_data.extend(chunk)
        finally:
            # Restore original timeout if supported
            if hasattr(self.port, 'set_read_timeout'):
                self.port.set_read_timeout(old_timeout)

        return flushed_data

    def _read_frequencies(self) -> list[float]:
        """Read frequency list using the frequencies command."""
        self.port.write(b"frequencies\r\n")
        response = self._read_until_prompt()
        
        frequencies = []
        for line in response.strip().split('\n'):
            line = line.strip()
            if line and not line.startswith('frequencies') and not line.startswith('ch>'):
                try:
                    freq = float(line)
                    frequencies.append(freq)
                except ValueError:
                    continue
        
        return frequencies

    def _read_data_index(self, index: int = 0) -> VNAData:
        """Read data using the data command with index."""
        cmd = f"data {index}\r\n".encode()
        self.port.write(cmd)
        response = self._read_until_prompt()
        
        # Parse the data command response
        data = VNAData(frequencies=[], s11=[], s21=[])
        freq_list = self._read_frequencies()
        
        lines = response.strip().split('\n')
        s11_data = []
        
        for line in lines:
            line = line.strip()
            if line and not line.startswith('data ') and not line.startswith('ch>'):
                parts = line.split()
                if len(parts) >= 2:  # real imag
                    try:
                        s11_real = float(parts[0])
                        s11_imag = float(parts[1])
                        s11_data.append(complex(s11_real, s11_imag))
                    except ValueError:
                        continue
        
        # For index 0 (S11) or 1 (S21), return the data
        if index == 0:  # S11 data
            # We need to get S21 separately, so return empty for now and get from scan
            data.frequencies = freq_list[:len(s11_data)]
            data.s11 = s11_data
        elif index == 1:  # S21 data
            # S11 would need to be separately retrieved
            pass
        
        return data

    def _read_binary_data(self, expected_len: int) -> bytes:
        """Read binary data from the device."""
        data = bytearray()
        while len(data) < expected_len:
            remaining = expected_len - len(data)
            chunk = self.port.read(remaining)
            if not chunk:
                raise RuntimeError(f"x: expected {expected_len} bytes, got {len(data)}")
            data.extend(chunk)
        return bytes(data)

    def _read_scan_binary(self) -> VNAData:
        """Perform a scan with binary output."""
        if self.config.points <= 0:
            raise RuntimeError("x: sweep not configured or zero points requested")
        
        # Request scan with binary mask (0x83 = freq + S11 + S21 + binary)
        cmd = f"scan {int(self.config.start)} {int(self.config.stop)} {self.config.points} 0x83\r\n"
        self.port.write(cmd.encode())
        
        # Read the binary header (mask + point count)
        header = self._read_binary_data(4)
        mask = struct.unpack('<H', header[:2])[0]  # Little-endian 16-bit
        point_count = struct.unpack('<H', header[2:4])[0]  # Little-endian 16-bit
        
        # Calculate expected total bytes: point_count * (freq + s11 + s21)
        # freq = 4 bytes (uint32), each complex sample = 8 bytes (2 floats * 4 bytes)
        expected_bytes = point_count * (4 + 8 + 8)  # freq + s11_complex + s21_complex
        
        # Read the binary data
        raw_data = self._read_binary_data(expected_bytes)
        
        # Parse the data
        data = VNAData(frequencies=[], s11=[], s21=[])
        
        step = (self.config.stop - self.config.start) / max(1, self.config.points - 1) if self.config.points > 1 else 0
        
        for i in range(point_count):
            offset = i * 20  # 4 bytes for freq + 8 bytes for s11 + 8 bytes for s21
            if offset + 20 > len(raw_data):
                break
                
            # Frequency is 32-bit unsigned int
            freq_raw = raw_data[offset:offset+4]
            freq = struct.unpack('<I', freq_raw)[0]  # Little-endian unsigned int
            
            # S11 is two 32-bit floats
            s11_real = struct.unpack('<f', raw_data[offset+4:offset+8])[0]
            s11_imag = struct.unpack('<f', raw_data[offset+8:offset+12])[0]
            
            # S21 is two 32-bit floats
            s21_real = struct.unpack('<f', raw_data[offset+12:offset+16])[0]
            s21_imag = struct.unpack('<f', raw_data[offset+16:offset+20])[0]
            
            data.frequencies.append(freq)
            data.s11.append(complex(s11_real, s11_imag))
            data.s21.append(complex(s21_real, s21_imag))
        
        return data


__all__ = ["XDriver"]