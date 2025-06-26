import serial
import time
import struct
from typing import Tuple

import driver_base
import data_types
#from pyvna.driver_base import BaseDriver
#from pyvna.data_types import SweepConfig, VNAData, Complex


# Opcodes и адреса регистров, полученные из реверс-инжиниринга
OP_NOP = 0x00
OP_READ = 0x10
OP_READ_FIFO = 0x18
OP_WRITE2 = 0x21
OP_WRITE4 = 0x22

ADDR_SWEEP_START = 0x00
ADDR_SWEEP_STEP = 0x10
ADDR_SWEEP_POINTS = 0x20
ADDR_VALS_FIFO = 0x30
ADDR_DEVICE_VARIANT = 0xF0


class V2Driver(driver_base.BaseDriver):
    """
    Драйвер для устройств NanoVNA V2 / LiteVNA (бинарный протокол).
    Реализует методы BaseDriver для взаимодействия с V2.
    Реализован на основе реверс-инжиниринга (scikit-rf, официальные прошивки).
    """

    def __init__(self, port: serial.Serial):
        super().__init__(port)
        # V2 работает на 115200, но это не так критично, как для V1, т.к.
        # протокол самосинхронизирующийся.
        self.port.baudrate = 115200
        self.port.timeout = 1.0  # Увеличенный таймаут для бинарных операций
        self._reset_protocol()

    def _reset_protocol(self):
        """
        Отправляет 8x NOP (0x00) байтов для сброса состояния протокола V2.
        Это необходимо для синхронизации при каждом новом подключении.
        """
        self.port.write(bytes([OP_NOP] * 8))
        time.sleep(0.05)  # Дать устройству время на сброс

    def identify(self) -> Tuple[str, bool]:
        """
        Попытка идентифицировать устройство как NanoVNA V2 / LiteVNA.
        Чтение регистра ADDR_DEVICE_VARIANT.
        """
        try:
            self._reset_protocol() # Повторный сброс для надежной идентификации
            self.port.write(bytes([OP_READ, ADDR_DEVICE_VARIANT]))
            response = self.port.read(1)
            if not response:
                return "V2: No response to identify command.", False
            
            variant = response[0]
            if variant in [2, 4]:  # 2: NanoVNA V2, 4: V2 Plus4/LiteVNA 64
                return f"NanoVNA_V2 (Variant {variant})", True
            return f"V2: Unknown device variant: {variant}", False
        except serial.SerialException as e:
            return f"V2: Serial communication error during identify: {e}", False
        except Exception as e:
            return f"V2: Unexpected error during identify: {e}", False

    def set_sweep(self, config: data_types.SweepConfig):
        """
        Устанавливает параметры сканирования на NanoVNA V2/LiteVNA.
        Частоты записываются как 64-битные целые.
        """
        self.config = config
        
        # Шаг частоты вычисляется для (points-1) интервалов
        if config.points <= 1:
            step = 0
        else:
            step = (config.stop - config.start) / (config.points - 1)

        # Запись начальной частоты (Гц) - 64-битное целое
        self._write_reg64(ADDR_SWEEP_START, int(config.start))
        # Запись шага частоты (Гц) - 64-битное целое
        self._write_reg64(ADDR_SWEEP_STEP, int(step))
        # Запись количества точек - 16-битное целое
        self._write_reg16(ADDR_SWEEP_POINTS, config.points)

        time.sleep(0.05) # Дать устройству время на применение настроек

    def scan(self) -> data_types.VNAData:
        """
        Выполняет сканирование и считывает данные с NanoVNA V2/LiteVNA.
        Запрашивает все точки из FIFO за один раз для эффективности.
        """
        # Команда запроса данных из FIFO: OP_READ_FIFO, ADDR_VALS_FIFO, 0x00 (read all)
        self.port.write(bytes([OP_READ_FIFO, ADDR_VALS_FIFO, 0x00]))

        # Каждая точка измерения занимает 32 байта:
        # S11_re (float32), S11_im (float32), S12_re (float32), S12_im (float32),
        # S21_re (float32), S21_im (float32), S22_re (float32), S22_im (float32)
        # Мы интересуемся S11 и S21.
        expected_bytes = self.config.points * 32
        
        # Чтение ожидаемого количества байтов
        data_raw = self.port.read(expected_bytes)
        
        if len(data_raw) != expected_bytes:
            raise Exception(f"V2Driver: Expected {expected_bytes} bytes, got {len(data_raw)}")

        return self._parse_binary_data(data_raw)

    def _write_reg64(self, address: int, value: int):
        """
        Записывает 64-битное целое значение в регистр устройства.
        """
        # OP_WRITE4 (запись 4 байтов) + (2 байта? нет, 4 байта на запись, 64-бит = 8 байт)
        # Протокол V2:
        # 0x22 (WRITE4) + адрес + 4 байта данных (float32)
        # 0x23 (WRITE_MANY_4) + адрес + N*4 байт данных
        # В данном случае используем 0x22 (WRITE4), но посылаем 8 байт (uint64)
        # Потому что V2 ожидает uint64 для частот
        cmd = bytes([OP_WRITE4 + 2, address]) + value.to_bytes(8, 'little')
        self.port.write(cmd)

    def _write_reg16(self, address: int, value: int):
        """
        Записывает 16-битное целое значение в регистр устройства.
        """
        cmd = bytes([OP_WRITE2, address]) + value.to_bytes(2, 'little')
        self.port.write(cmd)

    def _parse_binary_data(self, raw_data: bytes) -> data_types.VNAData:
        """
        Парсит необработанные бинарные данные, полученные от V2.
        """
        frequencies = []
        s11_data = []
        s21_data = []
        
        # Вычисляем шаг частоты для генерации массива частот
        if self.config.points <= 1:
            step = 0
        else:
            step = (self.config.stop - self.config.start) / (self.config.points - 1)

        for i in range(self.config.points):
            offset = i * 32  # Каждая точка 32 байта
            
            # Частота генерируется на основе start, stop, points
            current_freq = self.config.start + i * step
            frequencies.append(current_freq)

            # S11: 4 байта real, 4 байта imag
            s11_re = struct.unpack('<f', raw_data[offset:offset+4])[0]
            s11_im = struct.unpack('<f', raw_data[offset+4:offset+8])[0]
            s11_data.append(data_types.Complex(s11_re, s11_im))

            # S21: 4 байта real, 4 байта imag (смещение 16 байт от начала точки)
            s21_re = struct.unpack('<f', raw_data[offset+16:offset+20])[0]
            s21_im = struct.unpack('<f', raw_data[offset+20:offset+24])[0]
            s21_data.append(data_types.Complex(s21_re, s21_im))

        return data_types.VNAData(frequencies, s11_data, s21_data)
