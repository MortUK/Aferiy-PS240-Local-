from pathlib import Path


INIT_SOURCE = Path("custom_components/aecc_battery/__init__.py").read_text()


def _stale_cleanup_body() -> str:
    return INIT_SOURCE.split("def _async_remove_stale_battery_soc_entities", 1)[1].split(
        "\ndef ",
        1,
    )[0]


def test_stale_battery_soc_cleanup_keeps_existing_entities() -> None:
    body = _stale_cleanup_body()

    assert "keeping existing Battery N SOC entities" in body
    assert "Storage_list snapshots can be partial" in body
    assert ".async_remove(" not in body
