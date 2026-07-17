"""Regression tests for the user-configurable SMART overnight safety buffer."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NUMBER = ROOT / "custom_components" / "aecc_battery" / "number.py"
SENSOR = ROOT / "custom_components" / "aecc_battery" / "sensor.py"
COORDINATOR = ROOT / "custom_components" / "aecc_battery" / "coordinator.py"
INIT = ROOT / "custom_components" / "aecc_battery" / "__init__.py"
FRONTEND = (
    ROOT
    / "custom_components"
    / "aecc_battery"
    / "frontend"
    / "aferiy-overnight-plan-card.js"
)


def _module_function(name: str):
    tree = ast.parse(SENSOR.read_text())
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    namespace: dict[str, object] = {"_FULL_SOC": 100.0}
    compiled = compile(
        ast.Module(body=[function], type_ignores=[]),
        str(SENSOR),
        "exec",
    )
    exec(compiled, namespace)
    return namespace[name]


def test_single_overnight_buffer_replaces_forecast_and_demand_scales() -> None:
    number_source = NUMBER.read_text()
    coordinator_source = COORDINATOR.read_text()

    assert "class AeccSmartOvernightBuffer" in number_source
    assert '_attr_name = "Overnight Safety Buffer"' in number_source
    assert "_attr_native_min_value = 0" in number_source
    assert "_attr_native_max_value = 20" in number_source
    assert "smart_overnight_buffer_soc" in coordinator_source
    assert "smart_solar_forecast_scale" not in coordinator_source
    assert "smart_house_demand_scale" not in coordinator_source


def test_three_percent_buffer_protects_a_thirteen_percent_handover_floor() -> None:
    planned_floor = _module_function("_planned_handover_floor_soc")

    assert planned_floor(10.0, 3.0) == 13.0


def test_negative_adaptive_correction_cannot_consume_the_buffer() -> None:
    effective_adjustment = _module_function(
        "_effective_adaptive_target_adjustment_soc"
    )
    compose_energy = _module_function("_compose_required_usable_energy_kwh")

    adjustment_soc = effective_adjustment(-5.0, True)
    required_kwh = compose_energy(
        0.2,
        0.3,
        0.0,
        adjustment_soc / 100 * 10.0,
    )

    assert adjustment_soc == 0.0
    assert required_kwh == 0.5


def test_negative_adaptive_correction_remains_available_for_whole_day_plans() -> None:
    effective_adjustment = _module_function(
        "_effective_adaptive_target_adjustment_soc"
    )

    assert effective_adjustment(-2.0, False) == -2.0


def test_learned_morning_demand_is_covered_before_the_buffer() -> None:
    compose_energy = _module_function("_compose_required_usable_energy_kwh")

    assert compose_energy(0.5, 0.3, 0.0, 0.0) == 0.8


def test_buffer_keeps_independent_automatic_safeguards() -> None:
    source = SENSOR.read_text()

    assert 'reasons = ["user_configured_handover_safety_buffer"]' in source
    assert '"limited_house_demand_history"' in source
    assert '"daily_forecast_without_timed_solar"' in source
    assert '"time_of_day_demand_fallback"' in source
    assert '"low_solar_forecast"' in source
    assert '"close_call_solar_forecast"' in source
    assert '"automatic_buffer_adjustment_soc"' in source


def test_plan_card_explains_the_handover_safety_floor() -> None:
    source = FRONTEND.read_text()

    assert "planned_useful_solar_handover_floor_soc" in source
    assert "planned at useful-solar handover" in source
    assert "already learned" not in source


def test_withdrawn_scale_entities_are_removed() -> None:
    source = INIT.read_text()

    assert '_smart_solar_forecast_scale"' in source
    assert '_smart_house_demand_scale"' in source
