from abc import ABC, abstractmethod
from typing import Tuple

import serial

#from data_types import SweepConfig, VNAData
import data_types

class BaseDriver(ABC):
    """
    Абстрактный базовый класс для драйверов VNA устройств.
    Реализует паттерн Bridge: определяет интерфейс для конкретных драйверов.
    """

    def __init__(self, port: 'serial.Serial'):
        """
        Инициализация драйвера с объектом последовательного порта.
        """
        self.port = port
        self.config: SweepConfig = data_types.SweepConfig(0, 0, 0)

    @abstractmethod
    def identify(self) -> Tuple[str, bool]:
        """
        Пытается идентифицировать устройство.
        Возвращает (имя_устройства, успешно_идентифицировано).
        Если успешно_идентифицировано == False, err_msg содержит причину.
        """
        pass

    @abstractmethod
    def set_sweep(self, config: data_types.SweepConfig):
        """
        Устанавливает параметры сканирования на устройстве.
        """
        pass

    @abstractmethod
    def scan(self) -> data_types.VNAData:
        """
        Выполняет одно сканирование и возвращает данные VNA.
        """
        pass

    def close(self):
        """
        Закрывает последовательный порт.
        """
        if self.port and self.port.is_open:
            self.port.close()
