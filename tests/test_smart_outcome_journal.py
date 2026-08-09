"""Regression tests for the compact SMART overnight outcome journal."""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COORDINATOR = ROOT / "custom_components" / "aecc_battery" / "coordinator.py"
SENSOR = ROOT / "custom_components" / "aecc_battery" / "sensor.py"
DIAGNOSTICS = ROOT / "custom_components" / "aecc_battery" / "diagnostics.py"
FRONTEND = (
    ROOT
    / "custom_components"
    / "aecc_battery"
    / "frontend"
    / "aferiy-overnight-plan-card.js"
)


def _outcome_function():
    tree = ast.parse(COORDINATOR.read_text())
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_smart_outcome_result"
    )
    namespace: dict[str, object] = {
        "Any": object,
        "datetime": datetime,
    }
    compiled = compile(
        ast.Module(body=[function], type_ignores=[]),
        str(COORDINATOR),
        "exec",
    )
    exec(compiled, namespace)
    return namespace["_smart_outcome_result"]


def test_late_solar_explains_a_low_handover() -> None:
    classify = _outcome_function()
    forecast = datetime(2026, 8, 8, 7, 30, tzinfo=UTC)
    actual = forecast + timedelta(minutes=45)

    result = classify(13.0, 10.0, 40.0, 40.0, forecast, actual)

    assert result["result"] == "too_low"
    assert result["likely_cause"] == "useful_solar_later_than_forecast"
    assert result["handover_difference_soc"] == -3.0
    assert result["forecast_error_minutes"] == 45.0


def test_missed_locked_target_takes_priority() -> None:
    classify = _outcome_function()
    forecast = datetime(2026, 8, 8, 7, 30, tzinfo=UTC)

    result = classify(13.0, 9.0, 35.0, 40.0, forecast, forecast)

    assert result["result"] == "too_low"
    assert result["likely_cause"] == "locked_target_not_reached"
    assert result["locked_target_shortfall_at_off_peak_end_soc"] == 5.0


def test_handover_within_two_percent_is_about_right() -> None:
    classify = _outcome_function()
    handover = datetime(2026, 8, 8, 8, 0, tzinfo=UTC)

    result = classify(13.0, 12.0, 40.0, 40.0, handover, handover)

    assert result["result"] == "about_right"
    assert result["likely_cause"] == "within_tolerance"


def test_journal_is_persisted_capped_and_diagnostic_only() -> None:
    coordinator_source = COORDINATOR.read_text()
    sensor_source = SENSOR.read_text()
    diagnostics_source = DIAGNOSTICS.read_text()
    frontend_source = FRONTEND.read_text()

    assert "_SMART_OUTCOME_HISTORY_LIMIT = 60" in coordinator_source
    assert '"smart_outcome_history": self._smart_outcome_history' in coordinator_source
    assert "_SMART_OUTCOME_SOLAR_CONFIRM_SECONDS = 60" in coordinator_source
    assert "_smart_outcome_solar_candidate_soc" in coordinator_source
    assert "latest_smart_overnight_outcome" in sensor_source
    assert '"smart_overnight_outcomes": smart_outcomes_section' in diagnostics_source
    assert "Last SMART morning" in frontend_source
    assert "latest_smart_overnight_outcome" in frontend_source
    assert "soc_at_actual_useful_solar == null" in frontend_source
