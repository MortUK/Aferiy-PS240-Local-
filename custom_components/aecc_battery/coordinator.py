"""DataUpdateCoordinator for the AECC Battery (Local TCP) integration."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .cleaners import CLEANERS, CleanerContext
from .const import (
    DEFAULT_BRAND_PROFILE,
    DEFAULT_BATTERY_CAPACITY_KWH,
    DEFAULT_OFF_PEAK_END,
    DEFAULT_OFF_PEAK_START,
    DEFAULT_TARIFF_PRESET,
    DEFAULT_OVERNIGHT_CHARGE_MODE,
    DOMAIN,
    MAX_BATTERY_POWER_W,
    MAX_REGISTER_POWER_DEFAULT,
    MIN_POLL_INTERVAL,
    MODE_CUSTOM,
    MODE_DISABLED,
    MODE_REGISTERS,
    MODE_SELF_CONSUMPTION,
    REG_BASE_DISCHARGE_ENABLE,
    REG_BASE_DISCHARGE_POWER,
    POLL_INTERVAL,
    REG_AI_SMART_CHARGE,
    REG_AI_SMART_DISC,
    REG_CONTROL_TIME1,
    REG_CUSTOM_MODE,
    REG_EMS_ENABLE,
    REG_MAX_FEED_POWER,
    REG_MAX_SOC,
    REG_MIN_SOC,
    REG_SCHEDULE_MODE,
    REG_SURPLUS_CHARGE_TRIGGER,
    SLOT_DISABLED,
    OVERNIGHT_CHARGE_MODE_DISABLED,
    OVERNIGHT_CHARGE_MODE_MANUAL,
    OVERNIGHT_CHARGE_MODE_SMART,
    TARIFF_PRESETS,
)
from .tcp_client import AeccTcpClient

_LOGGER = logging.getLogger(__name__)
_RUNTIME_CONFIG_STORAGE_VERSION = 1
_OVERNIGHT_ROLLING_RECHECK_MIN_INCREASE_SOC = 2

# ── Unified field mapping ─────────────────────────────────────────────────────
# Maps canonical sensor keys to (source, field_name, scale) tuples.
# Storage_list is tried first (Sunpura), then SSumInfoList (Lunergy fallback).
# Storage_list power values are 10x scaled; SSumInfoList values are in watts.
# ──────────────────────────────────────────────────────────────────────────────

_FIELD_MAP: dict[str, list[tuple[str, str, float]]] = {
    "battery_soc": [
        ("summary", "AverageBatteryAverageSOC", 1.0),
        ("storage", "BatterySoc", 1.0),
    ],
    "average_battery_soc": [
        ("summary", "AverageBatteryAverageSOC", 1.0),
    ],
    "local_battery_soc": [
        ("storage", "BatterySoc", 1.0),
    ],
    "ac_charging_power": [
        ("summary", "TotalACChargePower", 1.0),
        ("storage", "AcChargingPower", 0.1),
    ],
    "total_charge_power": [
        ("summary", "TotalChargePower", 1.0),
    ],
    "battery_discharging_power": [
        ("storage", "BatteryDischargingPower", 0.1),
        ("summary", "TotalBatteryOutputPower", 1.0),
    ],
    "total_battery_output_power": [
        ("summary", "TotalBatteryOutputPower", 1.0),
    ],
    "battery_charging_power": [
        ("storage", "BatteryChargingPower", 0.1),
        ("summary", "TotalACChargePower", 1.0),
    ],
    "pv_power": [
        ("summary", "TotalPVPower", 1.0),
        ("storage", "PvChargingPower", 0.1),
    ],
    "pv_charging_power": [
        ("summary", "TotalPVChargePower", 1.0),
        ("storage", "PvChargingPower", 0.1),
    ],
    "grid_power": [
        ("summary", "MeterTotalActivePower", 1.0),
        ("storage", "AcInActivePower", 0.1),
    ],
    "total_grid_output_power": [
        ("summary", "TotalGridOutputPower", 1.0),
    ],
    "control_enable_status": [
        ("summary", "ControlEnableStatus", 1.0),
    ],
    "grid_export_power": [],  # Derived in sensor from grid_power (positive values only)
    "backup_power": [
        # OffGridLoadPower is reported in watts directly, unlike other storage
        # power fields which are in deciwatts (0.1x scale). Verified against a
        # 2000W heater test on 2026-04-20: battery_discharging_power read
        # ~2040W while backup_power with the 0.1 multiplier read ~193W.
        ("storage", "OffGridLoadPower", 1.0),
        ("summary", "TotalBackUpPower", 1.0),
    ],
    "pv1_power": [
        ("storage", "Pv1Power", 1.0),
    ],
    "pv2_power": [
        ("storage", "Pv2Power", 1.0),
    ],
}


class AeccBatteryCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(
        self,
        hass: HomeAssistant,
        client: AeccTcpClient,
        device_name: str,
        entry_id: str | None = None,
        poll_interval: int = POLL_INTERVAL,
        manufacturer: str = "AECC",
        model: str = "",
        extended_power: bool = False,
        brand_profile: dict[str, Any] | None = None,
        off_peak_start: str = DEFAULT_OFF_PEAK_START,
        off_peak_end: str = DEFAULT_OFF_PEAK_END,
        smart_tariff_preset: str = DEFAULT_TARIFF_PRESET,
        overnight_charging_mode: str = DEFAULT_OVERNIGHT_CHARGE_MODE,
    ) -> None:
        self.client = client
        self.device_name = device_name
        self._entry_id = entry_id
        self._runtime_store: Store | None = (
            Store(hass, _RUNTIME_CONFIG_STORAGE_VERSION, f"{DOMAIN}_{entry_id}_runtime_config")
            if entry_id
            else None
        )
        self.runtime_preferences_loaded: bool = False
        self._manufacturer = manufacturer
        self._model = model
        self._consecutive_failures: int = 0
        self._last_good_data: dict[str, Any] | None = None
        self._last_good_storage_soc_count: int = 0
        self.last_successful_update: datetime | None = None
        self.last_failed_update: datetime | None = None
        self.last_failure_reason: str | None = None
        self._failure_tolerance: int = 5
        self.device_serial: str | None = None
        self.firmware_version: str | None = None
        self.device_model_code: str | None = None
        self.device_hardware_version: str | None = None
        self.device_clock: str | None = None
        self.device_sdk_version: str | None = None
        self.wifi_rssi_dbm: int | None = None
        self.topology_device_count: int = 0
        self.topology_reported_count: int = 0
        self.inverter_count: int = 0
        self.system_topology: list[dict[str, Any]] = []
        self.master_serial: str | None = None
        self.executor_serials: list[str] = []
        self.meter_serial: str | None = None
        self.meter_name: str | None = None
        self._commanded_power: int = 0
        self._commanded_direction: str = "Idle"
        self._commanded_work_mode: str | None = None
        self.commanded_operating_mode: str | None = None
        self.commanded_charge_power: int = 800
        self.commanded_discharge_power: int = 800
        self.commanded_feed_power: int = 0
        self.battery_capacity_kwh: float = DEFAULT_BATTERY_CAPACITY_KWH
        self.smart_overnight_buffer_soc: float = 3.0
        self.energy_dashboard_manager: Any | None = None
        self.manual_overnight_target_soc: int = 80
        self.overnight_charging_mode: str = overnight_charging_mode
        self.off_peak_start: str = off_peak_start
        self.off_peak_end: str = off_peak_end
        self.manual_off_peak_start: str = off_peak_start
        self.manual_off_peak_end: str = off_peak_end
        self.smart_tariff_preset: str = smart_tariff_preset
        self._overnight_task: asyncio.Task | None = None
        self._overnight_status: dict[str, Any] = {
            "state": "Off",
            "mode": overnight_charging_mode,
            "reason": "Automatic overnight charging is off.",
        }
        self._smart_history_status: dict[str, Any] = {
            "recorder_history_status": "warming",
            "recorder_history_valid_days": 0,
            "recorder_history_lookback_days": 30,
            "recorder_retention_days": 35,
            "reason": "Usage history is warming up.",
        }
        self._overnight_last_action: str | None = None
        self._overnight_last_action_at: datetime | None = None
        self._overnight_last_window_key: str | None = None
        self._overnight_last_restored_window_key: str | None = None
        self._overnight_scheduler_started_charge: bool = False
        self._overnight_charge_confirm_count: int = 0
        self._overnight_last_trusted_soc: float | None = None
        self._overnight_last_trusted_soc_at: datetime | None = None
        self._overnight_locked_target_soc: int | None = None
        self._overnight_locked_target_source: str | None = None
        self._overnight_locked_target_at: datetime | None = None
        self._overnight_locked_target_window_key: str | None = None
        self._overnight_locked_target_context: dict[str, Any] = {}
        self._overnight_locked_target_charged_during_window: bool = False
        self._overnight_locked_target_reached_threshold: bool = False
        self._overnight_locked_target_recheck_count: int = 0
        self._overnight_locked_target_last_recheck_at: datetime | None = None
        self._overnight_last_completed_plan: dict[str, Any] | None = None
        self._overnight_morning_accuracy: dict[str, Any] = {}
        self._overnight_accuracy_recorded_window_key: str | None = None
        self._overnight_accuracy_status: dict[str, Any] = {
            "state": None,
            "result": "waiting",
            "reason": "Waiting for a completed SMART overnight cycle.",
        }
        self.solar_unavailable_override: bool = False
        self._commanded_min_soc: int = 10
        self._commanded_max_soc: int = 100
        self.extended_power: bool = extended_power
        self.max_register_power: int = MAX_BATTERY_POWER_W if extended_power else MAX_REGISTER_POWER_DEFAULT
        self.initial_min_soc: int | None = None
        self.initial_max_soc: int | None = None
        self.initial_surplus_charge_trigger: int | None = None
        self.initial_base_discharge_power: int | None = None
        self.initial_max_feed_power: int | None = None
        self.initial_work_mode: str | None = None
        self.initial_power: int | None = None
        # Per-brand cleaning profile (thresholds for the physics-aware
        # cleaners). Defaults to the conservative "Other" profile so a
        # missing/typo'd brand still gets light protection without rejecting
        # legitimate readings.
        self.brand_profile: dict[str, Any] = dict(brand_profile or DEFAULT_BRAND_PROFILE)
        # State for the cleaner pipeline, last accepted (cleaned) value and
        # timestamp per canonical key. Used for rate-of-change checks and
        # for the hybrid hold-then-unavailable behavior in AeccSensor.
        self._cleaner_last_accepted: dict[str, float] = {}
        self._cleaner_last_accepted_at: dict[str, float] = {}
        # Rolling audit trail of recent control writes. Surfaced through
        # diagnostics so we can correlate user-reported misbehaviour with
        # the exact register payloads sent and the post-write verify
        # results from the device.
        self._write_history: deque[dict[str, Any]] = deque(maxlen=20)
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{device_name}",
            update_interval=timedelta(seconds=max(poll_interval, MIN_POLL_INTERVAL)),
        )

    async def _async_setup(self) -> None:
        await self.client.async_connect()
        await self.async_load_runtime_preferences()

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            raw = await self.client.get_energy_parameters()
        except Exception as exc:
            self._consecutive_failures += 1
            self.last_failed_update = datetime.now(UTC)
            self.last_failure_reason = str(exc)
            if self._consecutive_failures <= self._failure_tolerance and self._last_good_data is not None:
                _LOGGER.debug(
                    "Poll failed (%d/%d) - keeping last known data: %s",
                    self._consecutive_failures,
                    self._failure_tolerance,
                    exc,
                )
                return self._last_good_data
            raise UpdateFailed(f"Poll failed for {self.client.host}:{self.client.port}: {exc}") from exc

        valid = raw is not None and (raw.get("Storage_list") or raw.get("SSumInfoList"))
        invalid_reason = "missing Storage_list/SSumInfoList"
        if valid:
            valid, invalid_reason = self._storage_soc_snapshot_valid(raw)

        if not valid:
            self._consecutive_failures += 1
            self.last_failed_update = datetime.now(UTC)
            self.last_failure_reason = invalid_reason
            if self._consecutive_failures in (1, self._failure_tolerance):
                try:
                    await self.client.async_reconnect()
                except (TimeoutError, OSError, ConnectionError) as exc:
                    _LOGGER.debug("Reconnect after invalid poll response failed: %s", exc)
            if self._consecutive_failures == 1:
                _LOGGER.warning(
                    "Poll response missing expected data (Storage_list/SSumInfoList). "
                    "Reason: %s. Raw response keys: %s, raw (truncated): %.500s",
                    invalid_reason,
                    list(raw.keys()) if isinstance(raw, dict) else type(raw).__name__,
                    raw,
                )
            if self._consecutive_failures <= self._failure_tolerance and self._last_good_data is not None:
                _LOGGER.debug(
                    "Incomplete/missing poll response (%d/%d) - keeping last known data",
                    self._consecutive_failures,
                    self._failure_tolerance,
                )
                return self._last_good_data
            reason = (
                f"No valid response from {self.client.host}:{self.client.port} "
                f"after {self._consecutive_failures} consecutive failures"
            )
            self.last_failure_reason = reason
            raise UpdateFailed(reason)

        self._consecutive_failures = 0
        self.last_successful_update = datetime.now(UTC)
        self.last_failure_reason = None
        self._last_good_storage_soc_count = self._storage_soc_count(raw)
        self._last_good_data = raw
        self._schedule_overnight_evaluation()
        return raw

    async def async_load_runtime_preferences(self) -> None:
        """Restore user SMART/config selections from HA storage before first poll."""
        if self._runtime_store is None:
            return
        try:
            data = await self._runtime_store.async_load()
        except (OSError, ValueError) as exc:
            _LOGGER.warning("Could not load AECC runtime config: %s", exc)
            return
        if not isinstance(data, dict):
            return
        self.runtime_preferences_loaded = True

        capacity = self._safe_float(data.get("battery_capacity_kwh"))
        if capacity is not None and capacity > 0:
            self.battery_capacity_kwh = capacity

        overnight_buffer = self._safe_float(data.get("smart_overnight_buffer_soc"))
        if overnight_buffer is not None:
            self.smart_overnight_buffer_soc = max(0.0, min(20.0, overnight_buffer))

        manual_target = self._safe_int(data.get("manual_overnight_target_soc"))
        if manual_target is not None:
            self.manual_overnight_target_soc = max(10, min(100, manual_target))

        mode = data.get("overnight_charging_mode")
        if mode in (
            OVERNIGHT_CHARGE_MODE_DISABLED,
            OVERNIGHT_CHARGE_MODE_SMART,
            OVERNIGHT_CHARGE_MODE_MANUAL,
        ):
            self.overnight_charging_mode = mode
            self._overnight_status["mode"] = mode

        preset = data.get("smart_tariff_preset")
        if preset in TARIFF_PRESETS:
            self.smart_tariff_preset = preset

        self.manual_off_peak_start = self._normalise_hhmm(
            data.get("manual_off_peak_start", self.manual_off_peak_start),
            self.manual_off_peak_start,
        )
        self.manual_off_peak_end = self._normalise_hhmm(
            data.get("manual_off_peak_end", self.manual_off_peak_end),
            self.manual_off_peak_end,
        )
        self.off_peak_start = self._normalise_hhmm(
            data.get("off_peak_start", self.off_peak_start),
            self.off_peak_start,
        )
        self.off_peak_end = self._normalise_hhmm(
            data.get("off_peak_end", self.off_peak_end),
            self.off_peak_end,
        )

        self.solar_unavailable_override = bool(data.get("solar_unavailable_override", False))
        if self.overnight_charging_mode == OVERNIGHT_CHARGE_MODE_DISABLED:
            self._set_overnight_off_status()

    async def async_save_runtime_preferences(self, **updates: Any) -> None:
        """Persist user SMART/config selections without reloading the integration."""
        if self._runtime_store is None:
            return
        data = {
            "battery_capacity_kwh": round(float(self.battery_capacity_kwh), 3),
            "smart_overnight_buffer_soc": round(float(self.smart_overnight_buffer_soc), 1),
            "manual_overnight_target_soc": int(self.manual_overnight_target_soc),
            "overnight_charging_mode": self.overnight_charging_mode,
            "smart_tariff_preset": self.smart_tariff_preset,
            "off_peak_start": self.off_peak_start,
            "off_peak_end": self.off_peak_end,
            "manual_off_peak_start": self.manual_off_peak_start,
            "manual_off_peak_end": self.manual_off_peak_end,
            "solar_unavailable_override": bool(self.solar_unavailable_override),
        }
        data.update(updates)
        try:
            await self._runtime_store.async_save(data)
        except OSError as exc:
            _LOGGER.warning("Could not save AECC runtime config: %s", exc)

    # ── Public access to commanded state (used by entity platforms) ──────────

    @property
    def commanded_power(self) -> int:
        return self._commanded_power

    @commanded_power.setter
    def commanded_power(self, value: int) -> None:
        self._commanded_power = value

    @property
    def commanded_direction(self) -> str:
        return self._commanded_direction

    @commanded_direction.setter
    def commanded_direction(self, value: str) -> None:
        self._commanded_direction = value

    @property
    def commanded_work_mode(self) -> str | None:
        return self._commanded_work_mode

    @commanded_work_mode.setter
    def commanded_work_mode(self, value: str | None) -> None:
        self._commanded_work_mode = value

    @property
    def device_info(self) -> DeviceInfo:
        identifier = self.device_serial or f"{self.client.host}:{self.client.port}"
        return DeviceInfo(
            identifiers={(DOMAIN, identifier)},
            name=self.device_name,
            manufacturer="Richard Owen",
            model=self._model or None,
            sw_version=self.firmware_version,
            configuration_url="https://github.com/MortUK/Aferiy-PS240-Local-",
        )

    @property
    def storage(self) -> dict[str, Any]:
        if not self.data:
            return {}
        return self.storage_entries[0] if self.storage_entries else {}

    @property
    def storage_entries(self) -> list[dict[str, Any]]:
        if not self.data:
            return []
        entries = self.data.get("Storage_list") or []
        return [entry for entry in entries if isinstance(entry, dict)]

    def _storage_soc_count(self, data: dict[str, Any] | None) -> int:
        """Count plausible per-storage SOC entries in a raw poll response."""
        if not data:
            return 0
        entries = data.get("Storage_list") or []
        count = 0
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if self._safe_float(entry.get("BatterySoc")) is not None:
                count += 1
        return count

    def _storage_soc_snapshot_valid(self, data: dict[str, Any] | None) -> tuple[bool, str]:
        """Reject partial multi-battery snapshots before they reach HA entities.

        The PS240 local TCP stream can briefly report a truncated/garbled
        ``Storage_list`` while the cloud app remains healthy. Publishing that
        partial list makes Battery N SOC and the system average jump to
        impossible values, so keep the last good poll instead.
        """
        if not isinstance(data, dict):
            return False, "poll response is not a mapping"

        entries = data.get("Storage_list") or []
        if not entries:
            return True, "no Storage_list to validate"

        soc_values: list[float] = []
        zero_soc_online = False
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            soc = self._safe_float(entry.get("BatterySoc"))
            if soc is None:
                continue
            soc_values.append(soc)
            status = self._safe_int(entry.get("status") or entry.get("deviceStatus"))
            if soc == 0 and status != 0 and self._last_good_data is not None:
                zero_soc_online = True

        count = len(soc_values)
        if (
            self._last_good_storage_soc_count > 1
            and count
            and count < self._last_good_storage_soc_count
        ):
            return (
                False,
                f"partial Storage_list SOC snapshot ({count}/{self._last_good_storage_soc_count})",
            )

        if zero_soc_online:
            return False, "online battery reported 0% SOC in Storage_list"

        return True, "Storage_list SOC snapshot accepted"

    def device_role_for_serial(self, serial: Any) -> str | None:
        """Return the topology role reported for a storage serial."""
        clean_serial = str(serial or "").strip()
        if not clean_serial:
            return None
        for device in self.system_topology:
            if device.get("serial") == clean_serial:
                return str(device.get("role") or "") or None
        return None

    @property
    def summary(self) -> dict[str, Any]:
        return self.data.get("SSumInfoList", {}) if self.data else {}

    _STORAGE_POWER_KEYS = {
        "PvChargingPower",
        "AcChargingPower",
        "BatteryDischargingPower",
        "AcInActivePower",
        "OffGridLoadPower",
        "BatteryChargingPower",
        "Pv1Power",
        "Pv2Power",
        "Pv3Power",
        "Pv4Power",
    }

    def storage_val(self, key: str, default: Any = None) -> Any:
        val = self.storage.get(key, default)
        if val is None:
            return default
        if key in self._STORAGE_POWER_KEYS:
            try:
                return round(float(val) / 10, 1)
            except (TypeError, ValueError):
                return val
        return val

    def storage_entry_val(self, index: int, key: str, default: Any = None) -> Any:
        try:
            entry = self.storage_entries[index]
        except IndexError:
            return default
        val = entry.get(key, default)
        if val is None:
            return default
        if key in self._STORAGE_POWER_KEYS:
            try:
                return round(float(val) / 10, 1)
            except (TypeError, ValueError):
                return val
        return val

    def summary_val(self, key: str, default: Any = None) -> Any:
        return self.summary.get(key, default)

    def _scaled_field_value(self, source: str, field: str, scale: float) -> float | None:
        container = self.storage if source == "storage" else self.summary
        val = container.get(field)
        if val is None:
            return None
        try:
            return round(float(val) * scale, 1)
        except (TypeError, ValueError):
            return None

    def _first_field_value(self, fields: list[tuple[str, str, float]]) -> float | None:
        for source, field, scale in fields:
            val = self._scaled_field_value(source, field, scale)
            if val is not None:
                return val
        return None

    def _derived_ac_charging_power(self) -> float | None:
        """Return AC charging power with stale summary values suppressed.

        Some AECC firmwares leave ``TotalACChargePower`` at a small non-zero
        value after overnight charging has ended. When live PV already explains
        the observed battery charging, treating that stale value as real AC
        charge pollutes house-demand and energy estimates.
        """
        raw_ac = self._first_field_value(_FIELD_MAP["ac_charging_power"])
        total_charge = self._first_field_value(_FIELD_MAP["total_charge_power"])
        pv_power = self._first_field_value(_FIELD_MAP["pv_power"])
        pv_charge = self._first_field_value(_FIELD_MAP["pv_charging_power"])
        pv_available = max(pv_power or 0.0, pv_charge or 0.0)

        if total_charge is not None and total_charge <= 0:
            return 0.0

        if raw_ac is not None and raw_ac <= 0:
            return 0.0

        if total_charge is None:
            return raw_ac

        # If PV is already greater than, or close to, the total battery charge,
        # any small AC-charge value is almost certainly stale.
        margin_w = 25.0
        if pv_available > 0 and total_charge <= pv_available + margin_w:
            return 0.0

        pv_gap = max(total_charge - pv_available, 0.0)
        if raw_ac is None:
            return round(pv_gap, 1) if pv_gap > margin_w else 0.0

        return round(min(raw_ac, pv_gap), 1) if pv_gap > margin_w else 0.0

    def _wall_power_signal_w(self) -> float | None:
        """Best-effort wall-side power magnitude for cleaner physics checks.

        Returns a signed value: positive when the battery is charging,
        negative when discharging. None when neither AECC source has the
        data (cleaners then skip checks that depend on observable flow).

        Reads raw fields directly to avoid triggering nested cleaner calls
        from get_value, this is the activity signal cleaners depend on,
        not a published sensor value.
        """
        for field, scale in (
            ("AcChargingPower", 0.1),
            ("BatteryChargingPower", 0.1),
        ):
            val = self.storage.get(field)
            if val is not None:
                try:
                    charge = float(val) * scale
                    if charge > 0:
                        return charge
                except (TypeError, ValueError):
                    pass
        for field, scale in (
            ("BatteryDischargingPower", 0.1),
            ("AcChargingPower", 0.1),
        ):
            val = self.storage.get(field)
            if val is not None:
                try:
                    discharge = float(val) * scale
                    if discharge > 0:
                        return -discharge if field == "BatteryDischargingPower" else discharge
                except (TypeError, ValueError):
                    pass
        # Fall back to the summary fields (Lunergy primary).
        ac = self.summary.get("TotalACChargePower")
        out = self.summary.get("TotalBatteryOutputPower")
        try:
            ac_f = float(ac) if ac is not None else 0.0
            out_f = float(out) if out is not None else 0.0
            if ac_f > 0:
                return ac_f
            if out_f > 0:
                return -out_f
            if ac is not None or out is not None:
                return 0.0
        except (TypeError, ValueError):
            pass
        return None

    def get_value(self, canonical_key: str, default: Any = None) -> Any:
        if canonical_key == "ac_charging_power":
            raw_value = self._derived_ac_charging_power()
            return default if raw_value is None else raw_value

        entries = _FIELD_MAP.get(canonical_key)
        if not entries:
            return default
        raw_value: float | None = None
        for source, field, scale in entries:
            raw_value = self._scaled_field_value(source, field, scale)
            if raw_value is not None:
                break
        if raw_value is None:
            return default

        cleaner = CLEANERS.get(canonical_key)
        if cleaner is None:
            return raw_value

        ctx = CleanerContext(
            key=canonical_key,
            raw_value=raw_value,
            last_accepted_value=self._cleaner_last_accepted.get(canonical_key),
            last_accepted_at=self._cleaner_last_accepted_at.get(canonical_key),
            now=time.time(),
            wall_power_w=self._wall_power_signal_w(),
            profile=self.brand_profile,
        )
        cleaned = cleaner(ctx)
        if cleaned is None:
            _LOGGER.debug(
                "Cleaner rejected %s=%s (last_accepted=%s, wall_power=%s)",
                canonical_key,
                raw_value,
                ctx.last_accepted_value,
                ctx.wall_power_w,
            )
            return None
        # Record the accepted value/timestamp so the next call has fresh
        # state for rate-of-change checks. Only updates on accept.
        self._cleaner_last_accepted[canonical_key] = cleaned
        self._cleaner_last_accepted_at[canonical_key] = ctx.now
        return cleaned

    def cleaner_last_accepted_at(self, canonical_key: str) -> float | None:
        """Last epoch-second timestamp when this key passed the cleaner.

        AeccSensor uses this for the hybrid hold-then-unavailable behavior:
        once readings have been rejected for longer than the brand's
        ``hold_last_value_seconds``, the entity goes unavailable instead
        of indefinitely showing a stale value.
        """
        return self._cleaner_last_accepted_at.get(canonical_key)

    @property
    def overnight_charging_status(self) -> dict[str, Any]:
        """Current local overnight charging scheduler status."""
        return dict(self._overnight_status)

    @property
    def smart_history_status(self) -> dict[str, Any]:
        """Current usage-history quality used by the SMART recommendation."""
        return dict(self._smart_history_status)

    @property
    def overnight_accuracy_status(self) -> dict[str, Any]:
        """Last completed SMART overnight charge accuracy review."""
        return dict(self._overnight_accuracy_status)

    @property
    def morning_accuracy_status(self) -> dict[str, Any]:
        """Current or last SMART morning bridge accuracy review."""
        if self._overnight_morning_accuracy:
            return dict(self._overnight_morning_accuracy)
        return {
            "state": None,
            "result": "waiting",
            "reason": "Waiting for a completed SMART morning bridge calculation.",
        }

    def _set_overnight_off_status(self) -> None:
        """Make the overnight status reflect a disabled scheduler immediately."""
        self._overnight_status = {
            "state": "Off",
            "mode": OVERNIGHT_CHARGE_MODE_DISABLED,
            "reason": "Automatic overnight charging is off.",
            "updated_at": datetime.now(UTC).isoformat(),
        }

    def set_smart_history_status(self, attrs: dict[str, Any]) -> None:
        """Expose recorder-history quality for diagnostics and dashboards."""
        self._smart_history_status = dict(attrs)

    def set_overnight_charging_mode(self, mode: str) -> None:
        """Store the automatic overnight charging mode locally."""
        if mode not in (
            OVERNIGHT_CHARGE_MODE_DISABLED,
            OVERNIGHT_CHARGE_MODE_SMART,
            OVERNIGHT_CHARGE_MODE_MANUAL,
        ):
            mode = OVERNIGHT_CHARGE_MODE_DISABLED
        self.overnight_charging_mode = mode
        if mode == OVERNIGHT_CHARGE_MODE_DISABLED:
            self._clear_overnight_locked_target()
            self._set_overnight_off_status()
        self._schedule_overnight_evaluation()

    def set_off_peak_window(self, start: str, end: str) -> None:
        """Update the tariff window used by the local overnight scheduler."""
        self.off_peak_start = self._normalise_hhmm(start, DEFAULT_OFF_PEAK_START)
        self.off_peak_end = self._normalise_hhmm(end, DEFAULT_OFF_PEAK_END)
        self._schedule_overnight_evaluation()

    def set_smart_tariff_preset(self, preset: str) -> None:
        """Set the local SMART tariff preset without reloading the config entry."""
        if preset not in TARIFF_PRESETS:
            preset = DEFAULT_TARIFF_PRESET
        self.smart_tariff_preset = preset
        if preset == "custom":
            self.set_off_peak_window(self.manual_off_peak_start, self.manual_off_peak_end)
            return
        start, end = TARIFF_PRESETS.get(
            preset,
            (DEFAULT_OFF_PEAK_START, DEFAULT_OFF_PEAK_END),
        )
        self.manual_off_peak_start = self._normalise_hhmm(start, DEFAULT_OFF_PEAK_START)
        self.manual_off_peak_end = self._normalise_hhmm(end, DEFAULT_OFF_PEAK_END)
        self.set_off_peak_window(start, end)

    def set_manual_off_peak_time(self, *, start: str | None = None, end: str | None = None) -> None:
        """Store custom SMART tariff times and select the custom preset."""
        if start is not None:
            self.manual_off_peak_start = self._normalise_hhmm(start, DEFAULT_OFF_PEAK_START)
        if end is not None:
            self.manual_off_peak_end = self._normalise_hhmm(end, DEFAULT_OFF_PEAK_END)
        self.set_smart_tariff_preset("custom")

    def _schedule_overnight_evaluation(self) -> None:
        """Queue one scheduler pass after fresh data without blocking polling."""
        if self._overnight_task is not None and not self._overnight_task.done():
            return
        self._overnight_task = self.hass.async_create_task(
            self.async_evaluate_overnight_charging()
        )

    async def async_evaluate_overnight_charging(self) -> None:
        """Apply the local overnight charge-to-target decision tree."""
        mode = self.overnight_charging_mode
        now = dt_util.now()
        window = self._overnight_window(now)
        live_target_soc, live_target_source = self._overnight_target_soc(mode)
        current_soc = self._safe_float(self.get_value("average_battery_soc"))
        self._track_morning_accuracy(now)
        self._record_overnight_accuracy_if_due(now, window)
        if mode == OVERNIGHT_CHARGE_MODE_DISABLED:
            self._set_overnight_off_status()
            return
        if self._overnight_last_window_key != window["key"]:
            self._overnight_last_window_key = window["key"]
            self._overnight_scheduler_started_charge = False
            self._overnight_last_action = None
            self._reset_overnight_charge_confirmation()
            self._clear_overnight_locked_target()

        target_locked = False
        if (
            mode == OVERNIGHT_CHARGE_MODE_SMART
            and window["effective_start"] <= now < window["effective_end"]
            and live_target_soc is not None
        ):
            if self._overnight_locked_target_window_key != window["key"]:
                self._overnight_locked_target_soc = live_target_soc
                self._overnight_locked_target_source = live_target_source
                self._overnight_locked_target_at = datetime.now(UTC)
                self._overnight_locked_target_window_key = window["key"]
                self._overnight_locked_target_charged_during_window = False
                self._overnight_locked_target_reached_threshold = bool(
                    current_soc is not None and current_soc <= live_target_soc
                )
                lock_context = self._snapshot_overnight_target_context()
                lock_context["soc_at_target_lock"] = (
                    round(current_soc, 1) if current_soc is not None else None
                )
                lock_context["started_above_target"] = bool(
                    current_soc is not None and current_soc > live_target_soc
                )
                self._overnight_locked_target_context = lock_context
            target_locked = True

        if (
            mode == OVERNIGHT_CHARGE_MODE_SMART
            and self._overnight_locked_target_window_key == window["key"]
            and self._overnight_locked_target_soc is not None
        ):
            self._maybe_roll_overnight_target(
                live_target_soc,
                live_target_source,
                current_soc,
            )
            target_soc = self._overnight_locked_target_soc
            target_source = self._overnight_locked_target_source or live_target_source
        else:
            target_soc = live_target_soc
            target_source = live_target_source

        if (
            current_soc is not None
            and target_soc is not None
            and self._overnight_locked_target_window_key == window["key"]
            and current_soc <= target_soc
        ):
            self._overnight_locked_target_reached_threshold = True

        base_attrs = {
            "mode": mode,
            "target_source": target_source,
            "target_soc": target_soc,
            "live_target_source": live_target_source,
            "live_target_soc": live_target_soc,
            "target_locked": target_locked,
            "locked_target_soc": (
                self._overnight_locked_target_soc
                if self._overnight_locked_target_window_key == window["key"]
                else None
            ),
            "locked_target_source": (
                self._overnight_locked_target_source
                if self._overnight_locked_target_window_key == window["key"]
                else None
            ),
            "locked_target_at": (
                self._overnight_locked_target_at.isoformat()
                if self._overnight_locked_target_window_key == window["key"]
                and self._overnight_locked_target_at
                else None
            ),
            "rolling_recheck_min_increase_soc": _OVERNIGHT_ROLLING_RECHECK_MIN_INCREASE_SOC,
            "rolling_recheck_count": self._overnight_locked_target_recheck_count
            if self._overnight_locked_target_window_key == window["key"]
            else 0,
            "rolling_recheck_last_at": (
                self._overnight_locked_target_last_recheck_at.isoformat()
                if self._overnight_locked_target_window_key == window["key"]
                and self._overnight_locked_target_last_recheck_at
                else None
            ),
            "current_soc": round(current_soc, 1) if current_soc is not None else None,
            "off_peak_start": self.off_peak_start,
            "off_peak_end": self.off_peak_end,
            "effective_start": window["effective_start"].isoformat(),
            "effective_end": window["effective_end"].isoformat(),
            "window_key": window["key"],
            "start_delay_minutes": 1,
            "end_early_minutes": 5,
            "last_action": self._overnight_last_action,
            "last_action_at": self._overnight_last_action_at.isoformat()
            if self._overnight_last_action_at
            else None,
        }

        if target_soc is None:
            self._reset_overnight_charge_confirmation()
            self._set_overnight_status(
                "Waiting for target",
                "The smart target sensor is not available yet.",
                base_attrs,
            )
            return

        if current_soc is None:
            self._reset_overnight_charge_confirmation()
            self._set_overnight_status(
                "Waiting for SOC",
                "System Average Battery SOC is not available yet.",
                base_attrs,
            )
            return

        if now < window["effective_start"]:
            self._reset_overnight_charge_confirmation()
            if self._overnight_scheduler_started_charge:
                await self._overnight_restore(
                    self._overnight_last_window_key or window["key"],
                    "Restoring Self-Gen",
                    "The previous overnight charge window has ended; restoring Self-Gen/Zero Export.",
                    base_attrs,
                )
                return
            self._set_overnight_status(
                "Waiting for off-peak",
                "Waiting until 1 minute after the cheap-rate window starts.",
                base_attrs,
            )
            return

        if window["effective_start"] <= now < window["effective_end"]:
            if current_soc <= target_soc:
                is_new_charge_start = not (
                    self._overnight_scheduler_started_charge
                    or self._overnight_last_action == "charge"
                )
                if is_new_charge_start and not self._overnight_charge_start_confirmed(
                    current_soc, target_soc
                ):
                    confirm_attrs = {
                        **base_attrs,
                        "charge_confirm_count": self._overnight_charge_confirm_count,
                        "charge_confirm_required": 2,
                        "last_trusted_soc": self._overnight_last_trusted_soc,
                    }
                    self._set_overnight_status(
                        "Monitoring target",
                        "SOC is at or below target; waiting for one more stable reading before charging.",
                        confirm_attrs,
                    )
                    return
                await self._overnight_charge_to_target(target_soc, base_attrs)
            elif self._overnight_scheduler_started_charge:
                self._remember_overnight_trusted_soc(current_soc)
                self._reset_overnight_charge_confirmation()
                self._set_overnight_status(
                    "Charging to target",
                    "Charge command has already been sent; leaving the battery BMS to hold target until off-peak ends.",
                    {
                        **base_attrs,
                        "last_action": self._overnight_last_action,
                        "last_action_at": self._overnight_last_action_at.isoformat()
                        if self._overnight_last_action_at
                        else None,
                    },
                )
            else:
                self._remember_overnight_trusted_soc(current_soc)
                self._reset_overnight_charge_confirmation()
                self._set_overnight_status(
                    "Monitoring target",
                    "SOC is above target; waiting in case it falls during off-peak.",
                    base_attrs,
                )
            return

        if window["effective_end"] <= now < window["end"]:
            self._reset_overnight_charge_confirmation()
            await self._overnight_restore(
                window["key"],
                "Ending early",
                "Restoring Self-Gen/Zero Export 5 minutes before off-peak ends.",
                base_attrs,
            )
            return

        if (
            now >= window["end"]
            and self._overnight_scheduler_started_charge
            and self._overnight_last_restored_window_key != window["key"]
        ):
            await self._overnight_restore(
                window["key"],
                "Restoring Self-Gen",
                "Off-peak has ended; restoring Self-Gen/Zero Export.",
                base_attrs,
            )
            return

        self._set_overnight_status(
            "Outside off-peak",
            "Automatic overnight charging is waiting for the next cheap-rate window.",
            base_attrs,
        )

    def _reset_overnight_charge_confirmation(self) -> None:
        """Clear the short debounce used before starting overnight charge."""
        self._overnight_charge_confirm_count = 0

    def _remember_overnight_trusted_soc(self, current_soc: float) -> None:
        """Remember a stable SOC sample for overnight charge sanity checks."""
        self._overnight_last_trusted_soc = round(current_soc, 1)
        self._overnight_last_trusted_soc_at = datetime.now(UTC)

    def _overnight_charge_start_confirmed(
        self,
        current_soc: float,
        target_soc: int,
    ) -> bool:
        """Require stable SOC readings before starting automatic overnight charge."""
        now = datetime.now(UTC)
        last_soc = self._overnight_last_trusted_soc
        last_soc_at = self._overnight_last_trusted_soc_at
        if last_soc is not None and last_soc_at is not None:
            age_seconds = (now - last_soc_at).total_seconds()
            sudden_drop = last_soc - current_soc
            if age_seconds <= 180 and sudden_drop > 5:
                self._reset_overnight_charge_confirmation()
                return False

        self._remember_overnight_trusted_soc(current_soc)
        self._overnight_charge_confirm_count += 1
        return self._overnight_charge_confirm_count >= 2

    async def _overnight_charge_to_target(
        self,
        target_soc: int,
        base_attrs: dict[str, Any],
    ) -> None:
        if self._overnight_last_action == "charge":
            self._set_overnight_status(
                "Charging to target",
                f"Charging until System Average SOC is above {target_soc}%.",
                base_attrs,
            )
            return

        power = int(getattr(self, "commanded_charge_power", 800) or 800)
        success = await self.async_set_battery_control("Charge", power)
        if success:
            self._overnight_scheduler_started_charge = True
            self._overnight_locked_target_charged_during_window = True
            self._overnight_last_action = "charge"
            self._overnight_last_action_at = datetime.now(UTC)
            self.commanded_operating_mode = "Charge"
            self._set_overnight_status(
                "Charging to target",
                f"Started local charge at {power} W per unit until {target_soc}% SOC.",
                {
                    **base_attrs,
                    "charge_power_w_per_unit": power,
                    "last_action": self._overnight_last_action,
                    "last_action_at": self._overnight_last_action_at.isoformat(),
                },
            )
        else:
            self._set_overnight_status(
                "Charge command not confirmed",
                "The battery did not confirm the local charge command; retrying on the next update.",
                {**base_attrs, "charge_power_w_per_unit": power},
            )

    async def _overnight_idle_above_target(
        self,
        target_soc: int,
        base_attrs: dict[str, Any],
    ) -> None:
        if self._overnight_last_action == "idle":
            self._set_overnight_status(
                "Target reached",
                f"SOC is above {target_soc}%; holding idle until the window ends.",
                {
                    **base_attrs,
                    "last_action": self._overnight_last_action,
                    "last_action_at": self._overnight_last_action_at.isoformat(),
                },
            )
            return

        success = await self.async_set_battery_control("Idle", 0)
        if success:
            self._overnight_last_action = "idle"
            self._overnight_last_action_at = datetime.now(UTC)
            self.commanded_operating_mode = "Idle"
            self._set_overnight_status(
                "Target reached",
                f"SOC is above {target_soc}%; holding idle until the window ends.",
                base_attrs,
            )
        else:
            self._set_overnight_status(
                "Idle command failed",
                "The battery did not confirm the local idle command.",
                base_attrs,
            )

    async def _overnight_restore(
        self,
        window_key: str,
        state: str,
        reason: str,
        base_attrs: dict[str, Any],
    ) -> None:
        if self._overnight_last_restored_window_key == window_key:
            self._set_overnight_status(state, reason, base_attrs)
            return

        success = await self.async_restore_self_consumption()
        if success:
            solar_unavailable_reset = bool(self.solar_unavailable_override)
            self.solar_unavailable_override = False
            self._overnight_last_restored_window_key = window_key
            self._overnight_last_action = "restore_self_gen"
            self._overnight_last_action_at = datetime.now(UTC)
            self._overnight_scheduler_started_charge = False
            self._clear_overnight_locked_target()
            self._set_overnight_status(
                state,
                reason,
                {
                    **base_attrs,
                    "last_action": self._overnight_last_action,
                    "last_action_at": self._overnight_last_action_at.isoformat(),
                    "solar_unavailable_reset": solar_unavailable_reset,
                },
            )
        else:
            self._set_overnight_status(
                "Restore failed",
                "The battery did not confirm the Self-Gen/Zero Export restore command.",
                base_attrs,
            )

    def _clear_overnight_locked_target(self) -> None:
        self._remember_overnight_plan()
        self._overnight_locked_target_soc = None
        self._overnight_locked_target_source = None
        self._overnight_locked_target_at = None
        self._overnight_locked_target_window_key = None
        self._overnight_locked_target_context = {}
        self._overnight_locked_target_charged_during_window = False
        self._overnight_locked_target_reached_threshold = False
        self._overnight_locked_target_recheck_count = 0
        self._overnight_locked_target_last_recheck_at = None

    def _remember_overnight_plan(self) -> None:
        if (
            self._overnight_locked_target_soc is None
            or self._overnight_locked_target_window_key is None
        ):
            return
        self._overnight_last_completed_plan = {
            "window_key": self._overnight_locked_target_window_key,
            "target_soc": self._overnight_locked_target_soc,
            "target_source": self._overnight_locked_target_source,
            "locked_at": self._overnight_locked_target_at.isoformat()
            if self._overnight_locked_target_at
            else None,
            "charged_during_window": self._overnight_locked_target_charged_during_window,
            "reached_or_below_target": self._overnight_locked_target_reached_threshold,
            "rolling_recheck_count": self._overnight_locked_target_recheck_count,
            "rolling_recheck_last_at": self._overnight_locked_target_last_recheck_at.isoformat()
            if self._overnight_locked_target_last_recheck_at
            else None,
            **self._overnight_locked_target_context,
        }
        self._ensure_morning_accuracy_tracker(self._overnight_last_completed_plan)

    def _maybe_roll_overnight_target(
        self,
        live_target_soc: int | None,
        live_target_source: str,
        current_soc: float | None,
    ) -> None:
        """Raise the locked SMART target during off-peak if the plan worsens materially."""
        if live_target_soc is None or self._overnight_locked_target_soc is None:
            return

        locked = int(self._overnight_locked_target_soc)
        increase = int(live_target_soc) - locked
        if increase < _OVERNIGHT_ROLLING_RECHECK_MIN_INCREASE_SOC:
            return

        previous_context = dict(self._overnight_locked_target_context)
        new_context = self._snapshot_overnight_target_context()
        new_context["soc_at_target_lock"] = previous_context.get("soc_at_target_lock")
        new_context["started_above_target"] = previous_context.get(
            "started_above_target",
            bool(current_soc is not None and current_soc > live_target_soc),
        )
        new_context["rolling_recheck_previous_target_soc"] = locked
        new_context["rolling_recheck_latest_target_soc"] = int(live_target_soc)
        new_context["rolling_recheck_increase_soc"] = increase
        new_context["rolling_recheck_reason"] = (
            "SMART target increased during off-peak, so the locked target was raised. "
            "Targets are only raised, not lowered, to avoid twitchy behaviour."
        )

        self._overnight_locked_target_soc = int(live_target_soc)
        self._overnight_locked_target_source = live_target_source
        self._overnight_locked_target_context = new_context
        self._overnight_locked_target_recheck_count += 1
        self._overnight_locked_target_last_recheck_at = datetime.now(UTC)
        if current_soc is not None and current_soc <= live_target_soc:
            self._overnight_locked_target_reached_threshold = True

    def _snapshot_overnight_target_context(self) -> dict[str, Any]:
        state = self.hass.states.get(self._recommended_overnight_soc_entity_id())
        if state is None:
            return {}

        attrs = dict(state.attributes)
        breakdown = attrs.get("target_breakdown")
        if not isinstance(breakdown, dict):
            breakdown = {}

        keys = (
            "battery_capacity_kwh",
            "projected_house_demand_kwh",
            "projected_solar_kwh",
            "whole_day_net_shortfall_kwh",
            "pre_sunrise_need_kwh",
            "post_sunset_need_kwh",
            "peak_window_need_kwh",
            "battery_energy_before_buffer_kwh",
            "dynamic_buffer_soc",
            "confidence_mode",
            "estimated_grid_charge_energy_to_target_kwh",
            "useful_solar_start_at",
            "solar_credit_mode",
            "solar_unavailable_override",
        )
        context = {
            key: breakdown.get(key, attrs.get(key))
            for key in keys
            if breakdown.get(key, attrs.get(key)) is not None
        }
        context["why_target"] = attrs.get("why_target")
        context["recommendation_reason"] = attrs.get("recommendation_reason")
        return context

    def _current_overnight_plan(self) -> dict[str, Any] | None:
        if self._overnight_last_completed_plan:
            return self._overnight_last_completed_plan
        if (
            self._overnight_locked_target_soc is None
            or self._overnight_locked_target_window_key is None
        ):
            return None
        return {
            "window_key": self._overnight_locked_target_window_key,
            "target_soc": self._overnight_locked_target_soc,
            "target_source": self._overnight_locked_target_source,
            "locked_at": self._overnight_locked_target_at.isoformat()
            if self._overnight_locked_target_at
            else None,
            "charged_during_window": self._overnight_locked_target_charged_during_window,
            "reached_or_below_target": self._overnight_locked_target_reached_threshold,
            **self._overnight_locked_target_context,
        }

    def _reserve_floor_soc_for_plan(self, plan: dict[str, Any]) -> tuple[int, float, float]:
        minimum_soc = int(self._commanded_min_soc)
        buffer_soc = self._safe_float(plan.get("dynamic_buffer_soc")) or 0.0
        reserve_floor_soc = round(min(100.0, minimum_soc + buffer_soc), 1)
        return minimum_soc, buffer_soc, reserve_floor_soc

    def _plan_datetime(self, value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed

    def _ensure_morning_accuracy_tracker(self, plan: dict[str, Any]) -> None:
        window_key = plan.get("window_key")
        useful_solar_at = self._plan_datetime(plan.get("useful_solar_start_at"))
        if not window_key or useful_solar_at is None:
            return
        if self._overnight_morning_accuracy.get("window_key") == window_key:
            return
        minimum_soc, buffer_soc, reserve_floor_soc = self._reserve_floor_soc_for_plan(plan)
        self._overnight_morning_accuracy = {
            "window_key": window_key,
            "state": None,
            "result": "tracking",
            "minimum_soc": minimum_soc,
            "buffer_soc": buffer_soc,
            "reserve_floor_soc": reserve_floor_soc,
            "soc_at_target_lock": plan.get("soc_at_target_lock"),
            "started_above_target": bool(plan.get("started_above_target")),
            "charged_during_window": bool(plan.get("charged_during_window")),
            "reached_or_below_target": bool(plan.get("reached_or_below_target")),
            "carry_in_not_scored": False,
            "useful_solar_start_at": useful_solar_at.isoformat(),
            "lowest_soc_before_useful_solar": None,
            "lowest_soc_at": None,
            "completed_at": None,
            "reason": "Tracking the lowest SOC before useful solar takes over.",
        }

    def _track_morning_accuracy(self, now: datetime) -> None:
        plan = self._current_overnight_plan()
        if not plan:
            return
        self._ensure_morning_accuracy_tracker(plan)
        tracker = self._overnight_morning_accuracy
        if not tracker or tracker.get("window_key") != plan.get("window_key"):
            return

        useful_solar_at = self._plan_datetime(tracker.get("useful_solar_start_at"))
        if useful_solar_at is None:
            return

        active_window = self._overnight_window(now)
        if plan.get("window_key") == active_window["key"] and now < active_window["end"]:
            return

        current_soc = self._safe_float(self.get_value("average_battery_soc"))
        if current_soc is None:
            return

        if tracker.get("completed_at"):
            return

        lowest_soc = self._safe_float(tracker.get("lowest_soc_before_useful_solar"))
        if now <= useful_solar_at:
            if lowest_soc is None or current_soc < lowest_soc:
                tracker["lowest_soc_before_useful_solar"] = round(current_soc, 1)
                tracker["lowest_soc_at"] = datetime.now(UTC).isoformat()
                self.async_update_listeners()
            return

        if lowest_soc is None:
            lowest_soc = current_soc
            tracker["lowest_soc_before_useful_solar"] = round(current_soc, 1)
            tracker["lowest_soc_at"] = datetime.now(UTC).isoformat()

        reserve_floor_soc = self._safe_float(tracker.get("reserve_floor_soc")) or 0.0
        spare_soc = round(lowest_soc - reserve_floor_soc, 1)
        tolerance_soc = 2.0
        carry_in_not_scored = bool(
            spare_soc > tolerance_soc
            and plan.get("started_above_target")
            and not plan.get("charged_during_window")
            and not plan.get("reached_or_below_target")
        )
        if carry_in_not_scored:
            state = 0.0
            result = "not_scored_started_above_target"
            reason = (
                "Battery stayed above the SMART target overnight and did not need charging; "
                "morning spare SOC is carry-in energy rather than target error."
            )
        elif spare_soc > tolerance_soc:
            state = spare_soc
            result = "too_high"
        elif spare_soc < -tolerance_soc:
            state = spare_soc
            result = "too_low"
        else:
            state = spare_soc
            result = "about_right"
        if not carry_in_not_scored:
            reason = (
                f"Lowest SOC before useful solar was {lowest_soc:.1f}%, "
                f"{spare_soc:+.1f}% versus the {reserve_floor_soc:g}% planned reserve."
            )

        tracker.update(
            {
                "state": state,
                "result": result,
                "spare_soc": spare_soc,
                "tolerance_soc": tolerance_soc,
                "carry_in_not_scored": carry_in_not_scored,
                "completed_at": datetime.now(UTC).isoformat(),
                "reason": reason,
            }
        )
        self.async_update_listeners()

    def _record_overnight_accuracy_if_due(
        self,
        now: datetime,
        window: dict[str, Any],
    ) -> None:
        plan = self._overnight_last_completed_plan
        if (
            not plan
            and self._overnight_locked_target_soc is not None
            and self._overnight_locked_target_window_key is not None
            and self._overnight_locked_target_window_key != window["key"]
        ):
            plan = {
                "window_key": self._overnight_locked_target_window_key,
                "target_soc": self._overnight_locked_target_soc,
                "target_source": self._overnight_locked_target_source,
                "locked_at": self._overnight_locked_target_at.isoformat()
                if self._overnight_locked_target_at
                else None,
                "charged_during_window": self._overnight_locked_target_charged_during_window,
                "reached_or_below_target": self._overnight_locked_target_reached_threshold,
                **self._overnight_locked_target_context,
            }
        if not plan:
            return

        previous_window_key = plan.get("window_key")
        if (
            not previous_window_key
            or previous_window_key == window["key"]
            or self._overnight_accuracy_recorded_window_key == previous_window_key
            or now < window["effective_start"]
        ):
            return

        current_soc = self._safe_float(self.get_value("average_battery_soc"))
        if current_soc is None:
            self._overnight_accuracy_status = {
                "state": None,
                "result": "waiting",
                "reason": "Waiting for System Average Battery SOC to review the last overnight plan.",
                "previous_window_key": previous_window_key,
                "next_window_key": window["key"],
            }
            return

        minimum_soc, buffer_soc, reserve_floor_soc = self._reserve_floor_soc_for_plan(plan)
        spare_soc = round(current_soc - reserve_floor_soc, 1)
        shortfall_kwh = self._safe_float(plan.get("whole_day_net_shortfall_kwh")) or 0.0
        scored = shortfall_kwh > 0.05
        tolerance_soc = 2.0
        carry_in_not_scored = bool(
            scored
            and spare_soc > tolerance_soc
            and plan.get("started_above_target")
            and not plan.get("charged_during_window")
            and not plan.get("reached_or_below_target")
        )
        if not scored:
            accuracy_state = 0.0
            result = "not_scored_solar_surplus"
            reason = (
                "Solar forecast covered projected demand, so end-of-day spare SOC "
                "is not a useful measure of overnight target accuracy."
            )
        elif carry_in_not_scored:
            accuracy_state = 0.0
            scored = False
            result = "not_scored_started_above_target"
            reason = (
                "Battery started off-peak above the SMART target and never reached the target line, "
                "so spare SOC is carry-in energy rather than an overcharge error."
            )
        elif spare_soc > tolerance_soc:
            accuracy_state = spare_soc
            result = "too_high"
            reason = (
                f"Battery had {spare_soc:.1f}% spare above the {reserve_floor_soc:g}% planned reserve "
                "when the next off-peak window started."
            )
        elif spare_soc < -tolerance_soc:
            accuracy_state = spare_soc
            result = "too_low"
            reason = (
                f"Battery was {abs(spare_soc):.1f}% below the {reserve_floor_soc:g}% planned reserve "
                "when the next off-peak window started."
            )
        else:
            accuracy_state = spare_soc
            result = "about_right"
            reason = (
                f"Battery finished within {tolerance_soc:.0f}% of the {reserve_floor_soc:g}% planned reserve "
                "when the next off-peak window started."
            )

        minutes_after_start = round((now - window["effective_start"]).total_seconds() / 60, 1)
        self._overnight_accuracy_recorded_window_key = str(previous_window_key)
        self._overnight_accuracy_status = {
            "state": accuracy_state,
            "result": result,
            "reason": reason,
            "meaning": (
                "On solar-shortfall days, positive means spare SOC above the planned reserve "
                "before the next off-peak; negative means the target was too low. "
                "Solar-surplus days and carry-in nights that never needed charging are reported as 0 "
                "and marked not scored."
            ),
            "scored": scored,
            "carry_in_not_scored": carry_in_not_scored,
            "target_soc": plan.get("target_soc"),
            "target_source": plan.get("target_source"),
            "soc_at_target_lock": plan.get("soc_at_target_lock"),
            "started_above_target": bool(plan.get("started_above_target")),
            "charged_during_window": bool(plan.get("charged_during_window")),
            "reached_or_below_target": bool(plan.get("reached_or_below_target")),
            "minimum_soc": minimum_soc,
            "minimum_soc_source": "Discharge Limit slider / device register 3023",
            "buffer_soc": buffer_soc,
            "reserve_floor_soc": reserve_floor_soc,
            "soc_before_next_off_peak": round(current_soc, 1),
            "spare_soc": spare_soc,
            "whole_day_net_shortfall_kwh": shortfall_kwh,
            "tolerance_soc": tolerance_soc,
            "morning_need_accuracy": dict(self._overnight_morning_accuracy),
            "checked_at": datetime.now(UTC).isoformat(),
            "minutes_after_off_peak_effective_start": minutes_after_start,
            "checked_late": minutes_after_start > 10,
            "previous_window_key": previous_window_key,
            "next_window_key": window["key"],
            "locked_target_at": plan.get("locked_at"),
            "off_peak_start": self.off_peak_start,
            "off_peak_end": self.off_peak_end,
            **{
                key: value
                for key, value in plan.items()
                if key
                not in {
                    "window_key",
                    "target_soc",
                    "target_source",
                    "locked_at",
                }
            },
        }
        self.async_update_listeners()

    def _set_overnight_status(
        self,
        state: str,
        reason: str,
        attrs: dict[str, Any],
    ) -> None:
        self._overnight_status = {
            "state": state,
            "reason": reason,
            **attrs,
            "updated_at": datetime.now(UTC).isoformat(),
        }

    def _overnight_target_soc(self, mode: str) -> tuple[int | None, str]:
        if mode == OVERNIGHT_CHARGE_MODE_MANUAL:
            return int(self.manual_overnight_target_soc), "manual_slider"

        state = self.hass.states.get(self._recommended_overnight_soc_entity_id())
        if state is None or state.state in ("unknown", "unavailable"):
            return None, "recommended_overnight_soc"
        try:
            target = round(float(state.state))
        except (TypeError, ValueError):
            return None, "recommended_overnight_soc"
        return int(max(self._commanded_min_soc, min(target, 100))), "recommended_overnight_soc"

    def _recommended_overnight_soc_entity_id(self) -> str:
        if not self._entry_id:
            return "sensor.aecc_battery_recommended_overnight_soc"
        registry = er.async_get(self.hass)
        entity_id = registry.async_get_entity_id(
            "sensor",
            DOMAIN,
            f"{self._entry_id}_recommended_overnight_soc",
        )
        return entity_id or "sensor.aecc_battery_recommended_overnight_soc"

    def _overnight_window(self, now: datetime) -> dict[str, Any]:
        start_hour, start_minute = self._parse_hhmm(self.off_peak_start, DEFAULT_OFF_PEAK_START)
        end_hour, end_minute = self._parse_hhmm(self.off_peak_end, DEFAULT_OFF_PEAK_END)
        start = now.replace(
            hour=start_hour,
            minute=start_minute,
            second=0,
            microsecond=0,
        )
        end = now.replace(
            hour=end_hour,
            minute=end_minute,
            second=0,
            microsecond=0,
        )

        if start <= end:
            if now >= end:
                start += timedelta(days=1)
                end += timedelta(days=1)
        else:
            if now < end:
                start -= timedelta(days=1)
            else:
                end += timedelta(days=1)

        return {
            "key": start.strftime("%Y-%m-%d"),
            "start": start,
            "end": end,
            "effective_start": start + timedelta(minutes=1),
            "effective_end": end - timedelta(minutes=5),
        }

    @classmethod
    def _parse_hhmm(cls, value: str, fallback: str) -> tuple[int, int]:
        normalised = cls._normalise_hhmm(value, fallback)
        hour_s, minute_s = normalised.split(":", 1)
        return int(hour_s), int(minute_s)

    @staticmethod
    def _normalise_hhmm(value: str, fallback: str) -> str:
        try:
            hour_s, minute_s = str(value).strip().split(":", 1)
            hour = int(hour_s)
            minute = int(minute_s)
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return f"{hour:02d}:{minute:02d}"
        except (AttributeError, TypeError, ValueError):
            pass
        return fallback

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        try:
            if value is None:
                return None
            return int(float(value))
        except (TypeError, ValueError):
            return None

    # Delay between a SET command and the readback that verifies the device
    # actually accepted the change. Some AECC devices apply writes lazily;
    # a too-fast readback can hit the pre-write state and produce false
    # "mismatch" warnings. Half a second is empirically enough for Lunergy
    # and Sunpura without noticeably slowing user-facing UI updates.
    _WRITE_VERIFY_DELAY_SECONDS: float = 0.5

    async def _verify_write(
        self,
        expected: dict[str, str],
        operation: str,
    ) -> list[dict[str, Any]] | None:
        """Re-read registers after a write and warn on mismatch.

        Best-effort verification: the SET response already returned OK
        (otherwise the caller would have logged + returned False), so we
        do NOT change the return value of the calling write method. We
        only surface a WARNING when the device claimed success but the
        actual register state diverges, which is a real failure mode on
        AECC devices under load. Schedule slot strings (the long CSV) are
        log-only and never compared character-for-character because the
        device may normalise whitespace or trailing zeros.

        Returns a list of per-register verify entries (or ``None`` if the
        readback could not be performed) so callers like ``_logged_write``
        can persist the result for diagnostics. Each entry is
        ``{"register", "expected", "actual", "match"}`` where ``match`` is
        ``None`` for the schedule-slot string (log-only).
        """
        try:
            await asyncio.sleep(self._WRITE_VERIFY_DELAY_SECONDS)
            reg_addrs = [int(k) for k in expected.keys()]
            resp = await self.client.get_control_parameters(reg_addrs)
            if resp is None:
                _LOGGER.debug("Write-back verify for %s: no response (skipping)", operation)
                return None
            actual = resp.get("ControlInfo") or resp.get("GetParameters") or {}
            if not isinstance(actual, dict):
                return None
            results: list[dict[str, Any]] = []
            for reg, expected_val in expected.items():
                actual_val = actual.get(reg) or actual.get(int(reg))
                if reg == REG_CONTROL_TIME1:
                    # Schedule slot is a CSV string, device may reorder or
                    # rewrite parts. Log-only, no equality check.
                    _LOGGER.debug(
                        "Write-back verify for %s: %s = %r (expected %r)",
                        operation,
                        reg,
                        actual_val,
                        expected_val,
                    )
                    results.append(
                        {
                            "register": reg,
                            "expected": expected_val,
                            "actual": actual_val,
                            "match": None,
                        }
                    )
                    continue
                if actual_val is None:
                    results.append(
                        {
                            "register": reg,
                            "expected": expected_val,
                            "actual": None,
                            "match": None,
                        }
                    )
                    continue
                match = str(actual_val).strip() == str(expected_val).strip()
                results.append(
                    {
                        "register": reg,
                        "expected": expected_val,
                        "actual": actual_val,
                        "match": match,
                    }
                )
                if not match:
                    _LOGGER.warning(
                        "Write-back verify mismatch for %s: register %s expected %r, "
                        "device reports %r, write may have been silently dropped",
                        operation,
                        reg,
                        expected_val,
                        actual_val,
                    )
            return results
        except (TimeoutError, OSError, asyncio.IncompleteReadError) as exc:
            _LOGGER.debug("Write-back verify for %s failed: %s", operation, exc)
            return None

    async def _logged_write(self, payload: dict[str, str], operation: str) -> bool:
        """Send a control-register write and append an entry to the audit trail.

        Wraps ``client.set_control_parameters`` + ``_verify_write`` so all
        mutating coordinator methods record what they sent, whether the
        device acknowledged, and the per-register verify outcome. The
        rolling buffer is exposed via ``write_history`` for diagnostics.
        """
        entry: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "operation": operation,
            "payload": dict(payload),
            "response_received": False,
            "verify_result": None,
        }
        self._write_history.append(entry)
        resp = await self.client.set_control_parameters(payload)
        entry["response_received"] = resp is not None
        if resp is None:
            _LOGGER.warning("SET %s failed - no response from battery", operation)
            return False
        _LOGGER.debug("SET %s response: %s", operation, resp)
        entry["verify_result"] = await self._verify_write(payload, operation)
        if entry["verify_result"] is not None and any(
            item.get("match") is False for item in entry["verify_result"]
        ):
            _LOGGER.warning("SET %s failed write-back verification", operation)
            return False
        return True

    @property
    def write_history(self) -> list[dict[str, Any]]:
        """Recent control writes with verify outcomes (newest last)."""
        return list(self._write_history)

    @property
    def latest_write(self) -> dict[str, Any] | None:
        """Most recent control write, if any."""
        if not self._write_history:
            return None
        return self._write_history[-1]

    async def async_set_battery_control(self, direction: str, power_w: int) -> bool:
        has_storage = bool(self.data and self.data.get("Storage_list"))
        field7 = 5 if has_storage else 4

        charge_soc = self._commanded_max_soc
        discharge_soc = self._commanded_min_soc

        if direction == "Idle" or power_w == 0:
            slot1 = f"0,00:00,00:00,0,0,0,0,0,0,{charge_soc},{discharge_soc}"
        else:
            reg_power = -power_w if direction == "Charge" else power_w
            slot1 = f"1,00:00,23:59,{reg_power},0,6,{field7},0,0,{charge_soc},{discharge_soc}"

        payload = {
            REG_EMS_ENABLE: "1",
            REG_SCHEDULE_MODE: "6",
            REG_AI_SMART_CHARGE: "0",
            REG_AI_SMART_DISC: "0",
            REG_CUSTOM_MODE: "1",
            REG_CONTROL_TIME1: slot1,
        }

        if self.extended_power:
            payload[REG_MAX_FEED_POWER] = str(MAX_BATTERY_POWER_W)

        if power_w > MAX_REGISTER_POWER_DEFAULT and not self.extended_power:
            _LOGGER.warning(
                "Power %d W exceeds default 800 W limit. "
                "Enable 'Extended power range' in integration options to allow up to %d W.",
                power_w,
                MAX_BATTERY_POWER_W,
            )

        _LOGGER.info(
            "SET battery_control direction=%s power=%d W -> 3003=%r",
            direction,
            power_w,
            slot1,
        )

        success = await self._logged_write(payload, f"battery_control({direction}, {power_w}W)")
        if success:
            self._commanded_work_mode = MODE_CUSTOM
            self._commanded_direction = direction
            self._commanded_power = power_w
            if direction == "Charge" and power_w > 0:
                self.commanded_charge_power = power_w
            elif direction == "Discharge" and power_w > 0:
                self.commanded_discharge_power = power_w
        return success

    async def async_set_power_setpoint(self, watts: float) -> bool:
        power_w = int(watts)
        if power_w == 0:
            return await self.async_set_battery_control("Idle", 0)
        elif power_w > 0:
            return await self.async_set_battery_control("Charge", power_w)
        else:
            return await self.async_set_battery_control("Discharge", abs(power_w))

    async def async_restore_self_consumption(self) -> bool:
        """Robustly return the battery to Self-Consumption / AI mode.

        The basic upstream implementation only toggles the AI/custom flags.
        On some AECC batteries that leaves the previous custom time slot and
        schedule mode active, so the unit can appear to be in Self-Consumption
        while remaining effectively idle/manual.

        This sequence first clears the manual time slot, then applies the
        upstream-confirmed schedule mode 3 AI resume pattern while keeping
        EMS enabled. It also reasserts the zero-feed/base-discharge latch
        because Feed mode can otherwise be slow to release on some units.
        """
        clear_manual_payload = {
            REG_CONTROL_TIME1: SLOT_DISABLED,
            REG_CUSTOM_MODE: "0",
            REG_AI_SMART_DISC: "1",
            REG_BASE_DISCHARGE_ENABLE: "1",
        }

        restore_ai_payload = {
            REG_EMS_ENABLE: "1",
            REG_SCHEDULE_MODE: "3",
            REG_AI_SMART_CHARGE: "0",
            REG_AI_SMART_DISC: "1",
            REG_CUSTOM_MODE: "0",
            REG_CONTROL_TIME1: SLOT_DISABLED,
            REG_BASE_DISCHARGE_ENABLE: "1",
        }

        _LOGGER.info(
            "SET schedule-3 self-consumption: clear manual slot then restore AI/EMS"
        )

        for attempt in range(1, 4):
            await self._logged_write(
                clear_manual_payload,
                f"self_consumption(clear_manual_slot #{attempt})",
            )

            await asyncio.sleep(0.75)

            ok = await self._logged_write(
                restore_ai_payload,
                f"self_consumption(restore_ai_ems #{attempt})",
            )

            if ok:
                self._commanded_work_mode = MODE_SELF_CONSUMPTION
                self._commanded_direction = "Idle"
                self.commanded_operating_mode = "Self-Gen/Zero Export"
                self.commanded_feed_power = 0
                return True

            if attempt < 3:
                _LOGGER.warning(
                    "Self-consumption restore attempt %d failed; retrying",
                    attempt,
                )
                await asyncio.sleep(2)

        return False

    async def async_restore_original_self_consumption(self) -> bool:
        """Return to the stock self-consumption register set."""
        payload = {
            REG_EMS_ENABLE: "1",
            REG_AI_SMART_CHARGE: "1",
            REG_AI_SMART_DISC: "1",
            REG_CUSTOM_MODE: "0",
            REG_BASE_DISCHARGE_POWER: "0",
        }

        _LOGGER.info("SET stock self-consumption -> registers=%s", payload)

        success = await self._logged_write(payload, "self_consumption(stock)")
        if success:
            self._commanded_work_mode = MODE_SELF_CONSUMPTION
            self._commanded_direction = "Idle"
            self.commanded_operating_mode = "Self-Gen/Zero Export"
            self.commanded_feed_power = 0

        return success

    async def async_restore_schedule_3_self_consumption(self) -> bool:
        """Apply the upstream schedule-mode 3 self-consumption restore.

        This is intentionally separate from the stable dashboard Self mode so
        it can be tested against export/clipping behaviour without changing the
        known-good overnight automation path.
        """
        payload = {
            REG_EMS_ENABLE: "1",
            REG_SCHEDULE_MODE: "3",
            REG_AI_SMART_CHARGE: "1",
            REG_AI_SMART_DISC: "1",
            REG_CUSTOM_MODE: "0",
            REG_CONTROL_TIME1: SLOT_DISABLED,
            REG_BASE_DISCHARGE_POWER: "0",
        }

        _LOGGER.info("SET schedule-3 self-consumption -> registers=%s", payload)

        success = await self._logged_write(payload, "self_consumption(schedule_3)")
        if success:
            self._commanded_work_mode = MODE_SELF_CONSUMPTION
            self._commanded_direction = "Idle"
            self.commanded_operating_mode = "Self-Gen/Zero Export"
            self.commanded_feed_power = 0

        return success

    async def async_set_feed_power(self, power_w: int) -> bool:
        """Set the local base grid-connected feed/discharge power.

        This uses the EMS/custom base-feed path exposed by the cloud app as
        "Battery base grid-connected power". It is intentionally separate from
        the manual schedule Discharge mode.
        """
        feed_w = max(0, min(int(power_w), MAX_REGISTER_POWER_DEFAULT))
        payload = {
            REG_EMS_ENABLE: "1",
            REG_SCHEDULE_MODE: "3",
            REG_AI_SMART_CHARGE: "0",
            REG_AI_SMART_DISC: "0",
            REG_BASE_DISCHARGE_ENABLE: "1",
            REG_BASE_DISCHARGE_POWER: str(feed_w),
            REG_CUSTOM_MODE: "1",
            REG_CONTROL_TIME1: SLOT_DISABLED,
        }

        _LOGGER.info("SET feed/base discharge power=%d W -> register 3026", feed_w)
        success = await self._logged_write(payload, f"feed_power({feed_w}W)")
        if success:
            self.commanded_feed_power = feed_w
            self._commanded_power = feed_w
            self._commanded_direction = "Feed"
            self._commanded_work_mode = MODE_CUSTOM
            self.commanded_operating_mode = "Feed"
        return success

    async def async_set_work_mode(self, mode: str) -> bool:
        if mode == MODE_SELF_CONSUMPTION:
            return await self.async_restore_self_consumption()

        registers = MODE_REGISTERS.get(mode)
        if registers is None:
            _LOGGER.warning("SET work_mode: unknown mode %r", mode)
            return False

        _LOGGER.info("SET work_mode %r -> registers=%s", mode, registers)
        success = await self._logged_write(dict(registers), f"work_mode({mode})")
        if success:
            self._commanded_work_mode = mode
        return success

    async def async_set_min_soc(self, value: int) -> bool:
        self._commanded_min_soc = value
        payload = {REG_MIN_SOC: str(value)}
        return await self._logged_write(payload, f"min_soc({value}%)")

    async def async_set_max_soc(self, value: int) -> bool:
        self._commanded_max_soc = value
        payload = {REG_MAX_SOC: str(value)}
        return await self._logged_write(payload, f"max_soc({value}%)")

    async def async_set_surplus_charge_trigger(self, value: int) -> bool:
        """Set the PV surplus threshold that triggers grid-connected charging."""
        trigger_w = max(0, min(int(value), 50))
        payload = {REG_SURPLUS_CHARGE_TRIGGER: str(trigger_w)}
        return await self._logged_write(
            payload,
            f"surplus_charge_trigger({trigger_w}W)",
        )

    async def async_read_initial_state(self) -> None:
        resp = await self.client.get_control_parameters(
            [
                int(REG_EMS_ENABLE),
                int(REG_CONTROL_TIME1),
                int(REG_AI_SMART_CHARGE),
                int(REG_AI_SMART_DISC),
                int(REG_MIN_SOC),
                int(REG_MAX_SOC),
                int(REG_BASE_DISCHARGE_POWER),
                int(REG_BASE_DISCHARGE_ENABLE),
                int(REG_SURPLUS_CHARGE_TRIGGER),
                int(REG_CUSTOM_MODE),
                int(REG_MAX_FEED_POWER),
            ]
        )
        if resp is None:
            _LOGGER.warning("Failed to read initial control parameters (no response from battery)")
            return
        params = resp.get("ControlInfo") or resp.get("GetParameters") or resp.get("Parameters") or {}
        if not isinstance(params, dict):
            _LOGGER.debug(
                "Control parameters unexpected type: %s, response keys: %s",
                type(params).__name__,
                list(resp.keys()),
            )
            return
        if not params:
            _LOGGER.debug("Control parameters empty, response keys: %s", list(resp.keys()))

        def _int(key: str) -> int | None:
            val = params.get(key) or params.get(int(key))
            if val is None:
                return None
            try:
                return int(float(val))
            except (TypeError, ValueError):
                return None

        min_soc = _int(REG_MIN_SOC)
        max_soc = _int(REG_MAX_SOC)
        ems_on = _int(REG_EMS_ENABLE)
        ai_charge = _int(REG_AI_SMART_CHARGE)
        ai_discharge = _int(REG_AI_SMART_DISC)
        custom_mode = _int(REG_CUSTOM_MODE)
        base_discharge_power = _int(REG_BASE_DISCHARGE_POWER)
        surplus_charge_trigger = _int(REG_SURPLUS_CHARGE_TRIGGER)
        max_feed_power = _int(REG_MAX_FEED_POWER)

        if min_soc is not None:
            self.initial_min_soc = min_soc
            self._commanded_min_soc = min_soc
            _LOGGER.info("Read initial min SOC: %d%%", min_soc)
        if max_soc is not None:
            self.initial_max_soc = max_soc
            self._commanded_max_soc = max_soc
            _LOGGER.info("Read initial max SOC: %d%%", max_soc)
        if surplus_charge_trigger is not None:
            self.initial_surplus_charge_trigger = surplus_charge_trigger
            _LOGGER.info(
                "Read initial PV surplus charge trigger register 3037: %d W",
                surplus_charge_trigger,
            )
        if base_discharge_power is not None:
            self.initial_base_discharge_power = max(0, base_discharge_power)
            self.commanded_feed_power = self.initial_base_discharge_power
            _LOGGER.info(
                "Read initial base feed/discharge power register 3026: %d W",
                self.initial_base_discharge_power,
            )
        if max_feed_power is not None:
            self.initial_max_feed_power = max_feed_power
            _LOGGER.info("Read initial max feed power register 3039: %d W", max_feed_power)

        slot_str = params.get(REG_CONTROL_TIME1) or params.get(int(REG_CONTROL_TIME1))
        if slot_str and isinstance(slot_str, str):
            try:
                parts = slot_str.split(",")
                if len(parts) >= 4 and parts[0] == "1":
                    reg_power = int(parts[3])
                    self.initial_power = abs(reg_power)
                    self._commanded_power = self.initial_power
                    if reg_power > 0:
                        self._commanded_direction = "Discharge"
                        self.commanded_discharge_power = self.initial_power
                    elif reg_power < 0:
                        self._commanded_direction = "Charge"
                        self.commanded_charge_power = self.initial_power
                    else:
                        self._commanded_direction = "Idle"
                    _LOGGER.info(
                        "Read initial power: %d W (register value: %d, direction: %s)",
                        self.initial_power,
                        reg_power,
                        self._commanded_direction,
                    )
            except (ValueError, IndexError):
                _LOGGER.debug("Failed to parse control time slot: %r", slot_str)

        if ems_on == 0:
            self.initial_work_mode = MODE_DISABLED
        elif custom_mode == 1:
            self.initial_work_mode = MODE_CUSTOM
        elif ai_charge == 1 or ai_discharge == 1:
            self.initial_work_mode = MODE_SELF_CONSUMPTION
        else:
            self.initial_work_mode = MODE_CUSTOM

        if self.initial_work_mode:
            self._commanded_work_mode = self.initial_work_mode
            _LOGGER.info("Read initial work mode: %s", self.initial_work_mode)

        if (
            custom_mode == 1
            and self.initial_base_discharge_power
            and self.initial_base_discharge_power > 0
        ):
            self.initial_power = self.initial_base_discharge_power
            self._commanded_power = self.initial_base_discharge_power
            self._commanded_direction = "Feed"
            self.commanded_operating_mode = "Feed"
            _LOGGER.info(
                "Initial base feed/discharge power is active: %d W",
                self.initial_base_discharge_power,
            )

    async def async_probe_device_management(self) -> None:
        """Refresh safe device identity, topology and health metadata."""
        info = await self.client.get_device_management_info()
        if info is None:
            _LOGGER.debug("DeviceManagement probe returned nothing (not supported on all AECC devices)")
            return

        params = (
            info.get("DeviceManagementInfo")
            or info.get("ControlInfo")
            or info.get("Parameters")
            or info.get("GetParameters")
            or {}
        )
        if not isinstance(params, dict):
            _LOGGER.debug(
                "DeviceManagement params unexpected type: %s, response keys: %s",
                type(params).__name__,
                list(info.keys()),
            )
            return

        serial = params.get("8") or params.get(8)
        model_code = params.get("20") or params.get(20)
        firmware = params.get("21") or params.get(21)
        hardware = params.get("22") or params.get(22)
        device_clock = params.get("31") or params.get(31)
        sdk_version = params.get("61") or params.get(61)
        wifi_rssi = params.get("76") or params.get(76)

        if serial:
            self.device_serial = str(serial).strip()
            _LOGGER.info("DeviceManagement serial: %s", self.device_serial)
        if model_code:
            self.device_model_code = str(model_code).strip()
        if firmware:
            self.firmware_version = str(firmware).strip()
            _LOGGER.info("DeviceManagement firmware: %s", self.firmware_version)
        if hardware:
            self.device_hardware_version = str(hardware).strip()
        if device_clock:
            self.device_clock = str(device_clock).strip()
        if sdk_version:
            self.device_sdk_version = str(sdk_version).strip()
        try:
            self.wifi_rssi_dbm = int(float(wifi_rssi)) if wifi_rssi is not None else None
        except (TypeError, ValueError):
            self.wifi_rssi_dbm = None

        topology_value = params.get("102") or params.get(102)
        topology: list[dict[str, Any]] = []
        if isinstance(topology_value, dict):
            devices = topology_value.get("DeviceList") or []
            if isinstance(devices, list):
                role_by_class = {
                    1: "Master",
                    2: "Executor",
                    50: "Smart meter",
                    245: "Auxiliary",
                    254: "Auxiliary",
                }
                for device in devices:
                    if not isinstance(device, dict):
                        continue
                    try:
                        device_class = int(device.get("deviceClass"))
                    except (TypeError, ValueError):
                        device_class = None
                    device_serial = str(device.get("SN") or "").strip()
                    topology.append(
                        {
                            "role": role_by_class.get(device_class, "Unknown"),
                            "device_class": device_class,
                            "serial": device_serial or None,
                            "pair_status": device.get("PairStatus"),
                        }
                    )
            try:
                self.topology_reported_count = int(
                    topology_value.get("DeviceNum", len(topology))
                )
            except (TypeError, ValueError):
                self.topology_reported_count = len(topology)
        else:
            self.topology_reported_count = 0

        self.system_topology = topology
        self.topology_device_count = sum(
            1 for device in topology if device.get("serial")
        )
        self.inverter_count = sum(
            1
            for device in topology
            if device.get("role") in ("Master", "Executor")
            and device.get("serial")
        )
        self.master_serial = next(
            (
                str(device["serial"])
                for device in topology
                if device.get("role") == "Master" and device.get("serial")
            ),
            self.device_serial,
        )
        self.executor_serials = [
            str(device["serial"])
            for device in topology
            if device.get("role") == "Executor" and device.get("serial")
        ]
        self.meter_serial = next(
            (
                str(device["serial"])
                for device in topology
                if device.get("role") == "Smart meter" and device.get("serial")
            ),
            None,
        )

        meter_value = params.get("121") or params.get(121)
        if isinstance(meter_value, dict):
            meter_list = meter_value.get("ThirdPartyDevList") or []
            if isinstance(meter_list, list) and meter_list:
                meter = meter_list[0]
                if isinstance(meter, dict):
                    self.meter_name = str(meter.get("InstanceName") or "").strip() or None

        if topology:
            _LOGGER.info(
                "DeviceManagement topology: master=%s executors=%s meter=%s",
                self.master_serial,
                self.executor_serials,
                self.meter_name or self.meter_serial,
            )
