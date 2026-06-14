"""Regression tests for the user-facing off-peak tariff presets."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONST = ROOT / "custom_components" / "aecc_battery" / "const.py"


def _constants() -> dict[str, object]:
    values: dict[str, object] = {}
    tree = ast.parse(CONST.read_text())
    for node in tree.body:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            value = node.value
        if not isinstance(target, ast.Name) or value is None:
            continue
        try:
            values[target.id] = eval(
                compile(ast.Expression(value), str(CONST), "eval"),
                {"__builtins__": {}},
                values,
            )
        except (NameError, TypeError):
            continue
    return values


def test_current_uk_tariff_windows() -> None:
    presets = _constants()["TARIFF_PRESETS"]

    assert presets == {
        "snug_octopus": ("00:30", "06:30"),
        "octopus_intelligent_go": ("23:30", "05:30"),
        "octopus_go": ("23:30", "05:30"),
        "edf_goelectric_35": ("23:00", "06:00"),
        "british_gas_electric_driver": ("00:00", "05:00"),
        "eon_next_drive": ("00:00", "06:00"),
        "british_gas_economy_7": ("00:30", "07:30"),
        "edf_e7_fixed": ("00:30", "07:30"),
        "ovo_simpler_energy_e7": ("00:30", "07:30"),
        "octopus_e7": ("00:30", "07:30"),
        "eon_next_pumped_fixed": ("22:00", "06:00"),
        "custom": ("23:30", "05:30"),
    }


def test_tariff_labels_cover_every_preset() -> None:
    constants = _constants()
    presets = constants["TARIFF_PRESETS"]
    labels = constants["TARIFF_PRESET_LABELS"]

    assert labels.keys() == presets.keys()
