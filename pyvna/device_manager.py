import serial
import threading
import logging
from typing import Dict, Tuple

import driver_base
import driver_v1
import driver_v2
import data_types
#from pyvna.driver_base import BaseDriver
#from pyvna.driver_v1 import V1Driver
#from pyvna.driver_v2 import V2Driver
#from pyvna.data_types import VNA

class DeviceManager:
    """
    Управляет пулом VNA устройств, обеспечивая потокобезопасность.
    Является по сути синглтоном, поскольку создается один экземпляр.
    """
    _instance = None
    _lock = threading.Lock() # Мьютекс для управления singleton'ом

    def __new__(cls):
        """Реализация шаблона Singleton для DeviceManager."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(DeviceManager, cls).__new__(cls)
                cls._instance._devices: Dict[str, data_types.VNA] = {}
                cls._instance._device_locks: Dict[str, threading.Lock] = {} # Мьютексы для каждого порта
            return cls._instance

    def _get_port_lock(self, port_path: str) -> threading.Lock:
        """
        Возвращает мьютекс для конкретного порта, создавая его при необходимости.
        Используется для защиты доступа к физическому порту.
        """
        if port_path not in self._device_locks:
            with self._lock: # Защищаем создание мьютекса
                if port_path not in self._device_locks:
                    self._device_locks[port_path] = threading.Lock()
        return self._device_locks[port_path]

    def get_device(self, port_path: str) -> data_types.VNA:
        """
        Возвращает VNA устройство из пула. Если устройство не существует,
        оно создается, идентифицируется и добавляется в пул.
        """
        # Сначала проверяем без блокировки, если устройство уже есть
        if port_path in self._devices:
            return self._devices[port_path]

        # Если устройства нет, получаем блокировку для создания
        with self._get_port_lock(port_path):
            # Повторная проверка после получения блокировки (double-checked locking)
            if port_path in self._devices:
                return self._devices[port_path]

            logging.info(f"Attempting to open port {port_path} and identify device...")
            
            # Открытие порта
            try:
                # baudrate не важен для открытия, устанавливается в драйвере
                ser = serial.Serial(port_path, baudrate=115200, timeout=1) 
            except serial.SerialException as e:
                logging.error(f"Failed to open serial port {port_path}: {e}")
                raise Exception(f"Failed to open serial port {port_path}: {e}")
            
            # Фабрика драйверов: пытается идентифицировать устройство
            driver = self._driver_factory(ser)
            if not driver:
                ser.close()
                raise Exception(f"Could not identify device on port {port_path}. Is it a supported NanoVNA?")

            logging.info(f"Device on {port_path} identified as {driver.__class__.__name__}.")
            
            # Создаем обертку VNA и добавляем в пул
            vna_instance = data_types.VNA(driver)
            self._devices[port_path] = vna_instance
            return vna_instance

    def _driver_factory(self, port: serial.Serial) -> driver_base.BaseDriver | None:
        """
        Фабрика драйверов: пытается идентифицировать устройство с помощью разных драйверов.
        """
        drivers_to_try = [V1Driver(port), V2Driver(port)] # Порядок важен!
        
        for driver in drivers_to_try:
            name, success = driver.identify()
            if success:
                logging.info(f"Successfully identified device as {name} using {driver.__class__.__name__}.")
                return driver
            logging.debug(f"Driver {driver.__class__.__name__} failed to identify: {name}")
            
            # Важно: после неудачной идентификации драйвер может оставить порт в плохом состоянии.
            # Необходимо сбросить порт или переоткрыть его для следующего драйвера.
            # Для простоты, здесь просто закрываем и переоткрываем порт перед каждой попыткой
            # (кроме последней, т.к. она будет использована).
            # В более сложных случаях можно использовать более умные механизмы сброса.
            if driver != drivers_to_try[-1]:
                port.close()
                port.open() # Переоткрываем для следующей попытки
                
        return None

    def close_all_devices(self):
        """
        Закрывает все открытые устройства в пуле.
        """
        with self._lock:
            for port_path, vna_instance in self._devices.items():
                logging.info(f"Closing device on port {port_path}...")
                vna_instance.close()
            self._devices.clear()
            self._device_locks.clear()
