"""Behavior tests for rejecting bad multi-battery Storage_list snapshots."""

from __future__ import annotations

import ast
from pathlib import Path
import textwrap


ROOT = Path(__file__).resolve().parents[1]
COORDINATOR = ROOT / "custom_components" / "aecc_battery" / "coordinator.py"


def _subject_class():
    """Build the pure topology methods without importing Home Assistant."""
    tree = ast.parse(COORDINATOR.read_text())
    coordinator = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "AeccBatteryCoordinator"
    )
    method_names = {
        "_storage_unit_key",
        "_storage_units_by_key",
        "_storage_unit_label",
        "_frame_suspect_reason",
        "_update_storage_topology_confirmation",
        "_update_storage_topology_health",
        "storage_topology_confirmed",
        "confirmed_storage_slot_count",
        "storage_topology_incomplete",
        "diagnostic_state",
        "_storage_topology_summary",
        "_safe_float",
    }
    methods = [
        node
        for node in coordinator.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in method_names
    ]
    class_body = "\n\n".join(textwrap.indent(ast.unparse(node), "    ") for node in methods)
    namespace: dict[str, object] = {}
    exec(
        "from __future__ import annotations\n"
        "from datetime import datetime, timezone\n"
        "UTC = timezone.utc\n"
        "from typing import Any\n"
        "_SUSPECT_FRAME_TOLERANCE = 3\n"
        "_SOC_COLLAPSE_FLOOR = 5.0\n"
        f"class Subject:\n{class_body}\n",
        namespace,
    )
    return namespace["Subject"]


def _unit(serial: str | None, soc: int) -> dict[str, object]:
    unit: dict[str, object] = {"BatterySoc": soc, "status": 1}
    if serial is not None:
        unit["StorageSN"] = serial
    return unit


def test_partial_and_empty_storage_snapshots_are_rejected() -> None:
    subject = _subject_class()()
    subject._last_good_data = {
        "Storage_list": [_unit("master", 80), _unit("executor", 79)]
    }

    missing_reason = subject._frame_suspect_reason(
        {"Storage_list": [_unit("master", 80)]}
    )
    assert missing_reason == (
        "unit(s) missing from Storage_list: serial-identified unit"
    )
    assert "executor" not in missing_reason
    assert "empty" in subject._frame_suspect_reason({"Storage_list": []})


def test_zero_soc_collapse_is_rejected() -> None:
    subject = _subject_class()()
    subject._last_good_data = {"Storage_list": [_unit("master", 80)]}

    assert "SOC collapsed" in subject._frame_suspect_reason(
        {"Storage_list": [_unit("master", 0)]}
    )


def test_fallback_slot_identity_is_stable_between_frames() -> None:
    subject = _subject_class()()
    subject._last_good_data = {"Storage_list": [_unit(None, 80), _unit(None, 79)]}

    assert subject._frame_suspect_reason(
        {"Storage_list": [_unit(None, 80), _unit(None, 79)]}
    ) is None


def test_topology_requires_four_matching_good_polls() -> None:
    subject = _subject_class()()
    subject._storage_topology_signature = ()
    subject._storage_topology_stable_polls = 0
    subject._expected_storage_topology = ()
    subject.inverter_count = 0
    subject._last_reported_storage_topology = ()
    subject._storage_topology_incomplete_since = None
    subject._last_storage_topology_recovered_at = None
    subject._last_storage_topology_gap_seconds = None
    frame = {"Storage_list": [_unit("master", 80), _unit("executor", 79)]}

    for _ in range(3):
        subject._update_storage_topology_confirmation(frame)
    assert subject.storage_topology_confirmed is False
    assert subject.confirmed_storage_slot_count == 0

    subject._update_storage_topology_confirmation(frame)
    assert subject.storage_topology_confirmed is True
    assert subject.confirmed_storage_slot_count == 2


def test_persistent_reduced_topology_does_not_shrink_known_bank() -> None:
    subject = _subject_class()()
    subject._storage_topology_signature = ("StorageSN:master", "StorageSN:executor")
    subject._storage_topology_stable_polls = 20
    subject._last_reported_storage_topology = subject._storage_topology_signature
    subject._expected_storage_topology = subject._storage_topology_signature
    subject.inverter_count = 0
    subject._storage_topology_incomplete_since = None
    subject._last_storage_topology_recovered_at = None
    subject._last_storage_topology_gap_seconds = None
    reduced_frame = {"Storage_list": [_unit("master", 80)]}

    subject._update_storage_topology_confirmation(reduced_frame, observed_polls=4)

    assert subject.storage_topology_confirmed is True
    assert subject.confirmed_storage_slot_count == 2
    assert subject.storage_topology_incomplete is True
    assert subject._storage_topology_stable_polls == 4


def test_temporary_omission_records_duration_and_recovers() -> None:
    subject = _subject_class()()
    full_topology = ("StorageSN:master", "StorageSN:executor")
    subject._expected_storage_topology = full_topology
    subject.inverter_count = 0
    subject._last_reported_storage_topology = full_topology
    subject._storage_topology_incomplete_since = None
    subject._last_storage_topology_recovered_at = None
    subject._last_storage_topology_gap_seconds = None

    subject._last_reported_storage_topology = ("StorageSN:master",)
    subject._update_storage_topology_health(subject._last_reported_storage_topology)

    assert subject.storage_topology_incomplete is True
    assert subject._storage_topology_incomplete_since is not None

    subject._last_reported_storage_topology = full_topology
    subject._update_storage_topology_health(full_topology)

    assert subject.storage_topology_incomplete is False
    assert subject._storage_topology_incomplete_since is None
    assert subject._last_storage_topology_recovered_at is not None
    assert subject._last_storage_topology_gap_seconds is not None


def test_device_management_count_detects_incomplete_startup_poll() -> None:
    subject = _subject_class()()
    subject._expected_storage_topology = ()
    subject.inverter_count = 3
    subject._last_reported_storage_topology = (
        "StorageSN:master",
        "StorageSN:executor-1",
    )

    assert subject.storage_topology_confirmed is True
    assert subject.confirmed_storage_slot_count == 3
    assert subject.storage_topology_incomplete is True


def test_diagnostic_topology_summary_does_not_expose_serial_values() -> None:
    summary = _subject_class()._storage_topology_summary(
        ("StorageSN:private-master", "deviceSn:private-executor")
    )

    assert summary == {
        "slot_count": 2,
        "identity_sources": ["StorageSN", "deviceSn"],
    }
    assert "private" not in repr(summary)
