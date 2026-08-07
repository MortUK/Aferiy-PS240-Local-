"""Regression tests for read-only per-unit and topology diagnostics."""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
import textwrap


ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "aecc_battery"
COORDINATOR = COMPONENT / "coordinator.py"
SENSOR = COMPONENT / "sensor.py"
UTC = timezone.utc


def _subject_class():
    """Build the pure identity helpers without importing Home Assistant."""
    tree = ast.parse(COORDINATOR.read_text())
    coordinator = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "AeccBatteryCoordinator"
    )
    method_names = {
        "_storage_unit_key",
        "storage_identity_key",
        "storage_entry_by_identity",
        "_record_storage_anomaly",
        "_track_storage_identity_order",
        "_record_suspect_hold",
        "_update_storage_discharge_health",
        "storage_discharge_imbalance_active",
        "_safe_float",
    }
    methods = [
        node
        for node in coordinator.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in method_names
    ]
    class_body = "\n\n".join(
        textwrap.indent(ast.unparse(node), "    ") for node in methods
    )
    namespace: dict[str, object] = {}
    exec(
        "from __future__ import annotations\n"
        "from datetime import datetime, timezone\n"
        "UTC = timezone.utc\n"
        "from typing import Any\n"
        "_DISCHARGE_IMBALANCE_MIN_ACTIVE_W = 100.0\n"
        "_DISCHARGE_IMBALANCE_MAX_IDLE_W = 10.0\n"
        "_DISCHARGE_IMBALANCE_SOC_MARGIN = 3.0\n"
        "_DISCHARGE_IMBALANCE_CONFIRM_SECONDS = 300\n"
        f"class Subject:\n{class_body}\n",
        namespace,
    )
    return namespace["Subject"]


def test_identity_diagnostics_follow_a_unit_after_slot_reordering() -> None:
    subject = _subject_class()()
    subject.storage_entries = [
        {"StorageSN": "executor-b", "BatterySoc": 90},
        {"StorageSN": "executor-a", "BatterySoc": 80},
    ]
    subject._storage_identity_order = (
        "StorageSN:executor-a",
        "StorageSN:executor-b",
    )
    subject._storage_anomaly_count = 0
    subject._last_storage_anomaly_kind = None
    subject._last_storage_anomaly_at = None
    subject._last_storage_anomaly_details = {}

    signature = ("StorageSN:executor-b", "StorageSN:executor-a")
    subject._track_storage_identity_order(signature)

    slot, entry = subject.storage_entry_by_identity("StorageSN:executor-a")
    assert slot == 1
    assert entry["BatterySoc"] == 80
    assert subject._storage_anomaly_count == 1
    assert subject._last_storage_anomaly_kind == "unit_order_changed"
    assert subject._last_storage_anomaly_details["changed_slot_count"] == 2
    assert "executor-a" not in repr(subject._last_storage_anomaly_details)


def test_identity_set_change_is_recorded_without_serials() -> None:
    subject = _subject_class()()
    subject._storage_identity_order = (
        "StorageSN:master",
        "StorageSN:executor-a",
    )
    subject._storage_anomaly_count = 0
    subject._last_storage_anomaly_kind = None
    subject._last_storage_anomaly_at = None
    subject._last_storage_anomaly_details = {}

    subject._track_storage_identity_order(("StorageSN:master",))

    assert subject._last_storage_anomaly_kind == "unit_set_changed"
    assert subject._last_storage_anomaly_details == {
        "changed_slot_count": 1,
        "previous_slot_count": 2,
        "current_slot_count": 1,
    }


def test_sustained_high_soc_discharge_imbalance_records_anomaly() -> None:
    subject = _subject_class()()
    subject._commanded_min_soc = 10
    subject._storage_discharge_imbalance_since = None
    subject._storage_discharge_imbalance_active = False
    subject._last_storage_discharge_imbalance_at = None
    subject._last_storage_discharge_recovered_at = None
    subject._storage_discharge_imbalance_details = {}
    subject._storage_anomaly_count = 0
    subject._last_storage_anomaly_kind = None
    subject._last_storage_anomaly_at = None
    subject._last_storage_anomaly_details = {}
    frame = {
        "Storage_list": [
            {"BatterySoc": 70, "BatteryDischargingPower": 2500},
            {"BatterySoc": 100, "BatteryDischargingPower": 0},
            {"BatterySoc": 100, "BatteryDischargingPower": 0},
        ]
    }

    subject._update_storage_discharge_health(frame)
    subject._storage_discharge_imbalance_since = datetime.fromtimestamp(
        subject._storage_discharge_imbalance_since.timestamp() - 301,
        tz=UTC,
    )
    subject._update_storage_discharge_health(frame)

    assert subject.storage_discharge_imbalance_active is True
    assert subject._last_storage_anomaly_kind == "sustained_discharge_imbalance"
    assert subject._last_storage_anomaly_details["non_discharging_unit_count"] == 2


