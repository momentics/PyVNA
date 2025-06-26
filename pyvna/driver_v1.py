import serial
import time
import re
from typing import Tuple

import driver_base
import data_types
#from pyvna.driver_base import BaseDriver
#from pyvna.data_types import SweepConfig, VNAData, Complex


class V1Driver(driver_base.BaseDriver):
    """
    Драйвер для устройств NanoVNA V1 (текстовый протокол).
    Реализует методы BaseDriver для взаимодействия с V1.
    """

    def __init__(self, port: serial.Serial):
        super().__init__(port)
        self.port.baudrate = 115200  # Стандартная скорость для V1
        self.port.timeout = 0.5      # Таймаут для чтения/записи

    def identify(self) -> Tuple[str, bool]:
        """
        Попытка идентифицировать устройство как NanoVNA V1.
        Отправляет команду 'version' и ищет 'nanovna' в ответе.
        """
        try:
            self.port.write(b"version\n")
            time.sleep(0.1)  # Дать время устройству на ответ
            response = self.port.readline().decode('utf-8', errors='ignore').strip()
            if "nanovna" in response.lower():
                return response, True
            return f"V1: Response not recognized: {response}", False
        except serial.SerialException as e:
            return f"V1: Serial communication error during identify: {e}", False
        except Exception as e:
            return f"V1: Unexpected error during identify: {e}", False

    def set_sweep(self, config: data_types.SweepConfig):
        """
        Устанавливает параметры сканирования на NanoVNA V1.
        """
        self.config = config
        command = f"sweep {int(config.start)} {int(config.stop)} {config.points}\n".encode('ascii')
        self.port.write(command)
        time.sleep(0.1)  # Дать время устройству на обработку команды

    def scan(self) -> data_types.VNAData:
        """
        Выполняет сканирование и считывает данные с NanoVNA V1.
        """
        self.port.write(b"data\n")
        time.sleep(0.1)  # Дать время устройству на сбор данных

        frequencies = []
        s11_data = []
        s21_data = []

        for _ in range(self.config.points):
            line = self.port.readline().decode('utf-8', errors='ignore').strip()
            parts = line.split()
            if len(parts) >= 5:
                try:
                    freq = float(parts[0])
                    s11_re = float(parts[1])
                    s11_im = float(parts[2])
                    s21_re = float(parts[3])
                    s21_im = float(parts[4])

                    frequencies.append(freq)
                    s11_data.append(data_types.Complex(s11_re, s11_im))
                    s21_data.append(data_types.Complex(s21_re, s21_im))
                except ValueError as e:
                    # Логировать некорректные строки, но не прерывать весь процесс
                    print(f"V1Driver: Error parsing line '{line}': {e}")
            else:
                print(f"V1Driver: Incomplete line received: '{line}'")

        if len(frequencies) != self.config.points:
            raise Exception(f"V1Driver: Expected {self.config.points} points, got {len(frequencies)}")

        return data_types.VNAData(frequencies, s11_data, s21_data)
