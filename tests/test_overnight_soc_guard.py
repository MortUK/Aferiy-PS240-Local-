"""Regression tests for SMART overnight SOC sanity checks."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COORDINATOR = ROOT / "custom_components" / "aecc_battery" / "coordinator.py"


def test_overnight_charge_requires_stable_soc_before_starting() -> None:
    source = COORDINATOR.read_text()

    assert "_overnight_charge_start_confirmed" in source
    assert "_overnight_charge_confirm_count >= 2" in source
    assert "waiting for one more stable reading before charging" in source


def test_overnight_charge_ignores_sudden_soc_drop_glitches() -> None:
    source = COORDINATOR.read_text()

    assert "_overnight_last_trusted_soc" in source
    assert "age_seconds <= 180 and sudden_drop > 5" in source
    assert "self._reset_overnight_charge_confirmation()" in source


def test_overnight_charge_latches_until_off_peak_end() -> None:
    source = COORDINATOR.read_text()

    assert "leaving the battery BMS to hold target until off-peak ends" in source
    assert "await self._overnight_idle_above_target(target_soc, base_attrs)" not in source


def test_overnight_charge_uses_locked_target_not_global_charge_limit() -> None:
    source = COORDINATOR.read_text()

    assert 'success = await self.async_set_battery_control(\n            "Charge",' in source
    assert "charge_soc=target_soc" in source
    assert "charge_soc = self._commanded_max_soc if charge_soc is None else charge_soc" in source
