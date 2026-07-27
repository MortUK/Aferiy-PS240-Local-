from pathlib import Path


INIT_SOURCE = Path("custom_components/aecc_battery/__init__.py").read_text()


def _stale_cleanup_body() -> str:
    return INIT_SOURCE.split("def _async_remove_stale_battery_soc_entities", 1)[1].split(
        "\ndef ",
        1,
    )[0]


def test_stale_battery_soc_cleanup_waits_for_confirmed_topology() -> None:
    body = _stale_cleanup_body()

    assert "keeping existing Battery N SOC entities" in body
    assert "confirmed_storage_slot_count" in body
    assert "if confirmed_slots <= 0" in body
    assert "registry.async_remove(entity_id)" in body


def test_stale_battery_soc_cleanup_is_not_run_from_live_poll_listener() -> None:
    setup_body = INIT_SOURCE.split("async def async_setup_entry", 1)[1].split(
        "\n\nasync def ",
        1,
    )[0]

    assert "_async_remove_stale_battery_soc_entities" not in setup_body


def test_sensor_setup_restores_known_slots_missing_from_startup_poll() -> None:
    sensor_source = Path("custom_components/aecc_battery/sensor.py").read_text()
    setup_body = sensor_source.split("async def async_setup_entry", 1)[1].split(
        "\n\nclass ",
        1,
    )[0]

    assert "max(len(storage_entries), coordinator.inverter_count)" in setup_body
    assert "entity_registry.async_get_entity_id(" in setup_body
    assert "for index in range(storage_slot_count)" in setup_body
