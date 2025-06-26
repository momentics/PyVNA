import unittest
import serial
import threading
import time
import struct
import math

from data_types import SweepConfig, VNAData, Complex
from driver_v1 import V1Driver
from driver_v2 import V2Driver
from device_manager import DeviceManager


# Мок-объект для pyserial.Serial
class MockSerialPort:
    def __init__(self, responses=None):
        self._read_buffer = b''
        self._write_buffer = b''
        self._responses = responses if responses is not None else []
        self._lock = threading.Lock()
        self.baudrate = 115200
        self.timeout = 1.0
        self.is_open = True
        self._response_idx = 0

    def write(self, data):
        with self._lock:
            self._write_buffer += data
            # Для упрощения: сразу помещаем ответ в буфер чтения
            if self._response_idx < len(self._responses):
                self._read_buffer += self._responses[self._response_idx]
                self._response_idx += 1
            elif "version" in data.decode(errors='ignore'): # Для V1 identify
                self._read_buffer += b"NanoVNA H Version 1.0\n"
            elif data == b'\x10\xf0': # Для V2 identify (opREAD, ADDR_DEVICE_VARIANT)
                self._read_buffer += b'\x02' # Вариант V2

    def read(self, size=1):
        with self._lock:
            data = self._read_buffer[:size]
            self._read_buffer = self._read_buffer[size:]
            return data

    def readline(self):
        with self._lock:
            if b'\n' in self._read_buffer:
                line, rest = self._read_buffer.split(b'\n', 1)
                self._read_buffer = rest
                return line + b'\n'
            # Иначе имитируем таймаут или неполную строку
            data = self._read_buffer
            self._read_buffer = b''
            return data

    def close(self):
        self.is_open = False
        self._read_buffer = b''
        self._write_buffer = b''
        self._response_idx = 0

    def open(self):
        self.is_open = True

    def flush(self):
        pass

    def reset_buffers(self):
        self._read_buffer = b''
        self._write_buffer = b''
        self._response_idx = 0


class TestDrivers(unittest.TestCase):

    def test_v1_driver_identify_success(self):
        mock_port = MockSerialPort()
        driver = V1Driver(mock_port)
        name, success = driver.identify()
        self.assertTrue(success)
        self.assertIn("NanoVNA", name)

    def test_v1_driver_set_sweep_and_scan(self):
        mock_port = MockSerialPort(responses=[
            b"1000000 0.5 0.1 0.2 0.3\n",
            b"2000000 0.6 0.2 0.3 0.4\n"
        ])
        driver = V1Driver(mock_port)
        config = SweepConfig(start=1_000_000, stop=2_000_000, points=2)
        driver.set_sweep(config)
        
        vna_data = driver.scan()
        self.assertEqual(len(vna_data.frequencies), 2)
        self.assertEqual(vna_data.s11[0], Complex(0.5, 0.1))
        self.assertEqual(vna_data.s21[1], Complex(0.3, 0.4))
        
        self.assertIn(b"data\n", mock_port._write_buffer)
        self.assertIn(b"sweep 1000000 2000000 2\n", mock_port._write_buffer)


    def test_v2_driver_identify_success(self):
        mock_port = MockSerialPort()
        driver = V2Driver(mock_port)
        name, success = driver.identify()
        self.assertTrue(success)
        self.assertIn("NanoVNA_V2", name)

    def test_v2_driver_set_sweep_and_scan(self):
        mock_port = MockSerialPort()
        driver = V2Driver(mock_port)
        
        # Подготовка бинарных данных для V2 (1 точка)
        # S11_re, S11_im, S12_re, S12_im, S21_re, S21_im, S22_re, S22_im (все float32)
        s11_re, s11_im = 0.5, 0.1
        s21_re, s21_im = 0.2, 0.3
        dummy_val = 0.0 # для S12, S22
        
        response_data = b''
        response_data += struct.pack('<f', s11_re)
        response_data += struct.pack('<f', s11_im)
        response_data += struct.pack('<f', dummy_val) # S12_re
        response_data += struct.pack('<f', dummy_val) # S12_im
        response_data += struct.pack('<f', s21_re)
        response_data += struct.pack('<f', s21_im)
        response_data += struct.pack('<f', dummy_val) # S22_re
        response_data += struct.pack('<f', dummy_val) # S22_im
        
        # Добавляем бинарный ответ в MockSerialPort
        mock_port._responses = [response_data]

        config = SweepConfig(start=1_000_000, stop=1_000_000, points=1)
        driver.set_sweep(config)
        
        vna_data = driver.scan()
        
        self.assertEqual(len(vna_data.frequencies), 1)
        self.assertAlmostEqual(vna_data.s11[0].real, s11_re)
        self.assertAlmostEqual(vna_data.s11[0].imag, s11_im)
        self.assertAlmostEqual(vna_data.s21[0].real, s21_re)
        self.assertAlmostEqual(vna_data.s21[0].imag, s21_im)
        
        # Проверка отправленных команд (частичная, для бинарных сложнее)
        self.assertIn(bytes([V2Driver.OP_READ_FIFO, V2Driver.ADDR_VALS_FIFO, 0x00]), mock_port._write_buffer)


    def test_device_manager_get_device_v1(self):
        # Используем мок-порт для симуляции V1 устройства
        mock_port = MockSerialPort()
        device_manager = DeviceManager()
        
        # Имитируем ответ NanoVNA V1
        mock_port._responses = [b"NanoVNA H\n"]
        
        # Подменяем серийный порт в DeviceManager на наш мок
        # Это неидеальный способ, лучше использовать паттерн Adapter для Serial
        # Но для теста быстро
        device_manager._driver_factory = lambda p: V1Driver(p) if "V1" in p.name else None 
        
        # Запускаем get_device
        vna = device_manager.get_device("mock_port_v1")
        self.assertIsInstance(vna._driver, V1Driver)
        self.assertEqual(len(device_manager._devices), 1)

if __name__ == '__main__':
    unittest.main()
