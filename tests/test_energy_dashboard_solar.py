"""Regression tests for Energy Dashboard solar-source support."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SENSOR = ROOT / "custom_components" / "aecc_battery" / "sensor.py"


def test_house_demand_reads_additional_energy_dashboard_solar() -> None:
    source = SENSOR.read_text()

    assert "energy_data.async_get_manager(hass)" in source
    assert "_energy_dashboard_additional_solar_w(" in source
    assert "additional_solar_power_w" in source


def test_house_demand_excludes_own_aecc_solar_entity() -> None:
    source = SENSOR.read_text()

    assert "registry_entry.platform == DOMAIN" in source
    assert "registry_entry.config_entry_id == coordinator._entry_id" in source
    assert "excluded_aecc_entities" in source


def test_energy_dashboard_power_units_are_converted_to_watts() -> None:
    source = SENSOR.read_text()

    assert '"kW": 1000.0' in source
    assert '"MW": 1_000_000.0' in source


def test_house_demand_energy_reconciles_cumulative_external_solar() -> None:
    source = SENSOR.read_text()

    assert 'source.get("stat_energy")' in source
    assert "_state_energy_kwh(" in source
    assert "_external_solar_reconciliation(" in source
    assert "actual_delta_kwh - sampled_delta_kwh" in source
    assert "additional_solar_energy_reconciliation" in source
    assert "_demand_profile_from_energy_history(" in source
    assert 'history_source = "house_demand_energy"' in source


def test_external_solar_energy_counter_resets_are_guarded() -> None:
    source = SENSOR.read_text()

    assert 'status": "counter_reset"' in source
    assert "pending_correction_kwh" in source