def test_low_soc_reserve_unit_does_not_raise_imbalance() -> None:
    subject = _subject_class()()
    subject._commanded_min_soc = 10
    subject._storage_discharge_imbalance_since = None
    subject._storage_discharge_imbalance_active = False
    subject._last_storage_discharge_imbalance_at = None
    subject._last_storage_discharge_recovered_at = None
    subject._storage_discharge_imbalance_details = {}
    frame = {
        "Storage_list": [
            {"BatterySoc": 70, "BatteryDischargingPower": 2500},
            {"BatterySoc": 12, "BatteryDischargingPower": 0},
        ]
    }

    subject._update_storage_discharge_health(frame)

    assert subject.storage_discharge_imbalance_active is False
    assert subject._storage_discharge_imbalance_since is None


def test_suspect_timestamp_changes_only_for_a_new_episode() -> None:
    subject = _subject_class()()
    subject._last_suspect_reason = "unit DevAddr 2 SOC collapsed"
    subject._last_suspect_at = "original-timestamp"
    subject._last_suspect_outcome = "accepted_after_tolerance"

    subject._record_suspect_hold("unit DevAddr 2 SOC collapsed")

    assert subject._last_suspect_at == "original-timestamp"
    assert subject._last_suspect_outcome == "held"

    subject._last_suspect_outcome = "cleared_by_healthy_frame"
    subject._record_suspect_hold("unit DevAddr 2 SOC collapsed")

    assert subject._last_suspect_at != "original-timestamp"


def test_active_imbalance_keeps_a_stable_diagnostic_snapshot() -> None:
    subject = _subject_class()()
    subject._commanded_min_soc = 10
    subject._storage_discharge_imbalance_since = datetime.fromtimestamp(0, tz=UTC)
    subject._storage_discharge_imbalance_active = True
    subject._last_storage_discharge_imbalance_at = datetime.fromtimestamp(1, tz=UTC)
    subject._last_storage_discharge_recovered_at = None
    subject._storage_discharge_imbalance_details = {"snapshot": "original"}
    subject._storage_anomaly_count = 0
    frame = {
        "Storage_list": [
            {"BatterySoc": 70, "BatteryDischargingPower": 2600},
            {"BatterySoc": 80, "BatteryDischargingPower": 0},
        ]
    }

    subject._update_storage_discharge_health(frame)

    assert subject._storage_discharge_imbalance_details == {"snapshot": "original"}
    assert subject._storage_anomaly_count == 0


def test_raw_status_attributes_do_not_duplicate_soc_or_power_history() -> None:
    tree = ast.parse(SENSOR.read_text())
    status_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "AeccStorageUnitStatusSensor"
    )
    attributes = next(
        node
        for node in status_class.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "extra_state_attributes"
    )
    source = ast.unparse(attributes)

    assert "raw_health_fields" in source
    assert "BatterySoc" not in source
    assert "BatteryDischargingPower" not in source
    assert "individual_discharge_power_w" not in source


def test_sensor_platform_exposes_only_read_only_health_diagnostics() -> None:
    source = SENSOR.read_text()
    health_section = source.split("class AeccStorageIdentitySensor", 1)[1].split(
        "class AeccEnergySensor", 1
    )[0]

    assert "BatteryDischargingPower" in health_section
    assert '"StorageStatus", "status", "deviceStatus"' in health_section
    assert 'markers = ("status", "fault", "error", "alarm", "warn", "protect", "temp")' in health_section
    assert "class AeccStorageTopologyHealthSensor" in health_section
    assert 'return "discharge_imbalance"' in health_section
    assert "send_set" not in health_section
    assert "set_control_parameters" not in health_section
