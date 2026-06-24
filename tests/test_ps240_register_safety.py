"""Regression tests for AFERIY PS240 local-control safety decisions."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONST = ROOT / "custom_components" / "aecc_battery" / "const.py"
COORDINATOR = ROOT / "custom_components" / "aecc_battery" / "coordinator.py"
DIAGNOSTICS = ROOT / "custom_components" / "aecc_battery" / "diagnostics.py"


def _string_constants(source_path: Path) -> dict[str, str]:
    """Return simple module-level string constants from a source file."""
    constants: dict[str, str] = {}
    tree = ast.parse(source_path.read_text())
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        try:
            value = ast.literal_eval(node.value)
        except (TypeError, ValueError):
            continue
        if isinstance(value, str):
            constants[target.id] = value
    return constants


def _literal_or_named_string(node: ast.AST, constants: dict[str, str]) -> str:
    if isinstance(node, ast.Name) and node.id in constants:
        return constants[node.id]
    value = ast.literal_eval(node)
    assert isinstance(value, str)
    return value


def _literal_dict_assigned_in_function(
    source_path: Path,
    function_name: str,
    variable_name: str,
) -> dict[str, str]:
    """Return a literal dict assigned to ``variable_name`` inside a function."""
    tree = ast.parse(source_path.read_text())
    constants = _string_constants(CONST)

    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef) or node.name != function_name:
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Assign):
                continue
            if not any(isinstance(target, ast.Name) and target.id == variable_name for target in child.targets):
                continue
            assert isinstance(child.value, ast.Dict)
            return {
                _literal_or_named_string(key, constants): _literal_or_named_string(value, constants)
                for key, value in zip(child.value.keys, child.value.values)
                if key is not None
            }

    raise AssertionError(f"Could not find {variable_name!r} in {function_name!r}")


def test_ps240_self_gen_restore_uses_known_good_schedule3_pattern() -> None:
    """Self-Gen restore must match the register pattern proven on the PS240."""
    payload = _literal_dict_assigned_in_function(
        COORDINATOR,
        "async_restore_self_consumption",
        "restore_ai_payload",
    )

    assert payload == {
        "3000": "1",
        "3020": "3",
        "3021": "0",
        "3022": "1",
        "3029": "1",
        "3030": "0",
        "3003": "0,00:00,00:00,0,0,0,0,0,0,100,10",
    }


def test_ps240_self_gen_restore_clears_manual_slot_first() -> None:
    """A stale manual slot must be cleared before handing control back to AI."""
    payload = _literal_dict_assigned_in_function(
        COORDINATOR,
        "async_restore_self_consumption",
        "clear_manual_payload",
    )

    assert payload == {
        "3003": "0,00:00,00:00,0,0,0,0,0,0,100,10",
        "3030": "0",
        "3022": "1",
        "3029": "1",
    }


def test_diagnostics_redacts_private_device_details() -> None:
    """Diagnostics must keep serials, host addresses, and credentials private."""
    source = DIAGNOSTICS.read_text()

    for sensitive_key in (
        "host",
        "serial",
        "device_serial",
        "StorageSN",
        "password",
        "token",
        "api_key",
        "email",
        "ssid",
        "mac_address",
        "latitude",
        "longitude",
    ):
        assert repr(sensitive_key) in source or f'"{sensitive_key}"' in source


def test_diagnostics_labels_control_registers_we_care_about() -> None:
    """Register snapshots should label the PS240 control registers by meaning."""
    source = DIAGNOSTICS.read_text()

    for label in (
        "EMS enable (3000)",
        "Control time slot 1 (3003)",
        "Schedule mode (3020)",
        "AI smart charge (3021)",
        "AI smart discharge (3022)",
        "Min SOC (3023)",
        "Max SOC (3024)",
        "Base feed power (3026)",
        "Custom mode (3030)",
        "Max feed power (3039)",
    ):
        assert label in source


def test_control_write_audit_summarises_and_skips_recent_duplicates() -> None:
    """The control path should expose useful diagnostics and avoid rapid repeat writes."""
    source = COORDINATOR.read_text()

    assert "_DUPLICATE_WRITE_SUPPRESS_SECONDS = 10" in source
    assert "skipped_duplicate" in source
    assert "payload_summary" in source
    assert "control_time_1" in source


def test_runtime_preferences_mark_loaded_even_when_empty() -> None:
    """A fresh install should still let entities restore and persist runtime choices."""
    source = COORDINATOR.read_text()

    assert 'if not isinstance(data, dict):\n            self.runtime_preferences_loaded = True' in source
