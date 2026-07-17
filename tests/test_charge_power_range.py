"""Regression tests for the local charge-power range and default."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "aecc_battery"


def _literal_constants() -> dict[str, object]:
    tree = ast.parse((COMPONENT / "const.py").read_text())
    values: dict[str, object] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        try:
            values[target.id] = ast.literal_eval(node.value)
        except (ValueError, TypeError):
            continue
    return values


def test_charge_power_range_starts_at_200w_and_defaults_to_800w() -> None:
    constants = _literal_constants()
    number_source = (COMPONENT / "number.py").read_text()

    assert constants["MIN_CHARGE_POWER_W"] == 200
    assert constants["DEFAULT_CHARGE_POWER_W"] == 800
    assert "_attr_native_min_value = MIN_CHARGE_POWER_W" in number_source
    assert "default_value = DEFAULT_CHARGE_POWER_W" in number_source


def test_operating_mode_accepts_the_full_charge_slider_range() -> None:
    select_source = (COMPONENT / "select.py").read_text()

    assert "power = _clamp(power, MIN_CHARGE_POWER_W, 1200)" in select_source
    assert "power = _clamp(power, 400, 1200)" not in select_source


def test_automatic_charging_keeps_the_800w_new_install_default() -> None:
    coordinator_source = (COMPONENT / "coordinator.py").read_text()

    assert (
        "self.commanded_charge_power: int = DEFAULT_CHARGE_POWER_W"
        in coordinator_source
    )
    assert '"commanded_charge_power",\n                DEFAULT_CHARGE_POWER_W' in (
        coordinator_source
    )
