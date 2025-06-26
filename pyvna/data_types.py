import math
import time
from typing import List, NamedTuple
import cmath

#from driver_base import BaseDriver

# Максимальное количество точек сканирования для предотвращения DoS-атак.
# Из Go-кода: 10001 (10000 точек + 1 для интервалов).
MAX_SWEEP_POINTS = 10001


class Complex(NamedTuple):
    """
    Представляет комплексное число (real + imag*j).
    Используется вместо стандартного complex() для явности и NamedTuple.
    """
    real: float
    imag: float

    def to_cmath(self) -> complex:
        """Конвертирует в стандартный Python complex."""
        return complex(self.real, self.imag)


class SweepConfig(NamedTuple):
    """
    Конфигурация сканирования VNA.
    Содержит начальную частоту (Гц), конечную частоту (Гц) и количество точек.
    """
    start: float
    stop: float
    points: int

    def __post_init__(self):
        """Валидация после инициализации."""
        if not (self.start < self.stop and self.start > 0 and self.stop > 0):
            raise ValueError("Start frequency must be less than stop frequency and both must be positive.")
        if not (1 <= self.points <= MAX_SWEEP_POINTS):
            raise ValueError(f"Number of points must be between 1 and {MAX_SWEEP_POINTS}.")


class VNAData(NamedTuple):
    """
    Результаты сканирования VNA.
    Содержит списки частот, S11 и S21 данных.
    """
    frequencies: List[float]
    s11: List[Complex]
    s21: List[Complex]

    def to_touchstone(self) -> str:
        """
        Конвертирует данные VNA в формат Touchstone (.s2p).
        """
        lines = [
            "! PyVNA Data Export",
            f"! Date: {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC",
            "# Hz S RI R 50"  # Частота, S-параметры, Real/Imaginary, сопротивление 50 Ом
        ]
        for i in range(len(self.frequencies)):
            freq = int(self.frequencies[i])
            s11_c = self.s11[i].to_cmath()
            s21_c = self.s21[i].to_cmath()
            lines.append(f"{freq} {s11_c.real:.6f} {s11_c.imag:.6f} {s21_c.real:.6f} {s21_c.imag:.6f}")
        return "\n".join(lines)

    def calculate_vswr(self) -> List[float]:
        """
        Рассчитывает коэффициент стоячей волны (VSWR) на основе S11.
        """
        vswr_values = []
        for s11_complex in self.s11:
            s11_c = s11_complex.to_cmath()
            gamma = abs(s11_c)  # Magnitude of reflection coefficient
            if gamma >= 1.0:
                vswr_values.append(9999.0)  # Бесконечный или очень большой VSWR
            else:
                vswr = (1.0 + gamma) / (1.0 - gamma)
                vswr_values.append(vswr)
        return vswr_values


class VNA:
    """
    Обертка для VNA устройства, использующая паттерн Bridge.
    Делегирует фактическую работу по коммуникации выбранному драйверу.
    """
    #def __init__(self, driver: BaseDriver):
    def __init__(self, driver: None):
        self._driver = driver
        # В Python нет встроенного контекста как в Go,
        # но драйверы могут управлять своим жизненным циклом через self._driver.close()

    def set_sweep(self, config: SweepConfig):
        """Устанавливает параметры сканирования."""
        # Валидация SweepConfig происходит при его инициализации
        self._driver.set_sweep(config)

    def get_data(self) -> VNAData:
        """Выполняет сканирование и получает данные."""
        return self._driver.scan()

    def close(self):
        """Закрывает устройство."""
        self._driver.close()
