"""Regression tests for cautious pre-sunrise solar credit."""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SENSOR = ROOT / "custom_components" / "aecc_battery" / "sensor.py"


def _constants() -> dict[str, object]:
    values: dict[str, object] = {}
    tree = ast.parse(SENSOR.read_text())
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        try:
            values[target.id] = ast.literal_eval(node.value)
        except (TypeError, ValueError):
            continue
    return values


def test_pre_sunrise_solar_credit_remains_tiered_and_cautious() -> None:
    constants = _constants()

    assert constants["_OVERNIGHT_NO_USEFUL_SOLAR_CREDIT_FACTOR"] == 0.65
    assert constants["_OVERNIGHT_PRE_USEFUL_SOLAR_CREDIT_FACTOR"] == 0.65
    assert constants["_OVERNIGHT_BALANCED_SOLAR_PRE_USEFUL_CREDIT_FACTOR"] == 0.75
    assert constants["_OVERNIGHT_STRONG_SOLAR_PRE_USEFUL_CREDIT_FACTOR"] == 0.90
