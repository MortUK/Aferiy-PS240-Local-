"""Regression tests for the PV surplus charge trigger range."""

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


def test_pv_surplus_charge_trigger_accepts_zero_to_100_watts() -> None:
    constants = _literal_constants()
    number_source = (COMPONENT / "number.py").read_text()
    coordinator_source = (COMPONENT / "coordinator.py").read_text()

    assert constants["MAX_SURPLUS_CHARGE_TRIGGER_W"] == 100
    assert "_attr_native_min_value = 0" in number_source
    assert "_attr_native_max_value = MAX_SURPLUS_CHARGE_TRIGGER_W" in number_source
    assert "min(int(value), MAX_SURPLUS_CHARGE_TRIGGER_W)" in coordinator_source
    assert "initial if initial is not None else 50" in number_source
