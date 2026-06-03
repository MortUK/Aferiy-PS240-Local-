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
    SLOT_DISABLED,
    OVERNIGHT_CHARGE_MODE_DISABLED,
    OVERNIGHT_CHARGE_MODE_MANUAL,
    OVERNIGHT_CHARGE_MODE_SMART,
    TARIFF_PRESETS,
)
from .tcp_client import AeccTcpClient

_LOGGER = logging.getLogger(__name__)

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
        self._manufacturer = manufacturer
        self._model = model
        self._consecutive_failures: int = 0
        self._last_good_data: dict[str, Any] | None = None
        self.last_successful_update: datetime | None = None
        self.last_failed_update: datetime | None = None
        self.last_failure_reason: str | None = None
        self._failure_tolerance: int = 5
        self.device_serial: str | None = None
        self.firmware_version: str | None = None
        self._commanded_power: int = 0
        self._commanded_direction: str = "Idle"
        self._commanded_work_mode: str | None = None
        self.commanded_operating_mode: str | None = None
        self.commanded_charge_power: int = 800
        self.commanded_discharge_power: int = 800
        self.battery_capacity_kwh: float = DEFAULT_BATTERY_CAPACITY_KWH
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
        self._overnight_last_action: str | None = None
        self._overnight_last_action_at: datetime | None = None
        self._overnight_last_window_key: str | None = None
        self._overnight_last_restored_window_key: str | None = None
        self._overnight_scheduler_started_charge: bool = False
        self.solar_unavailable_override: bool = False
        self._commanded_min_soc: int = 10
        self._commanded_max_soc: int = 100
        self.extended_power: bool = extended_power
        self.max_register_power: int = MAX_BATTERY_POWER_W if extended_power else MAX_REGISTER_POWER_DEFAULT
        self.initial_min_soc: int | None = None
        self.initial_max_soc: int | None = None
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

        if not valid:
            self._consecutive_failures += 1
            self.last_failed_update = datetime.now(UTC)
            self.last_failure_reason = "missing Storage_list/SSumInfoList"
            if self._consecutive_failures == 1:
                _LOGGER.warning(
                    "Poll response missing expected data (Storage_list/SSumInfoList). "
                    "Raw response keys: %s, raw (truncated): %.500s",
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
        self._last_good_data = raw
        self._schedule_overnight_evaluation()
        return raw

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
        entries = _FIELD_MAP.get(canonical_key)
        if not entries:
            return default
        raw_value: float | None = None
        for source, field, scale in entries:
            container = self.storage if source == "storage" else self.summary
            val = container.get(field)
            if val is not None:
                try:
                    raw_value = round(float(val) * scale, 1)
                    break
                except (TypeError, ValueError):
                    continue
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
            self._overnight_status = {
                "state": "Off",
                "mode": mode,
                "reason": "Automatic overnight charging is off.",
            }
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
        if self.overnight_charging_mode == OVERNIGHT_CHARGE_MODE_DISABLED:
            return
        if self._overnight_task is not None and not self._overnight_task.done():
            return
        self._overnight_task = self.hass.async_create_task(
            self.async_evaluate_overnight_charging()
        )

    async def async_evaluate_overnight_charging(self) -> None:
        """Apply the local overnight charge-to-target decision tree."""
        mode = self.overnight_charging_mode
        if mode == OVERNIGHT_CHARGE_MODE_DISABLED:
            return

        now = dt_util.now()
        window = self._overnight_window(now)
        target_soc, target_source = self._overnight_target_soc(mode)
        current_soc = self._safe_float(self.get_value("average_battery_soc"))
        base_attrs = {
            "mode": mode,
            "target_source": target_source,
            "target_soc": target_soc,
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

        if self._overnight_last_window_key != window["key"]:
            self._overnight_last_window_key = window["key"]
            self._overnight_scheduler_started_charge = False
            self._overnight_last_action = None

        if target_soc is None:
            self._set_overnight_status(
                "Waiting for target",
                "The smart target sensor is not available yet.",
                base_attrs,
            )
            return

        if current_soc is None:
            self._set_overnight_status(
                "Waiting for SOC",
                "System Average Battery SOC is not available yet.",
                base_attrs,
            )
            return

        if now < window["effective_start"]:
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
                await self._overnight_charge_to_target(target_soc, base_attrs)
            elif self._overnight_scheduler_started_charge:
                await self._overnight_idle_above_target(target_soc, base_attrs)
            else:
                self._set_overnight_status(
                    "Monitoring target",
                    "SOC is above target; waiting in case it falls during off-peak.",
                    base_attrs,
                )
            return

        if window["effective_end"] <= now < window["end"]:
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
        EMS enabled.
        """
        clear_manual_payload = {
            REG_CONTROL_TIME1: SLOT_DISABLED,
            REG_CUSTOM_MODE: "0",
        }

        restore_ai_payload = {
            REG_EMS_ENABLE: "1",
            REG_SCHEDULE_MODE: "3",
            REG_AI_SMART_CHARGE: "0",
            REG_AI_SMART_DISC: "1",
            REG_CUSTOM_MODE: "0",
            REG_CONTROL_TIME1: SLOT_DISABLED,
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
        }

        _LOGGER.info("SET stock self-consumption -> registers=%s", payload)

        success = await self._logged_write(payload, "self_consumption(stock)")
        if success:
            self._commanded_work_mode = MODE_SELF_CONSUMPTION
            self._commanded_direction = "Idle"
            self.commanded_operating_mode = "Self-Gen/Zero Export"

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
        }

        _LOGGER.info("SET schedule-3 self-consumption -> registers=%s", payload)

        success = await self._logged_write(payload, "self_consumption(schedule_3)")
        if success:
            self._commanded_work_mode = MODE_SELF_CONSUMPTION
            self._commanded_direction = "Idle"
            self.commanded_operating_mode = "Self-Gen/Zero Export"

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

    async def async_read_initial_state(self) -> None:
        resp = await self.client.get_control_parameters(
            [
                int(REG_EMS_ENABLE),
                int(REG_CONTROL_TIME1),
                int(REG_AI_SMART_CHARGE),
                int(REG_AI_SMART_DISC),
                int(REG_MIN_SOC),
                int(REG_MAX_SOC),
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
                return int(val)
            except (TypeError, ValueError):
                return None

        min_soc = _int(REG_MIN_SOC)
        max_soc = _int(REG_MAX_SOC)
        ems_on = _int(REG_EMS_ENABLE)
        ai_charge = _int(REG_AI_SMART_CHARGE)
        ai_discharge = _int(REG_AI_SMART_DISC)
        custom_mode = _int(REG_CUSTOM_MODE)
        max_feed_power = _int(REG_MAX_FEED_POWER)

        if min_soc is not None:
            self.initial_min_soc = min_soc
            self._commanded_min_soc = min_soc
            _LOGGER.info("Read initial min SOC: %d%%", min_soc)
        if max_soc is not None:
            self.initial_max_soc = max_soc
            self._commanded_max_soc = max_soc
            _LOGGER.info("Read initial max SOC: %d%%", max_soc)
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

    async def async_probe_device_management(self) -> None:
        info = await self.client.get_device_management_info()
        if info is None:
            _LOGGER.debug("DeviceManagement probe returned nothing (not supported on all AECC devices)")
            return

        params = info.get("DeviceManagementInfo") or info.get("Parameters") or info.get("GetParameters") or {}
        if not isinstance(params, dict):
            _LOGGER.debug(
                "DeviceManagement params unexpected type: %s, response keys: %s",
                type(params).__name__,
                list(info.keys()),
            )
            return

        serial = params.get("8") or params.get(8)
        firmware = params.get("21") or params.get(21)

        if serial:
            self.device_serial = str(serial).strip()
            _LOGGER.info("DeviceManagement serial: %s", self.device_serial)
        if firmware:
            self.firmware_version = str(firmware).strip()
            _LOGGER.info("DeviceManagement firmware: %s", self.firmware_version)
