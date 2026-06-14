"""Regression tests for SMART History duration and weighting."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SENSOR = ROOT / "custom_components" / "aecc_battery" / "sensor.py"
COORDINATOR = ROOT / "custom_components" / "aecc_battery" / "coordinator.py"


def _module_constants(source_path: Path) -> dict[str, object]:
    constants: dict[str, object] = {}
    tree = ast.parse(source_path.read_text())
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        try:
            constants[target.id] = ast.literal_eval(node.value)
        except (TypeError, ValueError):
            continue
    return constants


def test_smart_history_uses_30_day_model_with_35_day_retention() -> None:
    constants = _module_constants(SENSOR)

    assert constants["_RUNTIME_RECORDER_HISTORY_DAYS"] == 30
    assert constants["_RUNTIME_RECORDER_RETENTION_DAYS"] == 35
    assert constants["_RUNTIME_RECORDER_PRIMARY_OCCUPIED_DAYS"] == 14
    assert constants["_RUNTIME_RECORDER_OLDER_DAY_WEIGHT_FACTOR"] == 0.25


def test_smart_history_initial_status_uses_30_day_lookback() -> None:
    source = COORDINATOR.read_text()

    assert '"recorder_history_lookback_days": 30' in source
    assert '"recorder_retention_days": 35' in source


def test_smart_history_weights_accepted_days_after_filtering() -> None:
    source = SENSOR.read_text()
    filter_position = source.index(
        "daily_averages, rejected_daily_averages = self._filter_runtime_history_days"
    )
    weighting_position = source.index(
        "self._apply_history_day_weights(daily_averages, local_now)"
    )

    assert weighting_position > filter_position


def test_smart_history_includes_the_latest_complete_rolling_day() -> None:
    source = SENSOR.read_text()

    assert "range(1, _RUNTIME_RECORDER_HISTORY_DAYS + 1)" in source
    assert '"recorder_history_uses_complete_rolling_days": True' in source
    assert '"recorder_history_excludes_current_day": True' not in source
