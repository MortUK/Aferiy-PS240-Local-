"""Regression tests for the user-configurable SMART overnight buffer."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NUMBER = ROOT / "custom_components" / "aecc_battery" / "number.py"
SENSOR = ROOT / "custom_components" / "aecc_battery" / "sensor.py"
COORDINATOR = ROOT / "custom_components" / "aecc_battery" / "coordinator.py"
INIT = ROOT / "custom_components" / "aecc_battery" / "__init__.py"


def test_single_overnight_buffer_replaces_forecast_and_demand_scales() -> None:
    number_source = NUMBER.read_text()
    coordinator_source = COORDINATOR.read_text()

    assert "class AeccSmartOvernightBuffer" in number_source
    assert '_attr_name = "Overnight Buffer"' in number_source
    assert "_attr_native_min_value = 0" in number_source
    assert "_attr_native_max_value = 20" in number_source
    assert "smart_overnight_buffer_soc" in coordinator_source
    assert "smart_solar_forecast_scale" not in coordinator_source
    assert "smart_house_demand_scale" not in coordinator_source


def test_buffer_keeps_existing_automatic_safeguards() -> None:
    source = SENSOR.read_text()

    assert 'reasons = ["user_configured_base"]' in source
    assert '"limited_house_demand_history"' in source
    assert '"daily_forecast_without_timed_solar"' in source
    assert '"time_of_day_demand_fallback"' in source
    assert '"low_solar_forecast"' in source
    assert '"close_call_solar_forecast"' in source
    assert '"automatic_buffer_adjustment_soc"' in source


def test_withdrawn_scale_entities_are_removed() -> None:
    source = INIT.read_text()

    assert '_smart_solar_forecast_scale"' in source
    assert '_smart_house_demand_scale"' in source
