from pathlib import Path


SENSOR_SOURCE = Path("custom_components/aecc_battery/sensor.py").read_text()


def test_storage_soc_sensor_holds_value_when_slot_is_present() -> None:
    assert "def _storage_slot_present" in SENSOR_SOURCE
    assert "self._index < len(self.coordinator.storage_entries)" in SENSOR_SOURCE
    assert "if self._storage_slot_present() and self._last_value is not None" in SENSOR_SOURCE
