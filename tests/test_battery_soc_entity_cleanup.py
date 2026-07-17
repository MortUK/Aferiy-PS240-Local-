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
