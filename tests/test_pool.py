"""Tests for VNAPool: caching, concurrency, error propagation and release."""

from __future__ import annotations

import threading
import time

from pyvna import driver as driver_module
from pyvna.driver import VNAPool
from pyvna.vna import VNA
from tests.devices import FakeV2Device
from tests.test_vna import MockSerialPort


class RecordingPort(MockSerialPort):
    """MockSerialPort that records whether close() was called."""

    def __init__(self, device=None) -> None:
        super().__init__(device=device)
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _make_open_port(opens: list[str], delay: float = 0.0):
    def fake_open(path, *args, **kwargs):
        opens.append(path)
        if delay:
            time.sleep(delay)
        return RecordingPort(device=FakeV2Device())

    return fake_open


def test_pool_caches_instances(monkeypatch) -> None:
    opens: list[str] = []
    monkeypatch.setattr(driver_module, "open_port", _make_open_port(opens))
    pool = VNAPool()
    vna1 = pool.get("COM1")
    vna2 = pool.get("COM1")
    assert vna1 is vna2
    assert opens == ["COM1"]


def test_pool_concurrent_get_opens_once(monkeypatch) -> None:
    opens: list[str] = []
    monkeypatch.setattr(driver_module, "open_port", _make_open_port(opens, delay=0.2))
    pool = VNAPool()
    barrier = threading.Barrier(4)
    results: list[VNA | None] = [None] * 4
    errors: list[Exception | None] = [None] * 4

    def worker(index: int) -> None:
        barrier.wait()
        try:
            results[index] = pool.get("COM1")
        except Exception as exc:  # a failed get must surface, never vanish
            errors[index] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(error is None for error in errors)
    assert opens == ["COM1"]  # the slow open ran exactly once
    assert results[0] is not None
    assert all(result is results[0] for result in results)


def test_pool_open_failure_propagates_and_retries(monkeypatch) -> None:
    calls = 0
    failing = threading.Event()
    hold = threading.Event()

    def fake_open(path, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            # The first call fails, but only after the test confirms a second
            # get() is waiting on this port's opening event.
            failing.set()
            hold.wait()
            raise RuntimeError("cannot open the serial port")
        return RecordingPort(device=FakeV2Device())

    monkeypatch.setattr(driver_module, "open_port", fake_open)
    pool = VNAPool()

    owner_error: list[RuntimeError] = []

    def owner_attempt() -> None:
        try:
            pool.get("COM1")
        except RuntimeError as exc:
            owner_error.append(exc)

    waiter_error: list[RuntimeError] = []

    def waiter_attempt() -> None:
        try:
            pool.get("COM1")
        except RuntimeError as exc:
            waiter_error.append(exc)

    owner_thread = threading.Thread(target=owner_attempt)
    owner_thread.start()
    assert failing.wait(5.0)  # the owner is inside the failing open call
    waiter_thread = threading.Thread(target=waiter_attempt)
    waiter_thread.start()
    time.sleep(0.2)  # let the waiter register on the owner's opening event
    hold.set()  # release the owner into the failure path
    owner_thread.join(timeout=10)
    waiter_thread.join(timeout=10)

    assert len(owner_error) == 1
    assert len(waiter_error) == 1
    assert waiter_error[0] is owner_error[0]  # the same exception propagates

    vna = pool.get("COM1")  # retry after the failing call is exhausted
    assert isinstance(vna, VNA)
    assert calls == 2


def test_pool_release(monkeypatch) -> None:
    ports: list[RecordingPort] = []

    def fake_open(path, *args, **kwargs):
        port = RecordingPort(device=FakeV2Device())
        ports.append(port)
        return port

    monkeypatch.setattr(driver_module, "open_port", fake_open)
    pool = VNAPool()
    vna = pool.get("COM1")
    assert isinstance(vna, VNA)
    assert pool.release("COM1") is True
    assert ports[0].closed is True  # release closed the device's port
    assert pool.release("COM1") is False


def test_pool_close_all_closes_devices(monkeypatch) -> None:
    ports: list[RecordingPort] = []

    def fake_open(path, *args, **kwargs):
        port = RecordingPort(device=FakeV2Device())
        ports.append(port)
        return port

    monkeypatch.setattr(driver_module, "open_port", fake_open)
    pool = VNAPool()
    vna1 = pool.get("COM1")
    pool.get("COM2")
    pool.close_all()
    assert len(ports) == 2
    assert all(port.closed for port in ports)
    # A subsequent get reopens the device instead of returning a closed one.
    vna2 = pool.get("COM1")
    assert vna2 is not vna1
    assert len(ports) == 3
    assert ports[2].closed is False


def test_pool_different_ports_independent(monkeypatch) -> None:
    opens: list[str] = []
    opens_lock = threading.Lock()
    barrier = threading.Barrier(2)

    def fake_open(path, *args, **kwargs):
        with opens_lock:
            opens.append(path)
        time.sleep(0.1)  # a slow open must not block other ports
        return RecordingPort(device=FakeV2Device())

    monkeypatch.setattr(driver_module, "open_port", fake_open)
    pool = VNAPool()
    results: dict[str, VNA | None] = {"a": None, "b": None}

    def worker(name: str, port_path: str) -> None:
        barrier.wait()
        results[name] = pool.get(port_path)

    threads = [
        threading.Thread(target=worker, args=("a", "COM1")),
        threading.Thread(target=worker, args=("b", "COM2")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert opens.count("COM1") == 1
    assert opens.count("COM2") == 1
    assert results["a"] is not None
    assert results["b"] is not None
    assert results["a"] is not results["b"]
