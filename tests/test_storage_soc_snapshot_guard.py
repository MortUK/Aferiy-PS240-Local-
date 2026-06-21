"""Regression tests for rejecting bad multi-battery Storage_list snapshots."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COORDINATOR = ROOT / "custom_components" / "aecc_battery" / "coordinator.py"


def test_partial_storage_soc_snapshots_are_rejected() -> None:
    source = COORDINATOR.read_text()

    assert "_last_good_storage_soc_count" in source
    assert "partial Storage_list SOC snapshot" in source
    assert "count < self._last_good_storage_soc_count" in source


def test_online_zero_soc_storage_snapshots_are_rejected() -> None:
    source = COORDINATOR.read_text()

    assert "online battery reported 0% SOC in Storage_list" in source
    assert "soc == 0 and status != 0" in source
    assert "self._last_good_data is not None" in source
