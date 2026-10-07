"""Tests for the core data models: Touchstone export, VSWR, validation."""

from __future__ import annotations

import math

import pytest

from pyvna.models import VNAData


def _data_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line and not line.startswith(("!", "#"))]


def test_touchstone_s11_golden() -> None:
    data = VNAData(
        frequencies=[1_234_567.89],
        s11=[complex(0.5, -0.5)],
        s21=[complex(0.1, -0.1)],
    )
    text = data.to_touchstone()
    assert "# Hz S RI R 50" in text
    # One parameter per file: exactly three values on the single data line.
    assert _data_lines(text) == ["1234567.890000 0.500000 -0.500000"]

    text_s21 = data.to_touchstone("s21")
    assert _data_lines(text_s21) == ["1234567.890000 0.100000 -0.100000"]

    with pytest.raises(ValueError):
        data.to_touchstone("s21 ")
    with pytest.raises(ValueError):
        data.to_touchstone("s31")


def test_vswr_inf() -> None:
    # Total reflection (|S11| = 1) maps to infinity, not a sentinel value.
    total = VNAData(frequencies=[1e6], s11=[complex(1.0, 0.0)], s21=[0j])
    assert total.calculate_vswr() == [math.inf]

    partial = VNAData(frequencies=[1e6], s11=[complex(0.5, 0.0)], s21=[0j])
    assert partial.calculate_vswr() == [pytest.approx(3.0)]


def test_vnadata_validate_alignment() -> None:
    mismatched = VNAData(frequencies=[1e6, 2e6], s11=[0j], s21=[0j, 0j])
    with pytest.raises(ValueError):
        mismatched.validate()

    nan_s21 = VNAData(
        frequencies=[1e6],
        s11=[complex(0.5, -0.5)],
        s21=[complex(float("nan"), 0.0)],
    )
    with pytest.raises(ValueError):
        nan_s21.validate()

    valid = VNAData(frequencies=[1e6], s11=[complex(0.5, -0.5)], s21=[complex(0.1, -0.1)])
    valid.validate()  # must not raise
