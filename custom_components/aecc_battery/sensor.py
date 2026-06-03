"""Sensor platform for AECC Battery (Local TCP)."""

from __future__ import annotations

import json
import logging
import math
import os
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util.dt import utcnow

from .const import (
    CONF_ADVANCED_ENERGY_SENSORS,
    CONF_OFF_PEAK_END,
    CONF_OFF_PEAK_START,
    CONF_TARIFF_PRESET,
    DEFAULT_BATTERY_CAPACITY_KWH,
    DEFAULT_BATTERY_MODULE_COUNT,
    DEFAULT_OFF_PEAK_END,
    DEFAULT_OFF_PEAK_START,
    DEFAULT_TARIFF_PRESET,
    DOMAIN,
)
from .coordinator import AeccBatteryCoordinator

_LOGGER = logging.getLogger(__name__)

# ── Standard power/measurement sensors ────────────────────────────────────────
# (key, name, canonical_key, unit, icon, is_power)
_SENSORS = [
    ("ac_charging_power", "AC Charging Power", "ac_charging_power", UnitOfPower.WATT, "mdi:power-plug", True),
    (
        "system_average_battery_soc",
        "System Average Battery SOC",
        "average_battery_soc",
        PERCENTAGE,
        "mdi:battery-sync",
        False,
    ),
    ("pv_power", "PV Power", "pv_power", UnitOfPower.WATT, "mdi:solar-power", True),
    ("grid_power", "Grid / Meter Power", "grid_power", UnitOfPower.WATT, "mdi:transmission-tower", True),
    (
        "total_grid_output_power",
        "Total Grid Output Power",
        "total_grid_output_power",
        UnitOfPower.WATT,
        "mdi:transmission-tower-export",
        True,
    ),
    (
        "total_charge_power",
        "Total Charge Power",
        "total_charge_power",
        UnitOfPower.WATT,
        "mdi:battery-charging",
        True,
    ),
    (
        "control_enable_status",
        "Control Enable Status",
        "control_enable_status",
        None,
        "mdi:toggle-switch",
        False,
    ),
]

_DIAGNOSTIC_SENSOR_KEYS = {
    "total_grid_output_power",
    "total_charge_power",
    "control_enable_status",
}
_DISABLED_BY_DEFAULT_SENSOR_KEYS = set()

# ── Energy counter definitions ────────────────────────────────────────────────
# (key, name, power_keys, icon)
_ENERGY_SENSORS = [
    ("energy_charged", "Energy Charged", ["total_charge_power"], "mdi:battery-charging"),
    (
        "energy_discharged",
        "Energy Discharged",
        ["total_battery_output_power", "battery_discharging_power"],
        "mdi:battery-arrow-down-outline",
    ),
    ("energy_generated", "Energy Generated", ["pv_power"], "mdi:solar-power"),
]

_MAX_GAP_SECONDS = 60
_SOLCAST_DETAILED_FORECAST_PATHS = (
    "solcast_solar/solcast.json",
    "solcast_solar/solcast-undampened.json",
)
_SOLCAST_REMAINING_TODAY_ENTITY = "sensor.solcast_pv_forecast_forecast_remaining_today"
_SOLCAST_NEXT_HOUR_ENTITY = "sensor.solcast_pv_forecast_forecast_next_hour"
_SOLCAST_POWER_NOW_ENTITY = "sensor.solcast_pv_forecast_power_now"
_SOLCAST_TOMORROW_ENTITY = "sensor.solcast_pv_forecast_forecast_tomorrow"
_SHELLY_IMPORT_ENTITY = "sensor.shelly_grid_import_power"
_SHELLY_EXPORT_ENTITY = "sensor.shelly_grid_export_power"
_SHELLY_GRID_POWER_CANDIDATES = (
    "sensor.shellypro3em_841fe8916604_power",
    "sensor.shellypro3em_841fe8916604_phase_a_power",
)
_GRID_METER_POWER_ENTITY_FALLBACK = "sensor.aecc_battery_grid_meter_power"
_HOUSE_DEMAND_DAILY_ENTITY = "sensor.aecc_battery_house_demand_daily"
_AC_CHARGING_DAILY_ENTITY = "sensor.aferiy_ac_charging_daily"
_HOUSE_OCCUPANCY_ENTITY = "zone.home"
_OCTOPUS_FREE_SESSION_ENTITY = (
    "binary_sensor.octopus_energy_a_5c18533f_octoplus_free_electricity_session"
)
_OCTOPUS_OFF_PEAK_ENTITY = (
    "binary_sensor.octopus_energy_electricity_19k0195462_1900042087502_off_peak"
)
_OVERNIGHT_SMART_CHARGE_ENTITY = "input_boolean.overnight_smart_charge"
_SOLAR_AVAILABILITY_ENTITY = "select.aecc_battery_solar_availability"
_FORECAST_PERIOD = timedelta(minutes=30)
_RUNTIME_DEMAND_HISTORY_WINDOW = timedelta(hours=3)
_RUNTIME_DEMAND_MIN_HISTORY = timedelta(minutes=15)
_RUNTIME_RECORDER_HISTORY_DAYS = 14
_RUNTIME_RECORDER_REFRESH_INTERVAL = timedelta(minutes=30)
_RUNTIME_PROFILE_HORIZON = timedelta(hours=24)
_RUNTIME_PROFILE_INTERVAL = timedelta(minutes=30)
_RUNTIME_PROFILE_MAX_CYCLES = 14
_RUNTIME_RECORDER_RECENCY_DECAY = 0.9
_RUNTIME_RECORDER_MIN_DAY_WEIGHT = 0.35
_RUNTIME_RECORDER_SAME_WEEKDAY_BOOST = 1.25
_RUNTIME_MIN_VALID_DAILY_AVERAGE_W = 150.0
_RUNTIME_MIN_VALID_DAY_MEDIAN_FACTOR = 0.5
_RUNTIME_SOLAR_ACTIVE_THRESHOLD_W = 100.0
_ESTIMATED_HOUSE_DEMAND_ENTITY_FALLBACK = "sensor.aecc_battery_estimated_house_demand"
_PV_POWER_ENTITY_FALLBACK = "sensor.aecc_battery_pv_power"
_PV_CHARGING_POWER_ENTITY_FALLBACK = "sensor.aecc_battery_pv_charging_power"
_AC_CHARGING_POWER_ENTITY_FALLBACK = "sensor.aecc_battery_ac_charging_power"
_TOTAL_CHARGE_POWER_ENTITY_FALLBACK = "sensor.aecc_battery_total_charge_power"
_BATTERY_DISCHARGING_POWER_ENTITY_FALLBACK = "sensor.aecc_battery_battery_discharging_power"
_TOTAL_BATTERY_OUTPUT_POWER_ENTITY_FALLBACK = "sensor.aecc_battery_total_battery_output_power"
_FULL_SOC = 100.0
_OVERNIGHT_OCCUPIED_BASE_BUFFER_SOC = 3.0
_OVERNIGHT_EMPTY_HOUSE_BASE_BUFFER_SOC = 2.0
_OVERNIGHT_MAX_BUFFER_SOC = 7.0
_OVERNIGHT_EMPTY_HOUSE_MAX_BUFFER_SOC = 4.0
_OVERNIGHT_LOW_SOLAR_KWH = 4.0
_OVERNIGHT_MORNING_SHORTFALL_BUFFER_KWH = 0.75
_OVERNIGHT_DISCHARGE_EFFICIENCY = 0.92
_OVERNIGHT_GRID_CHARGE_EFFICIENCY = 0.90
_OVERNIGHT_TARGET_CHANGE_WARNING_SOC = 15
_OVERNIGHT_SOLCAST_STALE_AFTER = timedelta(hours=36)
_OVERNIGHT_CONFIDENCE_CAUTION_ADJUSTMENT_SOC = 5
_OVERNIGHT_CONFIDENCE_LOW_ADJUSTMENT_SOC = 10
_OVERNIGHT_STALE_DATA_MIN_SOC = 50
_OVERNIGHT_EMPTY_HOUSE_STALE_DATA_MIN_SOC = 25
_OVERNIGHT_USEFUL_SOLAR_CONSECUTIVE_PERIODS = 2
_OVERNIGHT_USEFUL_SOLAR_MARGIN_W = 75.0
_OVERNIGHT_USEFUL_SOLAR_DEMAND_FACTOR = 1.1
_OVERNIGHT_PRE_USEFUL_SOLAR_CREDIT_FACTOR = 0.25
_OVERNIGHT_BALANCED_SOLAR_PRE_USEFUL_CREDIT_FACTOR = 0.50
_OVERNIGHT_STRONG_SOLAR_PRE_USEFUL_CREDIT_FACTOR = 0.70
_OVERNIGHT_BALANCED_SOLAR_RATIO = 1.0
_OVERNIGHT_STRONG_SOLAR_RATIO = 1.2
_OVERNIGHT_NO_USEFUL_SOLAR_CREDIT_FACTOR = 0.7
_OCCUPIED_DAILY_DEMAND_FLOOR_KWH = 9.0
_EMPTY_HOUSE_DAILY_DEMAND_FLOOR_KWH = 3.0


def _configured_battery_module_count(coordinator: AeccBatteryCoordinator) -> int:
    try:
        module_count = int(
            getattr(
                coordinator,
                "configured_battery_module_count",
                DEFAULT_BATTERY_MODULE_COUNT,
            )
        )
    except (TypeError, ValueError):
        return DEFAULT_BATTERY_MODULE_COUNT
    return max(1, module_count)


def _parse_hhmm(value: Any, default: str) -> tuple[int, int, str]:
    text = str(value or default).strip()
    try:
        hour_text, minute_text = text.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute, f"{hour:02d}:{minute:02d}"
    except (TypeError, ValueError):
        pass
    default_hour, default_minute = (int(part) for part in default.split(":", 1))
    return default_hour, default_minute, default


def _as_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _state_float(
    hass: HomeAssistant,
    entity_id: str,
    default: float | None = None,
) -> float | None:
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unknown", "unavailable"):
        return default
    return _as_float(state.state, default)


def _signed_shelly_grid_power_w(hass: HomeAssistant) -> tuple[float | None, str | None]:
    for entity_id in _SHELLY_GRID_POWER_CANDIDATES:
        value = _state_float(hass, entity_id)
        if value is not None:
            return value, entity_id

    import_w = _state_float(hass, _SHELLY_IMPORT_ENTITY)
    export_w = _state_float(hass, _SHELLY_EXPORT_ENTITY)
    if import_w is None and export_w is None:
        return None, None
    return (import_w or 0.0) - (export_w or 0.0), "shelly_import_export_helpers"


def _house_empty_from_state(state: str | None) -> tuple[bool | None, int | None]:
    if state is None or state in ("unknown", "unavailable"):
        return None, None

    occupants = _as_float(state)
    if occupants is not None:
        return occupants <= 0, int(max(0, occupants))

    if state == "home":
        return False, 1
    if state in ("not_home", "away"):
        return True, 0
    return None, None


def _estimate_house_demand_w(
    hass: HomeAssistant,
    coordinator: AeccBatteryCoordinator,
) -> tuple[float, dict[str, Any]]:
    """Estimate live house demand from PV, battery, and AECC grid meter flow."""
    pv_w = _as_float(coordinator.get_value("pv_power"), 0.0) or 0.0
    total_charge_w = _as_float(coordinator.get_value("total_charge_power"), 0.0) or 0.0
    battery_charging_w = _as_float(coordinator.get_value("battery_charging_power"), 0.0) or 0.0
    pv_charging_w = _as_float(coordinator.get_value("pv_charging_power"), 0.0) or 0.0
    ac_charging_w = _as_float(coordinator.get_value("ac_charging_power"), 0.0) or 0.0
    if total_charge_w > 0:
        charge_w = total_charge_w
        charge_source = "total_charge_power"
    elif pv_charging_w > 0 or ac_charging_w > 0:
        charge_w = pv_charging_w + ac_charging_w
        charge_source = "pv_charging_plus_ac_charging"
    else:
        charge_w = max(battery_charging_w, ac_charging_w)
        charge_source = "battery_charging_fallback"
    discharge_w = max(
        _as_float(coordinator.get_value("total_battery_output_power"), 0.0) or 0.0,
        _as_float(coordinator.get_value("battery_discharging_power"), 0.0) or 0.0,
    )
    grid_w = _as_float(coordinator.get_value("grid_power"), 0.0) or 0.0
    import_w = max(0.0, grid_w)
    export_w = max(0.0, -grid_w)

    raw_house_demand_w = pv_w + import_w + discharge_w - charge_w - export_w
    house_demand_w = max(0.0, raw_house_demand_w)

    attrs = {
        "formula": "pv + grid_import + battery_discharge - battery_charge - grid_export",
        "pv_power_w": round(pv_w, 1),
        "grid_meter_power_w": round(grid_w, 1),
        "grid_import_w": round(import_w, 1),
        "grid_export_w": round(export_w, 1),
        "battery_charge_w": round(charge_w, 1),
        "battery_charge_source": charge_source,
        "total_charge_power_w": round(total_charge_w, 1),
        "pv_charging_power_w": round(pv_charging_w, 1),
        "ac_charging_power_w": round(ac_charging_w, 1),
        "battery_charging_power_w": round(battery_charging_w, 1),
        "battery_discharge_w": round(discharge_w, 1),
        "raw_house_demand_w": round(raw_house_demand_w, 1),
        "source_grid_meter": _GRID_METER_POWER_ENTITY_FALLBACK,
        "status": "estimated" if raw_house_demand_w >= 0 else "clamped_to_zero",
    }
    return round(house_demand_w, 1), attrs


async def async_setup_entry(
    hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AeccBatteryCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    entities: list[SensorEntity] = []

    for key, name, canonical_key, unit, icon, is_power in _SENSORS:
        entities.append(AeccSensor(coordinator, config_entry, key, name, canonical_key, unit, icon, is_power))

    configured_module_count = _configured_battery_module_count(coordinator)
    for index, entry in enumerate(coordinator.storage_entries[:configured_module_count]):
        unit_number = index + 1
        if entry.get("BatterySoc") is not None:
            entities.append(
                AeccStorageEntrySensor(
                    coordinator,
                    config_entry,
                    index,
                    f"battery_{unit_number}_soc",
                    f"Battery {unit_number} SOC",
                    "BatterySoc",
                    PERCENTAGE,
                    "mdi:battery-medium",
                    SensorDeviceClass.BATTERY,
                )
            )
    for key, name, power_keys, icon in _ENERGY_SENSORS:
        entities.append(AeccEnergySensor(coordinator, config_entry, key, name, power_keys, icon))

    entities.append(AeccGridExportSensor(coordinator, config_entry))
    entities.append(AeccTotalBatteryOutputPowerSensor(coordinator, config_entry))
    entities.append(AeccBatteryStatusSensor(coordinator, config_entry))
    entities.append(AeccConnectionStatusSensor(coordinator, config_entry))
    entities.append(AeccLastSuccessfulUpdateSensor(coordinator, config_entry))
    entities.append(AeccConsecutiveFailuresSensor(coordinator, config_entry))
    entities.append(AeccLastCommandResultSensor(coordinator, config_entry))
    entities.append(AeccGridMeterAgreementSensor(coordinator, config_entry))
    entities.append(AeccChargingReasonSensor(coordinator, config_entry))
    entities.append(AeccAutomaticOvernightChargingStatusSensor(coordinator, config_entry))

    entities.append(AeccEstimatedHouseDemandSensor(coordinator, config_entry))
    entities.append(AeccHouseDemandEnergySensor(coordinator, config_entry))
    entities.append(AeccHouseDemandDailySensor(coordinator, config_entry))
    entities.append(AeccRecommendedOvernightSocSensor(coordinator, config_entry))

    entities.append(AeccFirmwareSensor(coordinator, config_entry))

    async_add_entities(entities)


class AeccSensor(CoordinatorEntity[AeccBatteryCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: AeccBatteryCoordinator,
        config_entry: ConfigEntry,
        key: str,
        name: str,
        canonical_key: str,
        unit: str | None,
        icon: str,
        is_power: bool,
    ) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._canonical_key = canonical_key
        self._is_power = is_power
        self._attr_unique_id = f"{config_entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon
        if is_power:
            self._attr_device_class = SensorDeviceClass.POWER
        elif unit == PERCENTAGE:
            self._attr_device_class = SensorDeviceClass.BATTERY
        if key in _DIAGNOSTIC_SENSOR_KEYS:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        if key in _DISABLED_BY_DEFAULT_SENSOR_KEYS:
            self._attr_entity_registry_enabled_default = False
        self._last_value = None

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def native_value(self):
        val = self.coordinator.get_value(self._canonical_key)

        # Some AECC firmwares report total PV correctly but do not populate the
        # separate "PV charging" field. In that case, fall back to total PV power
        # so the dashboard does not show 0 W while the unit is clearly producing.
        if self._canonical_key == "pv_charging_power":
            try:
                pv_total = self.coordinator.get_value("pv_power")
                val_f = float(val or 0)
                pv_f = float(pv_total or 0)
                if val_f <= 0 and pv_f > 0:
                    val = pv_f
            except (TypeError, ValueError):
                pass

        # Some multi-unit AECC systems do not expose per-string PV values via
        # local TCP even though total PV is present. Showing 0 W is misleading,
        # so report unavailable instead when total PV is active but string value
        # is missing/zero.
        if self._canonical_key in ("pv1_power", "pv2_power"):
            try:
                pv_total = self.coordinator.get_value("pv_power")
                val_f = float(val or 0)
                pv_f = float(pv_total or 0)
                if val_f <= 0 and pv_f > 0:
                    return None
            except (TypeError, ValueError):
                pass

        if val is not None:
            self._last_value = val
            return val

        # Cleaner rejected the reading (or it was missing entirely).
        # Fall back to the last accepted value, but only while we're
        # still inside the hybrid "hold last value" window, beyond that
        # we report None so HA marks the entity unavailable rather than
        # publishing indefinitely-stale data.
        if not self._within_hold_window():
            return None
        return self._last_value

    @property
    def available(self) -> bool:
        if self._last_value is None:
            return self.coordinator.last_update_success
        if self._within_hold_window():
            return True
        # Hold window has expired with no fresh accepted reading ,
        # entity goes unavailable until the cleaner accepts again.
        return False

    def _within_hold_window(self) -> bool:
        """True while the entity may keep returning its last accepted value.

        After a cleaner-rejected reading, the entity holds the previous
        good value for ``hold_last_value_seconds`` (per brand profile).
        Beyond that window we surface the failure as unavailable instead
        of continuing to publish stale data, honest signal to users
        and automations that the underlying sensor has stopped working.
        """
        last_accepted_at = self.coordinator.cleaner_last_accepted_at(self._canonical_key)
        if last_accepted_at is None:
            # No cleaner state yet, treat as fresh (don't hide the entity
            # before we've seen any accepted reading).
            return True
        hold_seconds = float(self.coordinator.brand_profile.get("hold_last_value_seconds", 120))
        return (time.time() - last_accepted_at) <= hold_seconds


class AeccStorageEntrySensor(CoordinatorEntity[AeccBatteryCoordinator], RestoreEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: AeccBatteryCoordinator,
        config_entry: ConfigEntry,
        index: int,
        key: str,
        name: str,
        field: str,
        unit: str | None,
        icon: str,
        device_class: SensorDeviceClass,
    ) -> None:
        super().__init__(coordinator)
        self._index = index
        self._field = field
        self._last_value: float | None = None
        self._last_accepted_at: float | None = None
        self._attr_unique_id = f"{config_entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon
        self._attr_device_class = device_class

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in ("unknown", "unavailable"):
            try:
                value = round(float(last_state.state), 1)
            except (TypeError, ValueError):
                return
            if 0 <= value <= 100:
                self._last_value = value
                self._last_accepted_at = time.time() - 60

    @property
    def available(self) -> bool:
        if self._index >= _configured_battery_module_count(self.coordinator):
            return False
        if self._raw_value() is not None:
            return super().available
        return self._last_value is not None and self._within_hold_window()

    @property
    def native_value(self):
        if self._index >= _configured_battery_module_count(self.coordinator):
            return None
        val = self._clean_value(self._raw_value())
        if val is None:
            return self._last_value if self._within_hold_window() else None
        self._last_value = val
        self._last_accepted_at = time.time()
        return val

    def _raw_value(self) -> float | None:
        val = self.coordinator.storage_entry_val(self._index, self._field)
        if val is None:
            return None
        try:
            return round(float(val), 1)
        except (TypeError, ValueError):
            return None

    def _clean_value(self, raw: float | None) -> float | None:
        if raw is None or not 0 <= raw <= 100:
            return None

        profile = self.coordinator.brand_profile
        threshold_w = float(profile.get("soc_zero_reject_during_active_w", 100))
        wall_power_w = self.coordinator._wall_power_signal_w()
        if raw == 0 and wall_power_w is not None and abs(wall_power_w) > threshold_w:
            return None

        if self._last_value is not None and self._last_accepted_at is not None:
            now = time.time()
            elapsed_seconds = now - self._last_accepted_at
            if elapsed_seconds >= 1.0:
                elapsed_min = elapsed_seconds / 60.0
                max_rate = float(profile.get("soc_max_rate_pct_per_min", 8.0))
                change_per_min = abs(raw - self._last_value) / elapsed_min
                if change_per_min > max_rate:
                    return None

        return raw

    def _within_hold_window(self) -> bool:
        if self._last_accepted_at is None:
            return False
        hold_seconds = float(self.coordinator.brand_profile.get("hold_last_value_seconds", 120))
        return (time.time() - self._last_accepted_at) <= hold_seconds

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "source": f"Storage_list[{self._index}]",
            "source_field": self._field,
            "battery_index": self._index + 1,
            "configured_module_count": _configured_battery_module_count(self.coordinator),
            "visible_by_capacity_preset": (
                self._index < _configured_battery_module_count(self.coordinator)
            ),
            "available_unit_count": len(self.coordinator.storage_entries),
            "raw_value": self._raw_value(),
            "last_accepted_value": self._last_value,
        }


class AeccEnergySensor(CoordinatorEntity[AeccBatteryCoordinator], RestoreEntity, SensorEntity):
    """Accumulated energy (kWh) computed by integrating power over time."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 3

    def __init__(
        self,
        coordinator: AeccBatteryCoordinator,
        config_entry: ConfigEntry,
        key: str,
        name: str,
        power_keys: list[str],
        icon: str,
    ) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._key = key
        self._power_keys = power_keys
        self._attr_unique_id = f"{config_entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_icon = icon
        self._accumulated_kwh: float = 0.0
        self._last_update_time: datetime | None = None
        self._last_raw_power_w: float | None = None
        self._last_integrated_power_w: float | None = None

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def native_value(self) -> float:
        return round(self._accumulated_kwh, 3)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in ("unknown", "unavailable"):
            try:
                self._accumulated_kwh = float(last_state.state)
            except (TypeError, ValueError):
                self._accumulated_kwh = 0.0

    @callback
    def _handle_coordinator_update(self) -> None:
        now = utcnow()

        total_power_w = 0.0
        any_valid = False
        valid_values: list[float] = []
        for key in self._power_keys:
            val = self.coordinator.get_value(key)
            if val is not None:
                try:
                    value = float(val)
                    valid_values.append(value)
                    total_power_w += value
                    any_valid = True
                except (TypeError, ValueError):
                    pass

        if self._key == "energy_discharged" and valid_values:
            total_power_w = max(valid_values)

        self._last_raw_power_w = total_power_w if any_valid else None
        if any_valid:
            total_power_w = max(0.0, total_power_w)
            self._last_integrated_power_w = total_power_w
        else:
            self._last_integrated_power_w = None

        if any_valid and self._last_update_time is not None:
            delta_seconds = (now - self._last_update_time).total_seconds()
            if 0 < delta_seconds <= _MAX_GAP_SECONDS:
                delta_kwh = total_power_w * delta_seconds / 3_600_000
                self._accumulated_kwh += delta_kwh

        if any_valid:
            self._last_update_time = now

        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "source_power_keys": self._power_keys,
            "raw_source_power_w": self._last_raw_power_w,
            "integrated_power_w": self._last_integrated_power_w,
        }


class AeccGridExportSensor(CoordinatorEntity[AeccBatteryCoordinator], SensorEntity):
    """Grid export power derived from grid_power. Export = negative grid values only."""

    _attr_has_entity_name = True
    _attr_name = "Grid Export Power"
    _attr_icon = "mdi:transmission-tower-export"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, coordinator: AeccBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_grid_export_power"

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def native_value(self) -> float | None:
        grid = self.coordinator.get_value("grid_power")
        if grid is None:
            return None
        try:
            return max(0, round(-float(grid), 1))
        except (TypeError, ValueError):
            return None


class AeccTotalBatteryOutputPowerSensor(CoordinatorEntity[AeccBatteryCoordinator], SensorEntity):
    """Total battery output power from AECC summary data.

    This tries to expose the combined master/slave output value:
      summary.TotalBatteryOutputPower

    Useful where BatteryDischargingPower only appears to show the current/master unit.
    """

    _attr_has_entity_name = True
    _attr_name = "Battery Output"
    _attr_icon = "mdi:battery-arrow-down"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, coordinator: AeccBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_total_battery_output_power"

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def native_value(self) -> float | None:
        value = self.coordinator.get_value("total_battery_output_power")

        # Prefer the explicit combined summary field if it exists.
        if value is None:
            try:
                summary = getattr(self.coordinator, "summary", None)
                if isinstance(summary, dict):
                    value = summary.get("TotalBatteryOutputPower")
            except Exception:
                value = None

        # Fallback: try common data dictionaries used by the coordinator.
        if value is None:
            try:
                data = getattr(self.coordinator, "data", None)
                if isinstance(data, dict):
                    summary = data.get("summary")
                    if isinstance(summary, dict):
                        value = summary.get("TotalBatteryOutputPower")
                    if value is None:
                        value = data.get("TotalBatteryOutputPower")
            except Exception:
                value = None

        if value is None:
            return None

        try:
            return round(float(value), 1)
        except (TypeError, ValueError):
            return None


class AeccBatteryPowerSensor(CoordinatorEntity[AeccBatteryCoordinator], SensorEntity):
    """Single signed value: positive = charging, negative = discharging."""

    _attr_has_entity_name = True
    _attr_name = "Battery Power"
    _attr_icon = "mdi:battery-sync"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, coordinator: AeccBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_battery_power"

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def native_value(self) -> float | None:
        charge = self.coordinator.get_value("battery_charging_power") or 0
        ac_charge = self.coordinator.get_value("ac_charging_power") or 0
        discharge = self.coordinator.get_value("battery_discharging_power") or 0
        total_discharge = self.coordinator.get_value("total_battery_output_power") or 0
        try:
            effective_charge = max(float(charge), float(ac_charge))
            effective_discharge = max(float(discharge), float(total_discharge))
            return round(effective_charge - effective_discharge, 1)
        except (TypeError, ValueError):
            return None


class AeccEstimatedHouseDemandSensor(CoordinatorEntity[AeccBatteryCoordinator], SensorEntity):
    """Estimated whole-home demand from PV, battery flow, and AECC grid flow."""

    _attr_has_entity_name = True
    _attr_name = "House Demand"
    _attr_icon = "mdi:home-lightning-bolt"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, coordinator: AeccBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_estimated_house_demand"
        self._last_attributes: dict[str, Any] = {}

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def native_value(self) -> float | None:
        value, attrs = _estimate_house_demand_w(self.hass, self.coordinator)
        self._last_attributes = attrs
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return dict(self._last_attributes)


class AeccHouseDemandEnergyBase(
    CoordinatorEntity[AeccBatteryCoordinator],
    RestoreEntity,
    SensorEntity,
):
    """Integrate estimated house demand into kWh."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 3
    def __init__(self, coordinator: AeccBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._accumulated_kwh = 0.0
        self._last_update_time: datetime | None = None
        self._last_house_demand_w: float | None = None
        self._last_attributes: dict[str, Any] = {}

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def native_value(self) -> float:
        return round(self._accumulated_kwh, 3)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return dict(self._last_attributes)

    @property
    def available(self) -> bool:
        return True

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in ("unknown", "unavailable"):
            try:
                self._accumulated_kwh = max(0.0, float(last_state.state))
            except (TypeError, ValueError):
                self._accumulated_kwh = 0.0

    @callback
    def _handle_coordinator_update(self) -> None:
        now = utcnow()
        house_demand_w, attrs = _estimate_house_demand_w(self.hass, self.coordinator)
        self._reset_if_needed(now)

        if self._last_update_time is not None:
            delta_seconds = (now - self._last_update_time).total_seconds()
            if 0 < delta_seconds <= _MAX_GAP_SECONDS:
                self._accumulated_kwh += max(0.0, house_demand_w) * delta_seconds / 3_600_000

        self._last_update_time = now
        self._last_house_demand_w = house_demand_w
        self._last_attributes = {
            "source": "estimated_house_demand",
            "last_house_demand_w": round(house_demand_w, 1),
            "house_demand": attrs,
        }
        self.async_write_ha_state()

    def _reset_if_needed(self, now: datetime) -> None:
        """Optional reset hook for subclasses."""


class AeccHouseDemandEnergySensor(AeccHouseDemandEnergyBase):
    """Total increasing estimated house demand energy."""

    _attr_name = "House Demand Energy"
    _attr_icon = "mdi:home-lightning-bolt"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, coordinator: AeccBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_house_demand_energy"


class AeccHouseDemandDailySensor(AeccHouseDemandEnergyBase):
    """Daily estimated house demand energy."""

    _attr_name = "House Demand Daily"
    _attr_icon = "mdi:home-clock"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: AeccBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_house_demand_daily"
        self._local_date: str | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        restored_date = last_state.attributes.get("local_date") if last_state else None
        current_date = datetime.now().astimezone().date().isoformat()
        if restored_date != current_date:
            self._accumulated_kwh = 0.0
        self._local_date = current_date

    def _reset_if_needed(self, now: datetime) -> None:
        local_date = now.astimezone().date().isoformat()
        if self._local_date is None:
            self._local_date = local_date
        if local_date != self._local_date:
            self._local_date = local_date
            self._accumulated_kwh = 0.0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            **self._last_attributes,
            "local_date": self._local_date,
        }


class AeccGridMeterAgreementSensor(CoordinatorEntity[AeccBatteryCoordinator], SensorEntity):
    """Diagnostic difference between AECC and Shelly grid readings."""

    _attr_has_entity_name = True
    _attr_name = "Grid Meter Agreement"
    _attr_icon = "mdi:scale-balance"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, coordinator: AeccBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_grid_meter_agreement"
        self._last_attributes: dict[str, Any] = {}

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def native_value(self) -> float | None:
        aecc_grid_w = _as_float(self.coordinator.get_value("grid_power"))
        shelly_grid_w, shelly_source = _signed_shelly_grid_power_w(self.hass)

        if aecc_grid_w is None or shelly_grid_w is None:
            self._last_attributes = {
                "status": "missing_data",
                "aecc_grid_meter_power_w": round(aecc_grid_w, 1) if aecc_grid_w is not None else None,
                "shelly_grid_power_w": round(shelly_grid_w, 1) if shelly_grid_w is not None else None,
                "shelly_source": shelly_source,
                "note": "Positive is import; negative is export.",
            }
            return None

        difference_w = aecc_grid_w - shelly_grid_w
        abs_difference_w = abs(difference_w)
        if abs_difference_w <= 75:
            status = "matching"
        elif abs_difference_w <= 250:
            status = "minor_drift"
        else:
            status = "large_drift"

        self._last_attributes = {
            "status": status,
            "aecc_grid_meter_power_w": round(aecc_grid_w, 1),
            "shelly_grid_power_w": round(shelly_grid_w, 1),
            "difference_w": round(difference_w, 1),
            "absolute_difference_w": round(abs_difference_w, 1),
            "shelly_source": shelly_source,
            "matching_threshold_w": 75,
            "minor_drift_threshold_w": 250,
            "note": "Positive is import; negative is export.",
        }
        return round(difference_w, 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return dict(self._last_attributes)


class AeccChargingReasonSensor(CoordinatorEntity[AeccBatteryCoordinator], SensorEntity):
    """Human-readable reason for the current battery charge behavior."""

    _attr_has_entity_name = True
    _attr_name = "Charging Reason"
    _attr_icon = "mdi:message-processing-outline"

    def __init__(self, coordinator: AeccBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_charging_reason"
        self._last_attributes: dict[str, Any] = {}

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def native_value(self) -> str:
        mode = getattr(self.coordinator, "commanded_operating_mode", None)
        if not mode:
            mode = self.hass.states.get("select.aecc_battery_operating_mode")
            mode = mode.state if mode is not None else None

        free_session = self.hass.states.is_state(_OCTOPUS_FREE_SESSION_ENTITY, "on")
        off_peak = self.hass.states.is_state(_OCTOPUS_OFF_PEAK_ENTITY, "on")
        overnight_enabled = self.hass.states.is_state(_OVERNIGHT_SMART_CHARGE_ENTITY, "on")
        battery_soc = _state_float(self.hass, "sensor.aecc_battery_system_average_battery_soc")
        charge_limit = _state_float(self.hass, "number.aecc_battery_charge_limit")

        if free_session:
            reason = "Free Octopus session"
        elif off_peak and not overnight_enabled:
            reason = "Smart charge disabled"
        elif off_peak and overnight_enabled and battery_soc is not None and charge_limit is not None:
            reason = "Off-peak target charge" if battery_soc <= charge_limit else "Already above target"
        elif off_peak and overnight_enabled:
            reason = "Off-peak target pending"
        elif mode == "Charge":
            reason = "Manual charge"
        elif mode == "Discharge":
            reason = "Manual discharge"
        elif mode in ("Self-Gen/Zero Export", "Self-Consumption"):
            reason = "Self-consumption"
        elif mode == "Idle":
            reason = "Idle"
        else:
            reason = "Unknown"

        self._last_attributes = {
            "operating_mode": mode,
            "free_octopus_session": free_session,
            "off_peak": off_peak,
            "overnight_smart_charge": overnight_enabled,
            "battery_soc": round(battery_soc, 1) if battery_soc is not None else None,
            "charge_limit": round(charge_limit, 1) if charge_limit is not None else None,
        }
        return reason

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return dict(self._last_attributes)


class AeccAutomaticOvernightChargingStatusSensor(
    CoordinatorEntity[AeccBatteryCoordinator],
    SensorEntity,
):
    """Status for the integration-owned local overnight scheduler."""

    _attr_has_entity_name = True
    _attr_name = "Overnight Status"
    _attr_icon = "mdi:calendar-clock"
    def __init__(self, coordinator: AeccBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_automatic_overnight_charging_status"

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def native_value(self) -> str:
        return str(self.coordinator.overnight_charging_status.get("state", "Off"))

    @property
    def available(self) -> bool:
        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return dict(self.coordinator.overnight_charging_status)


class AeccBatteryStatusSensor(CoordinatorEntity[AeccBatteryCoordinator], SensorEntity):
    """Battery status derived from live power flow.

    The raw battery status field can report Idle even when the unit is clearly
    charging or discharging. This sensor prioritises measured power values.
    """

    _attr_has_entity_name = True
    _attr_name = "Battery Status"
    _attr_icon = "mdi:battery-heart-variant"

    def __init__(self, coordinator: AeccBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_battery_status"
        self._last_status: str = "Idle"

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def native_value(self) -> str:
        def _as_float(value, default=0.0):
            try:
                if value is None:
                    return default
                return float(value)
            except (TypeError, ValueError):
                return default

        threshold_w = 20.0

        ac_charge = _as_float(self.coordinator.get_value("ac_charging_power"))
        battery_charge = _as_float(self.coordinator.get_value("battery_charging_power"))
        pv_charge = _as_float(self.coordinator.get_value("pv_charging_power"))
        discharge = _as_float(self.coordinator.get_value("battery_discharging_power"))
        total_output = _as_float(self.coordinator.get_value("total_battery_output_power"))

        # Fallback for combined master/slave output summary field.
        if total_output <= threshold_w:
            try:
                summary = getattr(self.coordinator, "summary", None)
                if isinstance(summary, dict):
                    total_output = _as_float(summary.get("TotalBatteryOutputPower"))
            except Exception:
                pass

        # If PV charging is not exposed separately, use total PV as a weak
        # charging signal only when there is no battery output.
        if pv_charge <= threshold_w:
            try:
                pv_total = _as_float(self.coordinator.get_value("pv_power"))
                if pv_total > threshold_w and total_output <= threshold_w and discharge <= threshold_w:
                    pv_charge = pv_total
            except Exception:
                pass

        if ac_charge > threshold_w or battery_charge > threshold_w or pv_charge > threshold_w:
            status = "Charging"
        elif total_output > threshold_w or discharge > threshold_w:
            status = "Discharging"
        else:
            status = "Idle"

        self._last_status = status
        return status


class AeccConnectionStatusSensor(CoordinatorEntity[AeccBatteryCoordinator], SensorEntity):
    """Human-readable integration connection status."""

    _attr_has_entity_name = True
    _attr_name = "Connection Status"
    _attr_icon = "mdi:lan-connect"

    def __init__(self, coordinator: AeccBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_connection_status"

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def native_value(self) -> str:
        if self.coordinator.last_update_success:
            return "Online"
        if self.coordinator.last_successful_update is not None:
            return "Using last good data"
        return "Offline"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        latest_write = self.coordinator.latest_write or {}
        return {
            "host": self.coordinator.client.host,
            "port": self.coordinator.client.port,
            "poll_interval_seconds": (
                int(self.coordinator.update_interval.total_seconds())
                if self.coordinator.update_interval is not None
                else None
            ),
            "last_successful_update": (
                self.coordinator.last_successful_update.isoformat()
                if self.coordinator.last_successful_update is not None
                else None
            ),
            "last_failed_update": (
                self.coordinator.last_failed_update.isoformat()
                if self.coordinator.last_failed_update is not None
                else None
            ),
            "consecutive_failures": self.coordinator._consecutive_failures,
            "last_failure_reason": self.coordinator.last_failure_reason,
            "last_command": latest_write.get("operation"),
            "last_command_at": latest_write.get("timestamp"),
            "last_command_acknowledged": latest_write.get("response_received"),
        }


class AeccLastSuccessfulUpdateSensor(CoordinatorEntity[AeccBatteryCoordinator], SensorEntity):
    """Timestamp of the last successful local poll."""

    _attr_has_entity_name = True
    _attr_name = "Last Successful Update"
    _attr_icon = "mdi:update"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    def __init__(self, coordinator: AeccBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_last_successful_update"

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.last_successful_update


class AeccConsecutiveFailuresSensor(CoordinatorEntity[AeccBatteryCoordinator], SensorEntity):
    """Number of consecutive failed local polls."""

    _attr_has_entity_name = True
    _attr_name = "Consecutive Poll Failures"
    _attr_icon = "mdi:counter"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AeccBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_consecutive_poll_failures"

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def native_value(self) -> int:
        return self.coordinator._consecutive_failures

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "last_failure_reason": self.coordinator.last_failure_reason,
            "last_failed_update": (
                self.coordinator.last_failed_update.isoformat()
                if self.coordinator.last_failed_update is not None
                else None
            ),
        }


class AeccLastCommandResultSensor(CoordinatorEntity[AeccBatteryCoordinator], SensorEntity):
    """Result of the last control command sent to the battery."""

    _attr_has_entity_name = True
    _attr_name = "Last Command Result"
    _attr_icon = "mdi:clipboard-check-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AeccBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_last_command_result"

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def native_value(self) -> str:
        latest = self.coordinator.latest_write
        if latest is None:
            return "No commands sent"
        if not latest.get("response_received"):
            return "No response"
        verify = latest.get("verify_result")
        if not verify:
            return "Acknowledged"
        mismatches = [item for item in verify if item.get("match") is False]
        if mismatches:
            return "Verify mismatch"
        return "Verified"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        latest = self.coordinator.latest_write
        if latest is None:
            return {}
        return dict(latest)


class AeccEstimatedChargeTimeSensor(CoordinatorEntity[AeccBatteryCoordinator], SensorEntity):
    """Display-only estimate of time until the battery reaches 100% SOC."""

    _attr_has_entity_name = True
    _attr_name = "Estimated Charge Time"

    def __init__(self, coordinator: AeccBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_estimated_charge_time"
        self._last_attributes: dict[str, Any] = {}
        self._forecast_cache_mtime: float | None = None
        self._forecast_cache_path: str | None = None
        self._forecast_cache: list[dict[str, Any]] = []

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def icon(self) -> str:
        status = self._last_attributes.get("status")
        if status == "full":
            return "mdi:battery-check"
        if status in ("not_enough_forecast", "not_enough_today"):
            return "mdi:battery-alert"
        return "mdi:battery-clock"

    @property
    def native_value(self) -> str:
        state, attrs = self._calculate_estimate()
        self._last_attributes = attrs
        return state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return dict(self._last_attributes)

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    def _calculate_estimate(self) -> tuple[str, dict[str, Any]]:
        now = datetime.now(UTC)
        soc = self._as_float(self.coordinator.get_value("battery_soc"))
        capacity_kwh = self._as_float(
            getattr(self.coordinator, "battery_capacity_kwh", DEFAULT_BATTERY_CAPACITY_KWH),
            DEFAULT_BATTERY_CAPACITY_KWH,
        )
        target_soc = _FULL_SOC

        attrs: dict[str, Any] = {
            "calculated_at": now.isoformat(),
            "target_soc": target_soc,
            "battery_capacity_kwh": round(capacity_kwh, 2),
            "solar_forecast_source": None,
            "forecast_remaining_today_kwh": self._state_energy_kwh(_SOLCAST_REMAINING_TODAY_ENTITY),
        }

        if soc is None or capacity_kwh <= 0:
            attrs["status"] = "missing_data"
            attrs["reason"] = "Battery SOC or capacity is unavailable"
            return "Unknown", attrs

        energy_needed_kwh = capacity_kwh * max(0.0, target_soc - soc) / 100.0
        attrs.update(
            {
                "current_soc": round(soc, 1),
                "energy_needed_kwh": round(energy_needed_kwh, 2),
            }
        )

        if energy_needed_kwh <= 0.05:
            attrs["status"] = "full"
            attrs["estimated_full_at"] = now.isoformat()
            return "Full", attrs

        home_load_kw = self._estimate_home_load_kw()
        attrs["estimated_home_load_w"] = round(home_load_kw * 1000, 1)

        estimate = self._estimate_from_detailed_forecast(now, energy_needed_kwh, home_load_kw)
        if estimate is None:
            estimate = self._estimate_from_solcast_entities(now, energy_needed_kwh, home_load_kw)

        attrs.update(estimate["attributes"])

        eta = estimate.get("eta")
        if isinstance(eta, datetime):
            attrs["status"] = "estimated"
            attrs["estimated_full_at"] = eta.isoformat()
            attrs["hours_to_full"] = round((eta - now).total_seconds() / 3600, 2)
            return self._format_duration(eta - now), attrs

        return estimate["state"], attrs

    def _estimate_from_detailed_forecast(
        self,
        now: datetime,
        energy_needed_kwh: float,
        home_load_kw: float,
    ) -> dict[str, Any] | None:
        forecasts = self._load_solcast_forecasts()
        if not forecasts:
            return None

        cumulative_kwh = 0.0
        raw_forecast_kwh = 0.0

        for item in forecasts:
            period_start = self._parse_datetime(item.get("period_start"))
            forecast_kw = self._as_float(item.get("pv_estimate"), 0.0)
            if period_start is None or forecast_kw <= 0:
                continue

            period_end = period_start + _FORECAST_PERIOD
            if period_end <= now:
                continue

            start = max(period_start, now)
            hours = (period_end - start).total_seconds() / 3600
            if hours <= 0:
                continue

            raw_forecast_kwh += forecast_kw * hours
            net_charge_kw = max(0.0, forecast_kw - home_load_kw)
            if net_charge_kw <= 0:
                continue

            segment_kwh = net_charge_kw * hours
            if cumulative_kwh + segment_kwh >= energy_needed_kwh:
                remaining_in_segment_kwh = energy_needed_kwh - cumulative_kwh
                hours_into_segment = remaining_in_segment_kwh / net_charge_kw
                return {
                    "eta": start + timedelta(hours=hours_into_segment),
                    "attributes": {
                        "solar_forecast_source": "Solcast detailed forecast file",
                        "solar_forecast_path": self._forecast_cache_path,
                        "usable_forecast_kwh": round(cumulative_kwh + segment_kwh, 2),
                        "raw_future_forecast_kwh": round(raw_forecast_kwh, 2),
                        "method": "detailed_forecast_minus_current_home_load",
                    },
                }

            cumulative_kwh += segment_kwh

        return {
            "state": "Not in forecast",
            "attributes": {
                "status": "not_enough_forecast",
                "solar_forecast_source": "Solcast detailed forecast file",
                "solar_forecast_path": self._forecast_cache_path,
                "usable_forecast_kwh": round(cumulative_kwh, 2),
                "raw_future_forecast_kwh": round(raw_forecast_kwh, 2),
                "method": "detailed_forecast_minus_current_home_load",
            },
        }

    def _estimate_from_solcast_entities(
        self,
        now: datetime,
        energy_needed_kwh: float,
        home_load_kw: float,
    ) -> dict[str, Any]:
        remaining_today_kwh = self._state_energy_kwh(_SOLCAST_REMAINING_TODAY_ENTITY)
        next_hour_kwh = self._state_energy_kwh(_SOLCAST_NEXT_HOUR_ENTITY)
        power_now_kw = self._state_power_kw(_SOLCAST_POWER_NOW_ENTITY)

        attrs = {
            "solar_forecast_source": "Solcast sensors",
            "method": "remaining_today_and_next_hour_sensors",
            "usable_forecast_kwh": None,
            "next_hour_forecast_kwh": next_hour_kwh,
            "power_now_kw": power_now_kw,
        }

        if remaining_today_kwh is None:
            return {
                "state": "Unknown",
                "attributes": {
                    **attrs,
                    "status": "missing_data",
                    "reason": f"{_SOLCAST_REMAINING_TODAY_ENTITY} is unavailable",
                },
            }

        usable_today_kwh = max(0.0, remaining_today_kwh)
        attrs["usable_forecast_kwh"] = round(usable_today_kwh, 2)
        if usable_today_kwh < energy_needed_kwh:
            return {
                "state": "Not today",
                "attributes": {
                    **attrs,
                    "status": "not_enough_today",
                },
            }

        rate_kw = 0.0
        if next_hour_kwh is not None:
            rate_kw = max(rate_kw, next_hour_kwh - home_load_kw)
        if power_now_kw is not None:
            rate_kw = max(rate_kw, power_now_kw - home_load_kw)

        if rate_kw <= 0:
            return {
                "state": "Enough sun later",
                "attributes": {
                    **attrs,
                    "status": "waiting_for_sun",
                },
            }

        return {
            "eta": now + timedelta(hours=energy_needed_kwh / rate_kw),
            "attributes": attrs,
        }

    def _estimate_home_load_kw(self) -> float:
        house_load_w, _attrs = _estimate_house_demand_w(self.hass, self.coordinator)
        return house_load_w / 1000

    def _load_solcast_forecasts(self) -> list[dict[str, Any]]:
        for relative_path in _SOLCAST_DETAILED_FORECAST_PATHS:
            path = self.hass.config.path(relative_path)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue

            if path == self._forecast_cache_path and mtime == self._forecast_cache_mtime:
                return self._forecast_cache

            try:
                with open(path, encoding="utf-8") as forecast_file:
                    data = json.load(forecast_file)
            except (OSError, json.JSONDecodeError) as exc:
                _LOGGER.debug("Could not read Solcast forecast file %s: %s", path, exc)
                continue

            forecasts = self._combine_solcast_site_forecasts(data)
            self._forecast_cache_path = path
            self._forecast_cache_mtime = mtime
            self._forecast_cache = forecasts
            return forecasts

        return []

    def _combine_solcast_site_forecasts(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        siteinfo = data.get("siteinfo")
        if not isinstance(siteinfo, dict):
            return []

        combined: dict[str, float] = {}
        for site in siteinfo.values():
            if not isinstance(site, dict):
                continue
            forecasts = site.get("forecasts")
            if not isinstance(forecasts, list):
                continue
            for item in forecasts:
                if not isinstance(item, dict):
                    continue
                period_start = item.get("period_start")
                forecast_kw = self._as_float(item.get("pv_estimate"), 0.0)
                if period_start:
                    combined[str(period_start)] = combined.get(str(period_start), 0.0) + forecast_kw

        return [
            {"period_start": period_start, "pv_estimate": forecast_kw}
            for period_start, forecast_kw in sorted(combined.items())
        ]

    def _state_energy_kwh(self, entity_id: str) -> float | None:
        value = self._state_float(entity_id)
        if value is None:
            return None
        state = self.hass.states.get(entity_id)
        unit = (state.attributes.get("unit_of_measurement") if state else "") or ""
        if unit.lower() == "wh":
            return value / 1000
        return value

    def _state_power_kw(self, entity_id: str) -> float | None:
        value = self._state_float(entity_id)
        if value is None:
            return None
        state = self.hass.states.get(entity_id)
        unit = (state.attributes.get("unit_of_measurement") if state else "") or ""
        if unit.lower() == "kw":
            return value
        return value / 1000

    def _state_float(self, entity_id: str, default: float | None = None) -> float | None:
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return default
        return self._as_float(state.state, default)

    @staticmethod
    def _as_float(value: Any, default: float | None = None) -> float | None:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    @staticmethod
    def _format_duration(delta: timedelta) -> str:
        total_minutes = max(0, int(round(delta.total_seconds() / 60)))
        days, remainder = divmod(total_minutes, 24 * 60)
        hours, minutes = divmod(remainder, 60)

        if days:
            return f"{days}d {hours}h"
        if hours:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"


class AeccWillFillTodaySensor(AeccEstimatedChargeTimeSensor):
    """Whether the battery is likely to reach 100% from today's solar forecast."""

    _attr_has_entity_name = True
    _attr_name = "Will Fill Today"

    def __init__(self, coordinator: AeccBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_will_fill_today"

    @property
    def icon(self) -> str:
        state = self._last_attributes.get("status")
        if state in ("full", "yes"):
            return "mdi:battery-check"
        if state == "maybe":
            return "mdi:battery-clock"
        return "mdi:battery-alert"

    @property
    def native_value(self) -> str:
        state, attrs = self._calculate_fill_today()
        self._last_attributes = attrs
        return state

    def _calculate_fill_today(self) -> tuple[str, dict[str, Any]]:
        now = datetime.now(UTC)
        local_now = datetime.now().astimezone()
        local_tomorrow = (local_now + timedelta(days=1)).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        end_today = local_tomorrow.astimezone(UTC)

        soc = self._as_float(self.coordinator.get_value("battery_soc"))
        capacity_kwh = self._as_float(getattr(self.coordinator, "battery_capacity_kwh", 0.0), 0.0)
        attrs: dict[str, Any] = {
            "calculated_at": now.isoformat(),
            "forecast_until": end_today.isoformat(),
            "target_soc": _FULL_SOC,
            "battery_capacity_kwh": round(capacity_kwh, 3),
        }

        if soc is None or capacity_kwh <= 0:
            attrs["status"] = "missing_data"
            attrs["reason"] = "Battery SOC or capacity is unavailable"
            return "Unknown", attrs

        energy_needed_kwh = capacity_kwh * max(0.0, _FULL_SOC - soc) / 100.0
        home_load_w, house_attrs = _estimate_house_demand_w(self.hass, self.coordinator)
        home_load_kw = home_load_w / 1000
        attrs.update(
            {
                "current_soc": round(soc, 1),
                "energy_needed_kwh": round(energy_needed_kwh, 2),
                "estimated_house_demand_w": round(home_load_w, 1),
                "house_demand": house_attrs,
            }
        )

        if energy_needed_kwh <= 0.05:
            attrs["status"] = "full"
            attrs["shortfall_kwh"] = 0
            return "Full", attrs

        forecast = self._solar_surplus_until(now, end_today, home_load_kw, energy_needed_kwh)
        attrs.update(forecast)

        usable_kwh = forecast.get("usable_forecast_kwh")
        raw_kwh = forecast.get("raw_forecast_kwh")
        if usable_kwh is None:
            attrs["status"] = "missing_data"
            return "Unknown", attrs

        shortfall_kwh = max(0.0, energy_needed_kwh - float(usable_kwh))
        attrs["shortfall_kwh"] = round(shortfall_kwh, 2)

        if shortfall_kwh <= 0:
            attrs["status"] = "yes"
            return "Yes", attrs

        if raw_kwh is not None and float(raw_kwh) >= energy_needed_kwh:
            attrs["status"] = "maybe"
            return "Maybe", attrs

        attrs["status"] = "no"
        return "No", attrs

    def _solar_surplus_until(
        self,
        now: datetime,
        deadline: datetime,
        home_load_kw: float,
        target_kwh: float,
    ) -> dict[str, Any]:
        forecasts = self._load_solcast_forecasts()
        if not forecasts:
            remaining_today_kwh = self._state_energy_kwh(_SOLCAST_REMAINING_TODAY_ENTITY)
            return {
                "solar_forecast_source": "Solcast remaining today sensor",
                "raw_forecast_kwh": remaining_today_kwh,
                "usable_forecast_kwh": remaining_today_kwh,
                "method": "remaining_today_sensor_no_timing",
            }

        raw_forecast_kwh = 0.0
        usable_forecast_kwh = 0.0
        estimated_full_at: str | None = None

        for item in forecasts:
            period_start = self._parse_datetime(item.get("period_start"))
            forecast_kw = self._as_float(item.get("pv_estimate"), 0.0)
            if period_start is None or forecast_kw <= 0:
                continue

            period_end = period_start + _FORECAST_PERIOD
            if period_end <= now or period_start >= deadline:
                continue

            start = max(period_start, now)
            end = min(period_end, deadline)
            hours = (end - start).total_seconds() / 3600
            if hours <= 0:
                continue

            raw_forecast_kwh += forecast_kw * hours
            net_charge_kw = max(0.0, forecast_kw - home_load_kw)
            segment_kwh = net_charge_kw * hours

            if estimated_full_at is None and net_charge_kw > 0 and usable_forecast_kwh + segment_kwh >= target_kwh:
                remaining_in_segment_kwh = target_kwh - usable_forecast_kwh
                eta = start + timedelta(hours=remaining_in_segment_kwh / net_charge_kw)
                estimated_full_at = eta.isoformat()

            usable_forecast_kwh += segment_kwh

        return {
            "solar_forecast_source": "Solcast detailed forecast file",
            "solar_forecast_path": self._forecast_cache_path,
            "raw_forecast_kwh": round(raw_forecast_kwh, 2),
            "usable_forecast_kwh": round(usable_forecast_kwh, 2),
            "estimated_full_at": estimated_full_at,
            "method": "forecast_until_midnight_minus_current_house_demand",
        }


class AeccRuntimeAtCurrentHouseDemandSensor(CoordinatorEntity[AeccBatteryCoordinator], SensorEntity):
    """Estimated runtime until reserve using recent house-demand history."""

    _attr_has_entity_name = True
    _attr_name = "Runtime Left"
    _attr_icon = "mdi:battery-clock"

    def __init__(self, coordinator: AeccBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_runtime_at_current_house_demand"
        self._last_attributes: dict[str, Any] = {}
        self._demand_history: deque[tuple[datetime, float]] = deque(maxlen=5000)
        self._recorder_average_demand_w: float | None = None
        self._recorder_demand_profile: list[dict[str, Any]] = []
        self._recorder_profile_start: datetime | None = None
        self._recorder_history_attrs: dict[str, Any] = {
            "recorder_history_status": "warming",
            "recorder_history_lookback_days": _RUNTIME_RECORDER_HISTORY_DAYS,
            "recorder_history_window_hours": round(_RUNTIME_PROFILE_HORIZON.total_seconds() / 3600, 1),
        }
        self._last_recorder_refresh: datetime | None = None
        self._recorder_refresh_in_progress = False
        self._last_runtime_demand_w: float | None = None

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def native_value(self) -> str:
        self._record_house_demand_sample()
        state, attrs = self._calculate_runtime()
        self._last_attributes = attrs
        return state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return dict(self._last_attributes)

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @callback
    def _handle_coordinator_update(self) -> None:
        self._record_house_demand_sample()
        self.async_write_ha_state()

    def _record_house_demand_sample(self) -> None:
        now = datetime.now(UTC)
        if self._solar_active_now():
            self._prune_demand_history(now)
            return

        house_demand_w, _attrs = _estimate_house_demand_w(self.hass, self.coordinator)
        if house_demand_w < 0:
            return

        if self._demand_history:
            last_time, last_value = self._demand_history[-1]
            if (now - last_time).total_seconds() < 30 and abs(last_value - house_demand_w) < 25:
                return

        self._demand_history.append((now, house_demand_w))
        self._prune_demand_history(now)

    def _prune_demand_history(self, now: datetime) -> None:
        cutoff = now - _RUNTIME_DEMAND_HISTORY_WINDOW
        while len(self._demand_history) > 1 and self._demand_history[1][0] < cutoff:
            self._demand_history.popleft()

    def _historical_average_demand_w(self, now: datetime, live_demand_w: float) -> tuple[float, dict[str, Any]]:
        self._schedule_recorder_history_refresh(now)
        self._prune_demand_history(now)
        samples = list(self._demand_history)
        if not samples:
            return self._recorder_or_fallback_demand_w(
                now,
                live_demand_w,
                {
                    "demand_basis": "live",
                    "history_sample_count": 0,
                    "history_duration_minutes": 0,
                },
            )

        cutoff = now - _RUNTIME_DEMAND_HISTORY_WINDOW
        if samples[0][0] > cutoff:
            effective_samples = samples
        else:
            effective_samples = [(cutoff, samples[0][1]), *samples[1:]]

        if effective_samples[-1][0] < now:
            effective_samples.append((now, live_demand_w))

        duration_seconds = (effective_samples[-1][0] - effective_samples[0][0]).total_seconds()
        if duration_seconds < _RUNTIME_DEMAND_MIN_HISTORY.total_seconds():
            return self._recorder_or_fallback_demand_w(
                now,
                live_demand_w,
                {
                    "demand_basis": "live_until_history_warms",
                    "history_sample_count": len(samples),
                    "history_duration_minutes": round(duration_seconds / 60, 1),
                    "minimum_history_minutes": round(_RUNTIME_DEMAND_MIN_HISTORY.total_seconds() / 60, 1),
                },
            )

        watt_seconds = 0.0
        for index in range(1, len(effective_samples)):
            previous_time, previous_watts = effective_samples[index - 1]
            current_time, _current_watts = effective_samples[index]
            interval_seconds = max(0.0, (current_time - previous_time).total_seconds())
            watt_seconds += previous_watts * interval_seconds

        average_w = watt_seconds / duration_seconds if duration_seconds > 0 else live_demand_w
        return self._recorder_or_fallback_demand_w(
            now,
            average_w,
            {
                "demand_basis": "rolling_energy_history",
                "history_window_hours": round(_RUNTIME_DEMAND_HISTORY_WINDOW.total_seconds() / 3600, 1),
                "history_sample_count": len(samples),
                "history_duration_minutes": round(duration_seconds / 60, 1),
                "history_energy_kwh": round(watt_seconds / 3_600_000, 3),
            },
        )

    def _recorder_or_fallback_demand_w(
        self,
        now: datetime,
        fallback_demand_w: float,
        fallback_attrs: dict[str, Any],
    ) -> tuple[float, dict[str, Any]]:
        solar_active = self._solar_active_now()
        attrs = {
            **fallback_attrs,
            **self._current_recorder_history_attrs(now),
            "runtime_assumption": "no_solar_generation",
            "solar_active_now": solar_active,
            "solar_active_threshold_w": _RUNTIME_SOLAR_ACTIVE_THRESHOLD_W,
        }
        if self._recorder_average_demand_w is None:
            if solar_active:
                if self._last_runtime_demand_w is None:
                    self._last_runtime_demand_w = fallback_demand_w
                    attrs["demand_basis"] = f"{fallback_attrs.get('demand_basis', 'live')}_initial_solar_fallback"
                else:
                    attrs["demand_basis"] = "held_no_solar_runtime_baseline"
                attrs["held_runtime_demand_w"] = round(self._last_runtime_demand_w, 1)
                return self._last_runtime_demand_w, attrs

            self._last_runtime_demand_w = fallback_demand_w
            return fallback_demand_w, attrs

        attrs["rolling_demand_w"] = round(fallback_demand_w, 1)
        attrs["rolling_demand_basis"] = fallback_attrs.get("demand_basis")
        attrs["demand_basis"] = "same_time_previous_days"
        self._last_runtime_demand_w = self._recorder_average_demand_w
        return self._recorder_average_demand_w, attrs

    def _solar_active_now(self) -> bool:
        values = (
            _as_float(self.coordinator.get_value("pv_power"), 0.0) or 0.0,
            _as_float(self.coordinator.get_value("pv_charging_power"), 0.0) or 0.0,
        )
        return max(values) > _RUNTIME_SOLAR_ACTIVE_THRESHOLD_W

    def _current_recorder_history_attrs(self, now: datetime) -> dict[str, Any]:
        attrs = dict(self._recorder_history_attrs)
        if self._last_recorder_refresh is not None:
            attrs["recorder_history_age_minutes"] = round(
                (now - self._last_recorder_refresh).total_seconds() / 60,
                1,
            )
        if self._recorder_refresh_in_progress:
            attrs["recorder_history_status"] = "refreshing"
        return attrs

    def _schedule_recorder_history_refresh(self, now: datetime) -> None:
        if self._recorder_refresh_in_progress:
            return
        if (
            self._last_recorder_refresh is not None
            and now - self._last_recorder_refresh < _RUNTIME_RECORDER_REFRESH_INTERVAL
        ):
            return
        self._recorder_refresh_in_progress = True
        self.hass.async_create_task(self._async_refresh_recorder_history(now))

    async def _async_refresh_recorder_history(self, now: datetime) -> None:
        entity_id = self._estimated_house_demand_entity_id()
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.history import state_changes_during_period

            recorder = get_instance(self.hass)
            daily_results: list[dict[str, Any]] = []
            skipped_away_days: list[dict[str, Any]] = []
            local_now = now.astimezone()
            self._recorder_profile_start = now

            for days_ago in range(1, _RUNTIME_RECORDER_HISTORY_DAYS + 1):
                window_start_local = local_now - timedelta(days=days_ago)
                window_end_local = window_start_local + _RUNTIME_PROFILE_HORIZON
                start = window_start_local.astimezone(UTC)
                end = window_end_local.astimezone(UTC)
                away_ratio, occupancy_entity_id = await self._async_away_ratio_for_window(
                    recorder,
                    state_changes_during_period,
                    start,
                    end,
                )
                if away_ratio is not None and away_ratio >= 0.5:
                    skipped_away_days.append(
                        {
                            "days_ago": days_ago,
                            "away_ratio": round(away_ratio, 3),
                            "occupancy_entity": occupancy_entity_id,
                            "reason": "house_empty_for_most_of_window",
                        }
                    )
                    continue

                history = await recorder.async_add_executor_job(
                    state_changes_during_period,
                    self.hass,
                    start,
                    end,
                    entity_id,
                    True,
                    False,
                    None,
                    True,
                )
                result = self._demand_profile_from_history(history.get(entity_id, []), start, end)
                history_source = "estimated_house_demand"
                if result is None:
                    result = await self._async_raw_power_flow_history(recorder, state_changes_during_period, start, end)
                    history_source = "raw_power_flow"
                if result is None:
                    continue

                profile_buckets, duration_seconds, watt_seconds = result
                if duration_seconds < _RUNTIME_DEMAND_MIN_HISTORY.total_seconds():
                    continue

                average_w = watt_seconds / duration_seconds
                history_weight, history_weight_reasons = self._history_day_weight(
                    days_ago,
                    window_start_local,
                    local_now,
                )
                daily_results.append(
                    {
                        "days_ago": days_ago,
                        "profile_buckets": profile_buckets,
                        "duration_seconds": duration_seconds,
                        "watt_seconds": watt_seconds,
                        "average_w": round(average_w, 1),
                        "energy_kwh": round(watt_seconds / 3_600_000, 3),
                        "source": history_source,
                        "history_weight": history_weight,
                        "history_weight_reasons": history_weight_reasons,
                    }
                )

            refreshed_at = datetime.now(UTC)
            self._last_recorder_refresh = refreshed_at
            daily_averages, rejected_daily_averages = self._filter_runtime_history_days(daily_results)
            profile_totals: dict[int, dict[str, float]] = {}
            total_duration_seconds = 0.0
            total_watt_seconds = 0.0
            total_history_weight = 0.0
            for day in daily_averages:
                history_weight = max(0.0, float(day.get("history_weight", 1.0)))
                total_history_weight += history_weight
                total_duration_seconds += float(day["duration_seconds"]) * history_weight
                total_watt_seconds += float(day["watt_seconds"]) * history_weight
                for bucket_index, bucket in day["profile_buckets"].items():
                    profile_bucket = profile_totals.setdefault(
                        bucket_index,
                        {"duration_seconds": 0.0, "watt_seconds": 0.0},
                    )
                    profile_bucket["duration_seconds"] += bucket["duration_seconds"] * history_weight
                    profile_bucket["watt_seconds"] += bucket["watt_seconds"] * history_weight

            if total_duration_seconds <= 0:
                self._recorder_average_demand_w = None
                self._recorder_demand_profile = []
                self._recorder_history_attrs = {
                    "recorder_history_status": "warming",
                    "recorder_history_source_entity": entity_id,
                    "recorder_history_lookback_days": _RUNTIME_RECORDER_HISTORY_DAYS,
                    "recorder_history_window_hours": round(_RUNTIME_PROFILE_HORIZON.total_seconds() / 3600, 1),
                    "recorder_history_valid_days": 0,
                    "recorder_history_rejected_days": len(rejected_daily_averages),
                    "recorder_history_skipped_away_days": skipped_away_days,
                    "recorder_history_rejected_daily_averages": rejected_daily_averages,
                    "recorder_history_last_refresh": refreshed_at.isoformat(),
                    "recorder_history_profile_start": now.isoformat(),
                    "recorder_history_reason": "No plausible forward house-demand history is available yet",
                }
            else:
                average_w = total_watt_seconds / total_duration_seconds
                average_window_energy_kwh = (
                    total_watt_seconds / max(total_history_weight, 1.0) / 3_600_000
                )
                demand_profile = self._profile_from_bucket_totals(profile_totals)
                self._recorder_average_demand_w = average_w
                self._recorder_demand_profile = demand_profile
                self._recorder_history_attrs = {
                    "recorder_history_status": "ready",
                    "recorder_history_source_entity": entity_id,
                    "recorder_history_lookback_days": _RUNTIME_RECORDER_HISTORY_DAYS,
                    "recorder_history_window_hours": round(_RUNTIME_PROFILE_HORIZON.total_seconds() / 3600, 1),
                    "recorder_history_interval_minutes": round(_RUNTIME_PROFILE_INTERVAL.total_seconds() / 60, 1),
                    "recorder_history_valid_days": len(daily_averages),
                    "recorder_history_weighting": "recency_decay_with_same_weekday_boost",
                    "recorder_history_recency_decay": _RUNTIME_RECORDER_RECENCY_DECAY,
                    "recorder_history_min_day_weight": _RUNTIME_RECORDER_MIN_DAY_WEIGHT,
                    "recorder_history_same_weekday_boost": _RUNTIME_RECORDER_SAME_WEEKDAY_BOOST,
                    "recorder_history_total_weight": round(total_history_weight, 3),
                    "recorder_history_rejected_days": len(rejected_daily_averages),
                    "recorder_history_skipped_away_days": skipped_away_days,
                    "recorder_history_demand_w": round(average_w, 1),
                    "recorder_history_energy_kwh": round(average_window_energy_kwh, 3),
                    "recorder_history_profile_buckets": len(demand_profile),
                    "recorder_history_last_refresh": refreshed_at.isoformat(),
                    "recorder_history_profile_start": now.isoformat(),
                    "recorder_history_daily_averages": self._summarise_runtime_days(daily_averages),
                    "recorder_history_rejected_daily_averages": rejected_daily_averages,
                    "recorder_history_note": "Runtime projects through a 24-hour house-demand profile and assumes no new solar generation.",
                }
        except Exception as exc:  # pragma: no cover - depends on recorder availability.
            self._last_recorder_refresh = datetime.now(UTC)
            _LOGGER.debug("Could not refresh runtime recorder history for %s: %s", entity_id, exc)
            if self._recorder_average_demand_w is None:
                self._recorder_history_attrs = {
                    "recorder_history_status": "unavailable",
                    "recorder_history_source_entity": entity_id,
                    "recorder_history_lookback_days": _RUNTIME_RECORDER_HISTORY_DAYS,
                    "recorder_history_window_hours": round(_RUNTIME_PROFILE_HORIZON.total_seconds() / 3600, 1),
                    "recorder_history_skipped_away_days": [],
                    "recorder_history_reason": str(exc),
                }
        finally:
            self._recorder_refresh_in_progress = False
            try:
                self.async_write_ha_state()
            except RuntimeError:
                pass

    @staticmethod
    def _history_day_weight(
        days_ago: int,
        window_start_local: datetime,
        target_local: datetime,
    ) -> tuple[float, list[str]]:
        days_back = max(0, days_ago - 1)
        weight = max(
            _RUNTIME_RECORDER_MIN_DAY_WEIGHT,
            _RUNTIME_RECORDER_RECENCY_DECAY**days_back,
        )
        reasons = ["recency_weighted"]
        if window_start_local.weekday() == target_local.weekday():
            weight *= _RUNTIME_RECORDER_SAME_WEEKDAY_BOOST
            reasons.append("same_weekday_boost")
        return round(weight, 3), reasons

    @staticmethod
    def _filter_runtime_history_days(
        daily_results: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if not daily_results:
            return [], []

        average_values = [float(day["average_w"]) for day in daily_results if float(day["average_w"]) > 0]
        if not average_values:
            return [], [
                {
                    "days_ago": day["days_ago"],
                    "average_w": day["average_w"],
                    "energy_kwh": day["energy_kwh"],
                    "source": day["source"],
                    "reason": "zero_or_missing_average",
                }
                for day in daily_results
            ]

        median_average_w = median(average_values)
        minimum_average_w = max(
            _RUNTIME_MIN_VALID_DAILY_AVERAGE_W,
            median_average_w * _RUNTIME_MIN_VALID_DAY_MEDIAN_FACTOR,
        )
        selected: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for day in daily_results:
            average_w = float(day["average_w"])
            summary = {
                "days_ago": day["days_ago"],
                "average_w": day["average_w"],
                "energy_kwh": day["energy_kwh"],
                "source": day["source"],
                "history_weight": day.get("history_weight"),
                "history_weight_reasons": day.get("history_weight_reasons"),
            }
            if average_w < minimum_average_w:
                rejected.append(
                    {
                        **summary,
                        "reason": "implausibly_low_average",
                        "minimum_average_w": round(minimum_average_w, 1),
                    }
                )
                continue
            selected.append(day)

        return selected, rejected

    @staticmethod
    def _summarise_runtime_days(daily_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "days_ago": day["days_ago"],
                "average_w": day["average_w"],
                "energy_kwh": day["energy_kwh"],
                "source": day["source"],
                "history_weight": day.get("history_weight"),
                "history_weight_reasons": day.get("history_weight_reasons"),
            }
            for day in daily_results
        ]

    def _estimated_house_demand_entity_id(self) -> str:
        registry = er.async_get(self.hass)
        entity_id = registry.async_get_entity_id(
            "sensor",
            DOMAIN,
            f"{self._config_entry.entry_id}_estimated_house_demand",
        )
        return entity_id or _ESTIMATED_HOUSE_DEMAND_ENTITY_FALLBACK

    async def _async_away_ratio_for_window(
        self,
        recorder: Any,
        state_changes_during_period: Any,
        start: datetime,
        end: datetime,
    ) -> tuple[float | None, str]:
        try:
            history = await recorder.async_add_executor_job(
                state_changes_during_period,
                self.hass,
                start,
                end,
                _HOUSE_OCCUPANCY_ENTITY,
                True,
                False,
                None,
                True,
            )
        except Exception as exc:
            _LOGGER.debug(
                "Could not read home occupancy history for %s: %s",
                _HOUSE_OCCUPANCY_ENTITY,
                exc,
            )
            return None, _HOUSE_OCCUPANCY_ENTITY
        return (
            self._away_ratio_from_history(history.get(_HOUSE_OCCUPANCY_ENTITY, []), start, end),
            _HOUSE_OCCUPANCY_ENTITY,
        )

    @staticmethod
    def _away_ratio_from_history(states: list[Any], start: datetime, end: datetime) -> float | None:
        samples: list[tuple[datetime, str]] = []
        for state in states:
            if state.state in ("unknown", "unavailable"):
                continue
            sample_time = state.last_updated.astimezone(UTC)
            if sample_time < start:
                sample_time = start
            if sample_time > end:
                continue
            samples.append((sample_time, state.state))

        if not samples:
            return None

        samples.sort(key=lambda item: item[0])
        if samples[0][0] > start:
            samples.insert(0, (start, samples[0][1]))
        if samples[-1][0] < end:
            samples.append((end, samples[-1][1]))

        total_seconds = max(0.0, (end - start).total_seconds())
        if total_seconds <= 0:
            return None

        away_seconds = 0.0
        for index in range(len(samples) - 1):
            current_time, current_state = samples[index]
            next_time, _next_state = samples[index + 1]
            house_empty, _occupants = _house_empty_from_state(current_state)
            if house_empty:
                away_seconds += max(0.0, (next_time - current_time).total_seconds())

        return away_seconds / total_seconds

    async def _async_raw_power_flow_history(
        self,
        recorder: Any,
        state_changes_during_period: Any,
        start: datetime,
        end: datetime,
    ) -> tuple[dict[int, dict[str, float]], float, float] | None:
        histories: dict[str, list[Any]] = {}
        for key, entity_id in self._raw_power_history_entity_ids().items():
            history = await recorder.async_add_executor_job(
                state_changes_during_period,
                self.hass,
                start,
                end,
                entity_id,
                False,
                False,
                None,
                True,
            )
            histories[key] = history.get(entity_id, [])

        return self._demand_profile_from_power_histories(histories, start, end)

    def _raw_power_history_entity_ids(self) -> dict[str, str]:
        return {
            "pv_power": self._history_entity_id("pv_power", _PV_POWER_ENTITY_FALLBACK),
            "pv_charging_power": self._history_entity_id("pv_charging_power", _PV_CHARGING_POWER_ENTITY_FALLBACK),
            "ac_charging_power": self._history_entity_id("ac_charging_power", _AC_CHARGING_POWER_ENTITY_FALLBACK),
            "total_charge_power": self._history_entity_id("total_charge_power", _TOTAL_CHARGE_POWER_ENTITY_FALLBACK),
            "battery_discharging_power": self._history_entity_id(
                "battery_discharging_power",
                _BATTERY_DISCHARGING_POWER_ENTITY_FALLBACK,
            ),
            "total_battery_output_power": self._history_entity_id(
                "total_battery_output_power",
                _TOTAL_BATTERY_OUTPUT_POWER_ENTITY_FALLBACK,
            ),
            "grid_power": self._history_entity_id("grid_power", _GRID_METER_POWER_ENTITY_FALLBACK),
        }

    def _history_entity_id(self, unique_key: str, fallback: str) -> str:
        registry = er.async_get(self.hass)
        entity_id = registry.async_get_entity_id(
            "sensor",
            DOMAIN,
            f"{self._config_entry.entry_id}_{unique_key}",
        )
        return entity_id or fallback

    @classmethod
    def _demand_profile_from_history(
        cls,
        states: list[Any],
        start: datetime,
        end: datetime,
    ) -> tuple[dict[int, dict[str, float]], float, float] | None:
        points: list[tuple[datetime, float]] = []
        for state in states:
            if state.state in ("unknown", "unavailable"):
                continue
            value = _as_float(state.state)
            if value is None or value < 0:
                continue
            points.append((state.last_updated.astimezone(UTC), value))

        if not points:
            return None

        points.sort(key=lambda item: item[0])
        coverage_grace = _RUNTIME_PROFILE_INTERVAL * 2
        if points[0][0] > start + coverage_grace or points[-1][0] < end - coverage_grace:
            return None

        starting_value: float | None = None
        effective_samples: list[tuple[datetime, float]] = []
        for sample_time, watts in points:
            if sample_time <= start:
                starting_value = watts
            elif sample_time < end:
                effective_samples.append((sample_time, watts))

        if starting_value is not None:
            effective_samples.insert(0, (start, starting_value))
        elif effective_samples:
            effective_samples.insert(0, (start, effective_samples[0][1]))
        else:
            return None

        if effective_samples[-1][0] < end:
            effective_samples.append((end, effective_samples[-1][1]))

        buckets = cls._empty_profile_buckets(start, end)
        for index in range(1, len(effective_samples)):
            previous_time, previous_watts = effective_samples[index - 1]
            current_time, _current_watts = effective_samples[index]
            cls._add_profile_segment(buckets, start, end, previous_time, current_time, previous_watts)

        return cls._profile_totals(buckets)

    @staticmethod
    def _empty_profile_buckets(start: datetime, end: datetime) -> dict[int, dict[str, float]]:
        interval_seconds = _RUNTIME_PROFILE_INTERVAL.total_seconds()
        total_seconds = max(interval_seconds, (end - start).total_seconds())
        bucket_count = max(1, int((total_seconds + interval_seconds - 1) // interval_seconds))
        return {
            bucket_index: {"duration_seconds": 0.0, "watt_seconds": 0.0}
            for bucket_index in range(bucket_count)
        }

    @classmethod
    def _add_profile_segment(
        cls,
        buckets: dict[int, dict[str, float]],
        start: datetime,
        end: datetime,
        segment_start: datetime,
        segment_end: datetime,
        watts: float,
    ) -> None:
        current = max(start, segment_start)
        segment_end = min(end, segment_end)
        interval_seconds = _RUNTIME_PROFILE_INTERVAL.total_seconds()

        while current < segment_end:
            offset_seconds = max(0.0, (current - start).total_seconds())
            bucket_index = int(offset_seconds // interval_seconds)
            if bucket_index not in buckets:
                break

            bucket_end = min(
                segment_end,
                start + timedelta(seconds=(bucket_index + 1) * interval_seconds),
            )
            duration_seconds = max(0.0, (bucket_end - current).total_seconds())
            buckets[bucket_index]["duration_seconds"] += duration_seconds
            buckets[bucket_index]["watt_seconds"] += watts * duration_seconds
            current = bucket_end

    @staticmethod
    def _profile_totals(
        buckets: dict[int, dict[str, float]],
    ) -> tuple[dict[int, dict[str, float]], float, float] | None:
        active_buckets = {
            bucket_index: bucket
            for bucket_index, bucket in buckets.items()
            if bucket["duration_seconds"] > 0
        }
        total_duration_seconds = sum(bucket["duration_seconds"] for bucket in active_buckets.values())
        total_watt_seconds = sum(bucket["watt_seconds"] for bucket in active_buckets.values())
        if total_duration_seconds <= 0:
            return None
        return active_buckets, total_duration_seconds, total_watt_seconds

    @staticmethod
    def _profile_from_bucket_totals(profile_totals: dict[int, dict[str, float]]) -> list[dict[str, Any]]:
        interval_seconds = _RUNTIME_PROFILE_INTERVAL.total_seconds()
        profile: list[dict[str, Any]] = []
        for bucket_index, bucket in sorted(profile_totals.items()):
            duration_seconds = bucket["duration_seconds"]
            if duration_seconds <= 0:
                continue
            profile.append(
                {
                    "bucket": bucket_index,
                    "offset_minutes": round(bucket_index * interval_seconds / 60, 1),
                    "duration_minutes": round(interval_seconds / 60, 1),
                    "average_w": round(bucket["watt_seconds"] / duration_seconds, 1),
                    "sample_days": round(duration_seconds / interval_seconds, 1),
                }
            )
        return profile

    @staticmethod
    def _weighted_demand_from_history(
        states: list[Any],
        start: datetime,
        end: datetime,
    ) -> tuple[float, float] | None:
        points: list[tuple[datetime, float]] = []
        for state in states:
            if state.state in ("unknown", "unavailable"):
                continue
            value = _as_float(state.state)
            if value is None or value < 0:
                continue
            points.append((state.last_updated.astimezone(UTC), value))

        if not points:
            return None

        points.sort(key=lambda item: item[0])
        starting_value: float | None = None
        effective_samples: list[tuple[datetime, float]] = []
        for sample_time, watts in points:
            if sample_time <= start:
                starting_value = watts
            elif sample_time < end:
                effective_samples.append((sample_time, watts))

        if starting_value is not None:
            effective_samples.insert(0, (start, starting_value))
        elif effective_samples:
            effective_samples.insert(0, (start, effective_samples[0][1]))
        else:
            return None

        if effective_samples[-1][0] < end:
            effective_samples.append((end, effective_samples[-1][1]))

        duration_seconds = (effective_samples[-1][0] - effective_samples[0][0]).total_seconds()
        if duration_seconds <= 0:
            return None

        watt_seconds = 0.0
        for index in range(1, len(effective_samples)):
            previous_time, previous_watts = effective_samples[index - 1]
            current_time, _current_watts = effective_samples[index]
            interval_seconds = max(0.0, (current_time - previous_time).total_seconds())
            watt_seconds += previous_watts * interval_seconds

        return duration_seconds, watt_seconds

    @classmethod
    def _demand_profile_from_power_histories(
        cls,
        histories: dict[str, list[Any]],
        start: datetime,
        end: datetime,
    ) -> tuple[dict[int, dict[str, float]], float, float] | None:
        series = {
            key: cls._normalised_power_history(states, start, end, allow_negative=key == "grid_power")
            for key, states in histories.items()
        }
        if not any(series.values()):
            return None

        timeline = {start, end}
        interval_seconds = _RUNTIME_PROFILE_INTERVAL.total_seconds()
        bucket_count = len(cls._empty_profile_buckets(start, end))
        for bucket_index in range(1, bucket_count):
            timeline.add(start + timedelta(seconds=bucket_index * interval_seconds))
        for points in series.values():
            timeline.update(sample_time for sample_time, _watts in points if start <= sample_time <= end)
        ordered_times = sorted(timeline)
        if len(ordered_times) < 2:
            return None

        values = {key: 0.0 for key in histories}
        indexes = {key: 0 for key in histories}
        buckets = cls._empty_profile_buckets(start, end)

        for index in range(len(ordered_times) - 1):
            current_time = ordered_times[index]
            next_time = ordered_times[index + 1]
            for key, points in series.items():
                point_index = indexes[key]
                while point_index < len(points) and points[point_index][0] <= current_time:
                    values[key] = points[point_index][1]
                    point_index += 1
                indexes[key] = point_index

            cls._add_profile_segment(
                buckets,
                start,
                end,
                current_time,
                next_time,
                cls._demand_from_power_values(values),
            )

        return cls._profile_totals(buckets)

    @classmethod
    def _weighted_demand_from_power_histories(
        cls,
        histories: dict[str, list[Any]],
        start: datetime,
        end: datetime,
    ) -> tuple[float, float] | None:
        series = {
            key: cls._normalised_power_history(states, start, end, allow_negative=key == "grid_power")
            for key, states in histories.items()
        }
        if not any(series.values()):
            return None

        timeline = {start, end}
        for points in series.values():
            timeline.update(sample_time for sample_time, _watts in points if start <= sample_time <= end)
        ordered_times = sorted(timeline)
        if len(ordered_times) < 2:
            return None

        values = {key: 0.0 for key in histories}
        indexes = {key: 0 for key in histories}
        watt_seconds = 0.0

        for index in range(len(ordered_times) - 1):
            current_time = ordered_times[index]
            next_time = ordered_times[index + 1]
            for key, points in series.items():
                point_index = indexes[key]
                while point_index < len(points) and points[point_index][0] <= current_time:
                    values[key] = points[point_index][1]
                    point_index += 1
                indexes[key] = point_index

            interval_seconds = max(0.0, (next_time - current_time).total_seconds())
            watt_seconds += cls._demand_from_power_values(values) * interval_seconds

        duration_seconds = (ordered_times[-1] - ordered_times[0]).total_seconds()
        if duration_seconds <= 0:
            return None
        return duration_seconds, watt_seconds

    @staticmethod
    def _normalised_power_history(
        states: list[Any],
        start: datetime,
        end: datetime,
        *,
        allow_negative: bool = False,
    ) -> list[tuple[datetime, float]]:
        points: list[tuple[datetime, float]] = []
        starting_value: float | None = None
        for state in states:
            if state.state in ("unknown", "unavailable"):
                continue
            value = _as_float(state.state)
            if value is None:
                continue

            unit = (state.attributes.get("unit_of_measurement") or "").lower()
            if unit == "kw":
                value *= 1000

            if not allow_negative:
                value = max(0.0, value)
            sample_time = state.last_updated.astimezone(UTC)
            if sample_time <= start:
                starting_value = value
            elif sample_time < end:
                points.append((sample_time, value))

        if starting_value is not None:
            points.insert(0, (start, starting_value))
        elif points:
            points.insert(0, (start, points[0][1]))

        if points and points[-1][0] < end:
            points.append((end, points[-1][1]))

        return sorted(points, key=lambda item: item[0])

    @staticmethod
    def _demand_from_power_values(values: dict[str, float]) -> float:
        total_charge_w = values.get("total_charge_power", 0.0)
        pv_charging_w = values.get("pv_charging_power", 0.0)
        ac_charging_w = values.get("ac_charging_power", 0.0)
        if total_charge_w > 0:
            charge_w = total_charge_w
        elif pv_charging_w > 0 or ac_charging_w > 0:
            charge_w = pv_charging_w + ac_charging_w
        else:
            charge_w = 0.0

        discharge_w = max(
            values.get("total_battery_output_power", 0.0),
            values.get("battery_discharging_power", 0.0),
        )
        grid_power = values.get("grid_power")
        if grid_power is not None:
            grid_import_w = max(0.0, grid_power)
            grid_export_w = max(0.0, -grid_power)
        else:
            grid_import_w = values.get("grid_import", 0.0)
            grid_export_w = values.get("grid_export", 0.0)

        raw_demand_w = (
            values.get("pv_power", 0.0)
            + grid_import_w
            + discharge_w
            - charge_w
            - grid_export_w
        )
        return max(0.0, raw_demand_w)

    def _runtime_from_demand_profile(
        self,
        usable_energy_kwh: float,
        fallback_demand_w: float,
    ) -> tuple[timedelta, dict[str, Any]] | None:
        if not self._recorder_demand_profile:
            return None

        interval_seconds = _RUNTIME_PROFILE_INTERVAL.total_seconds()
        profile_by_bucket = {
            int(entry["bucket"]): float(entry["average_w"])
            for entry in self._recorder_demand_profile
        }
        bucket_count = max(
            1,
            int(_RUNTIME_PROFILE_HORIZON.total_seconds() // interval_seconds),
            max(profile_by_bucket, default=0) + 1,
        )
        default_demand_w = fallback_demand_w
        if default_demand_w <= 20 and self._recorder_average_demand_w is not None:
            default_demand_w = self._recorder_average_demand_w
        if default_demand_w <= 20:
            return None

        first_cycle_energy_kwh = 0.0
        for bucket_index in range(bucket_count):
            demand_w = max(profile_by_bucket.get(bucket_index, default_demand_w), default_demand_w)
            first_cycle_energy_kwh += max(0.0, demand_w) * interval_seconds / 3_600_000

        energy_remaining_kwh = usable_energy_kwh
        elapsed_seconds = 0.0
        fallback_bucket_count = 0
        profile_bucket_count = 0

        for cycle in range(_RUNTIME_PROFILE_MAX_CYCLES):
            for bucket_index in range(bucket_count):
                demand_w = profile_by_bucket.get(bucket_index)
                if demand_w is None:
                    demand_w = default_demand_w
                    fallback_bucket_count += 1
                else:
                    demand_w = max(demand_w, default_demand_w)
                    profile_bucket_count += 1

                if demand_w <= 20:
                    elapsed_seconds += interval_seconds
                    continue

                segment_kwh = demand_w * interval_seconds / 3_600_000
                if segment_kwh >= energy_remaining_kwh:
                    seconds_into_segment = energy_remaining_kwh * 3_600_000 / demand_w
                    runtime = timedelta(seconds=elapsed_seconds + seconds_into_segment)
                    return runtime, {
                        "demand_basis": "forward_time_of_day_history",
                        "runtime_projection_horizon_hours": round(_RUNTIME_PROFILE_HORIZON.total_seconds() / 3600, 1),
                        "runtime_projection_interval_minutes": round(interval_seconds / 60, 1),
                        "runtime_projection_profile_buckets": len(profile_by_bucket),
                        "runtime_projection_cycles_used": cycle + 1,
                        "runtime_projection_fallback_buckets_used": fallback_bucket_count,
                        "runtime_projection_profile_buckets_used": profile_bucket_count,
                        "runtime_projection_first_cycle_energy_kwh": round(first_cycle_energy_kwh, 3),
                        "runtime_projection_min_demand_floor_w": round(default_demand_w, 1),
                        "runtime_projection_average_demand_w": round(
                            first_cycle_energy_kwh * 3_600_000 / (bucket_count * interval_seconds),
                            1,
                        ),
                    }

                energy_remaining_kwh -= segment_kwh
                elapsed_seconds += interval_seconds

        return None

    def _calculate_runtime(self) -> tuple[str, dict[str, Any]]:
        now = datetime.now(UTC)
        soc = _as_float(self.coordinator.get_value("battery_soc"))
        capacity_kwh = _as_float(getattr(self.coordinator, "battery_capacity_kwh", 0.0), 0.0)
        reserve_soc = _as_float(getattr(self.coordinator, "_commanded_min_soc", 10), 10.0)
        live_house_demand_w, house_attrs = _estimate_house_demand_w(self.hass, self.coordinator)
        house_demand_w, demand_history_attrs = self._historical_average_demand_w(now, live_house_demand_w)

        attrs: dict[str, Any] = {
            "calculated_at": now.isoformat(),
            "battery_capacity_kwh": round(capacity_kwh, 3),
            "reserve_soc": reserve_soc,
            "estimated_house_demand_w": round(house_demand_w, 1),
            "live_house_demand_w": round(live_house_demand_w, 1),
            "house_demand": house_attrs,
            **demand_history_attrs,
        }

        if soc is None or capacity_kwh <= 0:
            attrs["status"] = "missing_data"
            attrs["reason"] = "Battery SOC or capacity is unavailable"
            return "Unknown", attrs

        usable_soc = max(0.0, soc - reserve_soc)
        usable_energy_kwh = capacity_kwh * usable_soc / 100.0
        attrs.update(
            {
                "current_soc": round(soc, 1),
                "usable_soc_to_reserve": round(usable_soc, 1),
                "usable_energy_to_reserve_kwh": round(usable_energy_kwh, 2),
            }
        )

        if usable_energy_kwh <= 0.05:
            attrs["status"] = "at_reserve"
            return "At reserve", attrs

        if house_demand_w <= 20:
            attrs["status"] = "no_load"
            return "No demand", attrs

        profile_runtime = self._runtime_from_demand_profile(usable_energy_kwh, house_demand_w)
        if profile_runtime is not None:
            runtime, profile_attrs = profile_runtime
            attrs.update(profile_attrs)
            attrs["estimated_house_demand_w"] = profile_attrs.get(
                "runtime_projection_average_demand_w",
                attrs["estimated_house_demand_w"],
            )
            attrs["status"] = "estimated"
            attrs["hours_to_reserve"] = round(runtime.total_seconds() / 3600, 2)
            attrs["estimated_reserve_at"] = (now + runtime).isoformat()
            return AeccEstimatedChargeTimeSensor._format_duration(runtime), attrs

        runtime = timedelta(hours=usable_energy_kwh / (house_demand_w / 1000))
        attrs["status"] = "estimated"
        attrs["hours_to_reserve"] = round(runtime.total_seconds() / 3600, 2)
        attrs["estimated_reserve_at"] = (now + runtime).isoformat()
        return AeccEstimatedChargeTimeSensor._format_duration(runtime), attrs


class AeccRecommendedOvernightSocSensor(AeccRuntimeAtCurrentHouseDemandSensor, RestoreEntity):
    """Recommended overnight charge target from demand history and solar forecast timing."""

    _attr_has_entity_name = True
    _attr_name = "Recommended Overnight SOC"
    _attr_icon = "mdi:battery-charging-80"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: AeccBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_recommended_overnight_soc"
        self._forecast_cache_mtime: float | None = None
        self._forecast_cache_path: str | None = None
        self._forecast_cache: list[dict[str, Any]] = []
        self._previous_recommended_soc: int | None = None
        self._previous_recommendation_date: str | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is None:
            return
        restored_soc = _as_float(last_state.state)
        if restored_soc is not None:
            self._previous_recommended_soc = int(round(restored_soc))
        restored_date = last_state.attributes.get("recommendation_local_date")
        if isinstance(restored_date, str):
            self._previous_recommendation_date = restored_date

    @property
    def native_value(self) -> int | None:
        self._record_house_demand_sample()
        state, attrs = self._calculate_recommendation()
        self._last_attributes = attrs
        return state

    @property
    def available(self) -> bool:
        return True

    @property
    def icon(self) -> str:
        status = self._last_attributes.get("status")
        if status == "full_capacity_recommended":
            return "mdi:battery-alert"
        if status == "estimated":
            return "mdi:battery-charging-80"
        return "mdi:battery-clock"

    def _calculate_recommendation(self) -> tuple[int | None, dict[str, Any]]:
        now = datetime.now(UTC)
        self._schedule_recorder_history_refresh(now)

        soc = _as_float(self.coordinator.get_value("battery_soc"))
        capacity_kwh = _as_float(getattr(self.coordinator, "battery_capacity_kwh", 0.0), 0.0) or 0.0
        reserve_soc = _as_float(getattr(self.coordinator, "_commanded_min_soc", 10), 10.0) or 10.0
        start, end = self._next_peak_window(now)
        off_peak_start, off_peak_end, tariff_preset = self._off_peak_window_options()
        fallback_daily_kwh, fallback_attrs = self._fallback_daily_demand_kwh()
        fallback_demand_w = fallback_daily_kwh * 1000 / 24
        recorder_history_attrs = self._current_recorder_history_attrs(now)
        solar_unavailable = self._solar_unavailable_override()
        solar_unavailable_entity = self._solar_availability_entity_id()

        attrs: dict[str, Any] = {
            "calculated_at": now.isoformat(),
            "recommendation_local_date": now.astimezone().date().isoformat(),
            "tariff_preset": tariff_preset,
            "off_peak_start": off_peak_start,
            "off_peak_end": off_peak_end,
            "target_window_start": start.isoformat(),
            "target_window_end": end.isoformat(),
            "battery_capacity_kwh": round(capacity_kwh, 3),
            "current_soc": round(soc, 1) if soc is not None else None,
            "reserve_soc": round(reserve_soc, 1),
            "solcast_tomorrow_kwh": self._state_energy_kwh(_SOLCAST_TOMORROW_ENTITY),
            "solar_unavailable_override": solar_unavailable,
            "solar_unavailable_entity": solar_unavailable_entity,
            "solar_override_status": "Batteries Only" if solar_unavailable else "Solar forecast active",
            **fallback_attrs,
            **recorder_history_attrs,
        }

        if capacity_kwh <= 0:
            attrs["status"] = "missing_data"
            attrs["reason"] = "Battery capacity is unavailable"
            return None, attrs

        projection = self._project_peak_window(start, end, now, fallback_demand_w, solar_unavailable)
        attrs.update(projection)
        forecast_health_attrs = self._solar_forecast_health_attrs(projection, now)
        confidence_adjustment_soc, confidence_attrs = self._confidence_adjustment_soc(
            projection,
            recorder_history_attrs,
            forecast_health_attrs,
        )
        stale_guard_min_soc, stale_guard_attrs = self._stale_data_guard_attrs(
            fallback_attrs,
            recorder_history_attrs,
            forecast_health_attrs,
        )

        buffer_soc, buffer_attrs = self._dynamic_buffer_soc(
            capacity_kwh,
            projection,
            fallback_attrs,
            recorder_history_attrs,
        )
        buffer_kwh = capacity_kwh * buffer_soc / 100
        reserve_kwh = capacity_kwh * reserve_soc / 100
        usable_capacity_kwh = capacity_kwh * max(0.0, _FULL_SOC - reserve_soc) / 100
        required_ac_kwh = max(0.0, float(projection["required_start_energy_kwh"]))
        required_battery_kwh = required_ac_kwh / _OVERNIGHT_DISCHARGE_EFFICIENCY
        loss_allowance_kwh = max(0.0, required_battery_kwh - required_ac_kwh)
        confidence_adjustment_kwh = capacity_kwh * confidence_adjustment_soc / 100
        required_usable_kwh = required_battery_kwh + buffer_kwh + confidence_adjustment_kwh
        uncovered_shortfall_kwh = max(0.0, required_usable_kwh - usable_capacity_kwh)
        required_usable_kwh = min(required_usable_kwh, usable_capacity_kwh)

        raw_target_soc = reserve_soc + (required_usable_kwh / capacity_kwh * 100)
        minimum_target_soc = min(_FULL_SOC, reserve_soc + buffer_soc + confidence_adjustment_soc)
        rounded_target_soc = self._round_soc_up(raw_target_soc, 1)
        rounded_target_soc = int(min(_FULL_SOC, max(minimum_target_soc, rounded_target_soc)))
        target_soc_before_guard = rounded_target_soc
        if stale_guard_min_soc is not None:
            rounded_target_soc = int(min(_FULL_SOC, max(float(stale_guard_min_soc), rounded_target_soc)))

        stored_charge_needed_kwh = None
        estimated_grid_charge_energy_kwh = None
        if soc is not None:
            stored_charge_needed_kwh = capacity_kwh * max(0.0, rounded_target_soc - soc) / 100
            estimated_grid_charge_energy_kwh = stored_charge_needed_kwh / _OVERNIGHT_GRID_CHARGE_EFFICIENCY

        jump_attrs = self._target_jump_guard_attrs(rounded_target_soc, now)
        reason = self._recommendation_reason(
            rounded_target_soc,
            required_ac_kwh,
            loss_allowance_kwh,
            buffer_kwh,
            projection,
            buffer_attrs,
            estimated_grid_charge_energy_kwh,
        )
        target_breakdown_attrs = self._target_breakdown_attrs(
            rounded_target_soc,
            reserve_soc,
            capacity_kwh,
            required_ac_kwh,
            required_battery_kwh,
            loss_allowance_kwh,
            buffer_kwh,
            confidence_adjustment_kwh,
            projection,
            buffer_attrs,
            confidence_attrs,
            stale_guard_attrs,
        )

        attrs.update(
            {
                **forecast_health_attrs,
                **confidence_attrs,
                **stale_guard_attrs,
                **buffer_attrs,
                **target_breakdown_attrs,
                "reserve_energy_kwh": round(reserve_kwh, 3),
                "buffer_energy_kwh": round(buffer_kwh, 3),
                "confidence_adjustment_energy_kwh": round(confidence_adjustment_kwh, 3),
                "usable_capacity_above_reserve_kwh": round(usable_capacity_kwh, 3),
                "required_ac_energy_kwh": round(required_ac_kwh, 3),
                "battery_discharge_efficiency": _OVERNIGHT_DISCHARGE_EFFICIENCY,
                "grid_charge_efficiency": _OVERNIGHT_GRID_CHARGE_EFFICIENCY,
                "battery_loss_allowance_kwh": round(loss_allowance_kwh, 3),
                "required_battery_energy_before_buffer_kwh": round(required_battery_kwh, 3),
                "required_usable_energy_before_rounding_kwh": round(required_usable_kwh, 3),
                "uncovered_shortfall_kwh": round(uncovered_shortfall_kwh, 3),
                "stored_charge_needed_to_target_kwh": (
                    round(stored_charge_needed_kwh, 3) if stored_charge_needed_kwh is not None else None
                ),
                "estimated_grid_charge_energy_to_target_kwh": (
                    round(estimated_grid_charge_energy_kwh, 3)
                    if estimated_grid_charge_energy_kwh is not None
                    else None
                ),
                "target_soc_before_rounding": round(raw_target_soc, 1),
                "target_soc_before_stale_data_guard": target_soc_before_guard,
                "target_soc_rounding_step": 1,
                "minimum_target_soc": round(minimum_target_soc, 1),
                "recommended_soc": rounded_target_soc,
                "recommendation_reason": reason,
                "status": "full_capacity_recommended" if rounded_target_soc >= 100 else "estimated",
                **jump_attrs,
                "note": (
                    f"Recommendation covers the peak-rate window after {off_peak_end}, subtracts expected solar "
                    "by forecast period, uses confidence and stale-data guards, and allows for battery losses."
                ),
            }
        )
        self._previous_recommended_soc = rounded_target_soc
        self._previous_recommendation_date = attrs["recommendation_local_date"]
        return rounded_target_soc, attrs

    def _dynamic_buffer_soc(
        self,
        capacity_kwh: float,
        projection: dict[str, Any],
        fallback_attrs: dict[str, Any],
        recorder_history_attrs: dict[str, Any],
    ) -> tuple[float, dict[str, Any]]:
        house_empty = bool(fallback_attrs.get("house_empty_mode"))
        buffer_soc = (
            _OVERNIGHT_EMPTY_HOUSE_BASE_BUFFER_SOC
            if house_empty
            else _OVERNIGHT_OCCUPIED_BASE_BUFFER_SOC
        )
        reasons = ["empty_house_base" if house_empty else "occupied_house_base"]

        valid_days = int(_as_float(recorder_history_attrs.get("recorder_history_valid_days"), 0) or 0)
        history_status = str(recorder_history_attrs.get("recorder_history_status", "unknown"))
        if history_status != "ready" or valid_days < 2:
            buffer_soc += 1
            reasons.append("limited_house_demand_history")

        if projection.get("solar_forecast_source") != "Solcast detailed forecast file":
            buffer_soc += 1
            reasons.append("daily_forecast_without_timed_solar")

        fallback_buckets = int(_as_float(projection.get("fallback_demand_buckets_used"), 0) or 0)
        profile_buckets = int(_as_float(projection.get("demand_profile_buckets_used"), 0) or 0)
        if fallback_buckets > profile_buckets:
            buffer_soc += 1
            reasons.append("time_of_day_demand_fallback")

        projected_solar_kwh = _as_float(projection.get("projected_peak_solar_kwh"), 0.0) or 0.0
        if projected_solar_kwh < _OVERNIGHT_LOW_SOLAR_KWH:
            buffer_soc += 1
            reasons.append("low_solar_forecast")

        pre_sunrise_need_kwh = _as_float(
            projection.get("pre_sunrise_need_kwh", projection.get("morning_pre_solar_shortfall_kwh")),
            0.0,
        ) or 0.0
        if pre_sunrise_need_kwh >= _OVERNIGHT_MORNING_SHORTFALL_BUFFER_KWH:
            buffer_soc += 1
            reasons.append("higher_pre_sunrise_need")

        maximum = _OVERNIGHT_EMPTY_HOUSE_MAX_BUFFER_SOC if house_empty else _OVERNIGHT_MAX_BUFFER_SOC
        buffer_soc = min(maximum, max(1.0, buffer_soc))
        return buffer_soc, {
            "buffer_soc": buffer_soc,
            "dynamic_buffer_soc": buffer_soc,
            "dynamic_buffer_reasons": reasons,
            "dynamic_buffer_max_soc": maximum,
            "dynamic_buffer_energy_kwh": round(capacity_kwh * buffer_soc / 100, 3),
        }

    def _solar_forecast_health_attrs(
        self,
        projection: dict[str, Any],
        now: datetime,
    ) -> dict[str, Any]:
        source = projection.get("solar_forecast_source")
        attrs: dict[str, Any] = {
            "solar_forecast_status": "missing",
            "solar_forecast_stale_after_hours": round(
                _OVERNIGHT_SOLCAST_STALE_AFTER.total_seconds() / 3600,
                1,
            ),
        }

        if source == "Solcast detailed forecast file":
            if self._forecast_cache_mtime is None:
                attrs["solar_forecast_status"] = "missing"
                attrs["solar_forecast_health_reason"] = "Solcast forecast file timestamp unavailable"
                return attrs

            updated_at = datetime.fromtimestamp(self._forecast_cache_mtime, UTC)
            age_hours = (now - updated_at).total_seconds() / 3600
            stale = age_hours > _OVERNIGHT_SOLCAST_STALE_AFTER.total_seconds() / 3600
            attrs.update(
                {
                    "solar_forecast_status": "stale" if stale else "fresh",
                    "solar_forecast_updated_at": updated_at.isoformat(),
                    "solar_forecast_age_hours": round(max(0.0, age_hours), 1),
                    "solar_forecast_health_reason": (
                        "Solcast detailed forecast file is stale"
                        if stale
                        else "Solcast detailed forecast file is fresh"
                    ),
                }
            )
            return attrs

        if source == _SOLCAST_TOMORROW_ENTITY:
            state = self.hass.states.get(_SOLCAST_TOMORROW_ENTITY)
            if state is None or state.state in ("unknown", "unavailable"):
                attrs["solar_forecast_status"] = "missing"
                attrs["solar_forecast_health_reason"] = "Solcast tomorrow sensor unavailable"
                return attrs

            updated_at = state.last_updated.astimezone(UTC)
            age_hours = (now - updated_at).total_seconds() / 3600
            stale = age_hours > _OVERNIGHT_SOLCAST_STALE_AFTER.total_seconds() / 3600
            attrs.update(
                {
                    "solar_forecast_status": "stale" if stale else "daily_sensor",
                    "solar_forecast_updated_at": updated_at.isoformat(),
                    "solar_forecast_age_hours": round(max(0.0, age_hours), 1),
                    "solar_forecast_health_reason": (
                        "Solcast tomorrow sensor is stale"
                        if stale
                        else "Using Solcast tomorrow sensor because no timed forecast file is available"
                    ),
                }
            )
            return attrs

        attrs["solar_forecast_health_reason"] = "No Solcast forecast source was found"
        return attrs

    @staticmethod
    def _confidence_adjustment_soc(
        projection: dict[str, Any],
        recorder_history_attrs: dict[str, Any],
        forecast_health_attrs: dict[str, Any],
    ) -> tuple[float, dict[str, Any]]:
        reasons: list[str] = []
        solar_status = str(forecast_health_attrs.get("solar_forecast_status", "missing"))
        history_status = str(recorder_history_attrs.get("recorder_history_status", "unknown"))
        valid_days = int(_as_float(recorder_history_attrs.get("recorder_history_valid_days"), 0) or 0)
        fallback_buckets = int(_as_float(projection.get("fallback_demand_buckets_used"), 0) or 0)
        profile_buckets = int(_as_float(projection.get("demand_profile_buckets_used"), 0) or 0)
        projected_solar_kwh = _as_float(projection.get("projected_peak_solar_kwh"), 0.0) or 0.0
        pre_sunrise_need_kwh = _as_float(
            projection.get("pre_sunrise_need_kwh", projection.get("morning_pre_solar_shortfall_kwh")),
            0.0,
        ) or 0.0

        if solar_status in ("missing", "stale"):
            reasons.append(f"solar_forecast_{solar_status}")
        if history_status != "ready" or valid_days < 2:
            reasons.append("limited_house_demand_history")

        very_low_solar = projected_solar_kwh < 2.0
        weak_solar_with_morning_need = projected_solar_kwh < _OVERNIGHT_LOW_SOLAR_KWH and pre_sunrise_need_kwh >= 1.5
        if very_low_solar:
            reasons.append("very_low_solar_forecast")
        elif weak_solar_with_morning_need:
            reasons.append("low_solar_with_higher_pre_sunrise_need")

        if reasons:
            level = "low"
            adjustment = float(_OVERNIGHT_CONFIDENCE_LOW_ADJUSTMENT_SOC)
        else:
            caution_reasons: list[str] = []
            if solar_status != "fresh":
                caution_reasons.append("daily_forecast_only")
            if valid_days < 4:
                caution_reasons.append("short_house_demand_history")
            if fallback_buckets > profile_buckets:
                caution_reasons.append("time_of_day_profile_incomplete")
            if projected_solar_kwh < _OVERNIGHT_LOW_SOLAR_KWH:
                caution_reasons.append("low_solar_forecast")

            if caution_reasons:
                level = "caution"
                adjustment = float(_OVERNIGHT_CONFIDENCE_CAUTION_ADJUSTMENT_SOC)
                reasons = caution_reasons
            else:
                level = "normal"
                adjustment = 0.0
                reasons = ["fresh_timed_solar_and_good_history"]

        return adjustment, {
            "forecast_confidence": level,
            "forecast_confidence_adjustment_soc": adjustment,
            "forecast_confidence_reasons": reasons,
        }

    @staticmethod
    def _stale_data_guard_attrs(
        fallback_attrs: dict[str, Any],
        recorder_history_attrs: dict[str, Any],
        forecast_health_attrs: dict[str, Any],
    ) -> tuple[int | None, dict[str, Any]]:
        reasons: list[str] = []
        solar_status = str(forecast_health_attrs.get("solar_forecast_status", "missing"))
        history_status = str(recorder_history_attrs.get("recorder_history_status", "unknown"))
        valid_days = int(_as_float(recorder_history_attrs.get("recorder_history_valid_days"), 0) or 0)
        house_empty = bool(fallback_attrs.get("house_empty_mode"))

        if solar_status in ("missing", "stale"):
            reasons.append(f"solar_forecast_{solar_status}")
        if history_status != "ready" or valid_days < 2:
            reasons.append("limited_house_demand_history")

        min_soc = (
            _OVERNIGHT_EMPTY_HOUSE_STALE_DATA_MIN_SOC
            if house_empty
            else _OVERNIGHT_STALE_DATA_MIN_SOC
        )
        active = bool(reasons)
        return (
            min_soc if active else None,
            {
                "stale_data_guard_active": active,
                "stale_data_guard_min_soc": min_soc if active else None,
                "stale_data_guard_reasons": reasons,
                "stale_data_guard_note": (
                    "Applied a safer minimum target because forecast or demand history is weak."
                    if active
                    else None
                ),
            },
        )

    @staticmethod
    def _target_breakdown_attrs(
        target_soc: int,
        reserve_soc: float,
        capacity_kwh: float,
        required_ac_kwh: float,
        required_battery_kwh: float,
        loss_allowance_kwh: float,
        buffer_kwh: float,
        confidence_adjustment_kwh: float,
        projection: dict[str, Any],
        buffer_attrs: dict[str, Any],
        confidence_attrs: dict[str, Any],
        stale_guard_attrs: dict[str, Any],
    ) -> dict[str, Any]:
        projected_house_kwh = _as_float(projection.get("projected_peak_house_demand_kwh"), 0.0) or 0.0
        projected_solar_kwh = _as_float(projection.get("projected_peak_solar_kwh"), 0.0) or 0.0
        pre_sunrise_need_kwh = _as_float(
            projection.get("pre_sunrise_need_kwh", projection.get("morning_pre_solar_shortfall_kwh")),
            0.0,
        ) or 0.0
        pre_sunrise_net_need_kwh = _as_float(projection.get("pre_sunrise_net_need_kwh"), 0.0) or 0.0
        pre_sunrise_credited_solar_kwh = (
            _as_float(projection.get("pre_sunrise_credited_solar_kwh"), 0.0) or 0.0
        )
        buffer_soc = _as_float(buffer_attrs.get("dynamic_buffer_soc"), 0.0) or 0.0
        confidence_soc = _as_float(confidence_attrs.get("forecast_confidence_adjustment_soc"), 0.0) or 0.0

        breakdown = {
            "target_soc": target_soc,
            "reserve_soc": round(reserve_soc, 1),
            "battery_capacity_kwh": round(capacity_kwh, 3),
            "projected_house_demand_kwh": round(projected_house_kwh, 3),
            "projected_solar_kwh": round(projected_solar_kwh, 3),
            "pre_sunrise_need_kwh": round(pre_sunrise_need_kwh, 3),
            "pre_sunrise_net_need_kwh": round(pre_sunrise_net_need_kwh, 3),
            "pre_sunrise_credited_solar_kwh": round(pre_sunrise_credited_solar_kwh, 3),
            "pre_sunrise_solar_credit_factor": projection.get("pre_sunrise_solar_credit_factor"),
            "no_useful_solar_forecast": projection.get("no_useful_solar_forecast"),
            "solar_credit_mode": projection.get("solar_credit_mode"),
            "solar_unavailable_override": projection.get("solar_unavailable_override"),
            "solar_override_status": projection.get("solar_override_status"),
            "peak_window_need_kwh": round(required_ac_kwh, 3),
            "battery_energy_before_buffer_kwh": round(required_battery_kwh, 3),
            "loss_allowance_kwh": round(loss_allowance_kwh, 3),
            "dynamic_buffer_soc": round(buffer_soc, 1),
            "dynamic_buffer_kwh": round(buffer_kwh, 3),
            "confidence_mode": confidence_attrs.get("forecast_confidence"),
            "confidence_adjustment_soc": round(confidence_soc, 1),
            "confidence_adjustment_kwh": round(confidence_adjustment_kwh, 3),
            "stale_data_guard_active": stale_guard_attrs.get("stale_data_guard_active"),
            "stale_data_guard_min_soc": stale_guard_attrs.get("stale_data_guard_min_soc"),
        }
        summary = (
            f"Demand {projected_house_kwh:.1f} kWh - solar {projected_solar_kwh:.1f} kWh; "
            f"Pre-Sunrise Need {pre_sunrise_need_kwh:.2f} kWh; "
            f"losses {loss_allowance_kwh:.2f} kWh; buffer {buffer_soc:.0f}%"
        )
        if projection.get("no_useful_solar_forecast"):
            summary += f"; low-solar credit {pre_sunrise_credited_solar_kwh:.2f} kWh"
        if projection.get("solar_unavailable_override"):
            summary += "; Solar Unavailable: Batteries Only"
        if confidence_soc:
            summary += f"; confidence +{confidence_soc:.0f}%"
        if stale_guard_attrs.get("stale_data_guard_active"):
            summary += f"; guard floor {stale_guard_attrs.get('stale_data_guard_min_soc')}%"
        summary += f"; target {target_soc}%."

        return {
            "target_breakdown": breakdown,
            "target_breakdown_summary": summary,
            "why_target": summary,
        }

    def _target_jump_guard_attrs(self, target_soc: int, now: datetime) -> dict[str, Any]:
        local_date = now.astimezone().date().isoformat()
        previous = self._previous_recommended_soc
        if previous is None:
            return {
                "previous_recommended_soc": None,
                "previous_recommendation_date": self._previous_recommendation_date,
                "target_change_soc": None,
                "target_jump_guard": "first_sample",
                "target_jump_guard_threshold_soc": _OVERNIGHT_TARGET_CHANGE_WARNING_SOC,
                "target_jump_guard_action": "warning_only",
            }

        change = target_soc - previous
        large_change = abs(change) >= _OVERNIGHT_TARGET_CHANGE_WARNING_SOC
        return {
            "previous_recommended_soc": previous,
            "previous_recommendation_date": self._previous_recommendation_date,
            "target_change_soc": change,
            "target_jump_guard": "large_change_warning" if large_change else "normal",
            "target_jump_guard_threshold_soc": _OVERNIGHT_TARGET_CHANGE_WARNING_SOC,
            "target_jump_guard_action": "warning_only",
            "target_jump_guard_note": (
                "Large target change flagged for review; the recommendation is not capped."
                if large_change
                else None
            ),
            "target_change_same_local_day": self._previous_recommendation_date == local_date,
        }

    def _solar_unavailable_override(self) -> bool:
        return bool(getattr(self.coordinator, "solar_unavailable_override", False)) or self.hass.states.is_state(
            self._solar_availability_entity_id(),
            "Solar Unavailable",
        )

    def _solar_availability_entity_id(self) -> str:
        registry = er.async_get(self.hass)
        entity_id = registry.async_get_entity_id(
            "select",
            DOMAIN,
            f"{self._config_entry.entry_id}_solar_availability",
        )
        return entity_id or _SOLAR_AVAILABILITY_ENTITY

    @staticmethod
    def _recommendation_reason(
        target_soc: int,
        required_ac_kwh: float,
        loss_allowance_kwh: float,
        buffer_kwh: float,
        projection: dict[str, Any],
        buffer_attrs: dict[str, Any],
        estimated_grid_charge_energy_kwh: float | None,
    ) -> str:
        pre_sunrise_need_kwh = _as_float(
            projection.get("pre_sunrise_need_kwh", projection.get("morning_pre_solar_shortfall_kwh")),
            0.0,
        ) or 0.0
        projected_solar_kwh = _as_float(projection.get("projected_peak_solar_kwh"), 0.0) or 0.0
        grid_text = (
            f", approx {estimated_grid_charge_energy_kwh:.2f} kWh grid charge to reach target"
            if estimated_grid_charge_energy_kwh is not None and estimated_grid_charge_energy_kwh > 0
            else ""
        )
        return (
            f"Target {target_soc}%: Pre-Sunrise Need {pre_sunrise_need_kwh:.2f} kWh; "
            f"peak-window need {required_ac_kwh:.2f} kWh, loss allowance {loss_allowance_kwh:.2f} kWh, "
            f"dynamic buffer {buffer_kwh:.2f} kWh ({buffer_attrs.get('dynamic_buffer_soc')}%), "
            f"forecast solar {projected_solar_kwh:.1f} kWh"
            f"{' (Solar Unavailable: Batteries Only)' if projection.get('solar_unavailable_override') else ''}"
            f"{grid_text}."
        )

    @staticmethod
    def _round_soc_up(value: float, step: int) -> int:
        return int(math.ceil(max(0.0, value) / step) * step)

    def _off_peak_window_options(self) -> tuple[str, str, str]:
        options = self._config_entry.options
        off_peak_start = getattr(
            self.coordinator,
            "off_peak_start",
            options.get(CONF_OFF_PEAK_START, DEFAULT_OFF_PEAK_START),
        )
        off_peak_end = getattr(
            self.coordinator,
            "off_peak_end",
            options.get(CONF_OFF_PEAK_END, DEFAULT_OFF_PEAK_END),
        )
        _, _, off_peak_start = _parse_hhmm(off_peak_start, DEFAULT_OFF_PEAK_START)
        _, _, off_peak_end = _parse_hhmm(off_peak_end, DEFAULT_OFF_PEAK_END)
        tariff_preset = getattr(
            self.coordinator,
            "smart_tariff_preset",
            options.get(CONF_TARIFF_PRESET, DEFAULT_TARIFF_PRESET),
        )
        return off_peak_start, off_peak_end, tariff_preset

    def _next_peak_window(self, now: datetime) -> tuple[datetime, datetime]:
        off_peak_start, off_peak_end, _tariff_preset = self._off_peak_window_options()
        peak_start_hour, peak_start_minute, _ = _parse_hhmm(off_peak_end, DEFAULT_OFF_PEAK_END)
        peak_end_hour, peak_end_minute, _ = _parse_hhmm(off_peak_start, DEFAULT_OFF_PEAK_START)
        local_now = now.astimezone()
        start = local_now.replace(
            hour=peak_start_hour,
            minute=peak_start_minute,
            second=0,
            microsecond=0,
        )
        if local_now >= start:
            start += timedelta(days=1)
        end = start.replace(
            hour=peak_end_hour,
            minute=peak_end_minute,
        )
        if end <= start:
            end += timedelta(days=1)
        return start.astimezone(UTC), end.astimezone(UTC)

    def _fallback_daily_demand_kwh(self) -> tuple[float, dict[str, Any]]:
        house_daily_entity = self._house_demand_daily_entity_id()
        house_daily_kwh = self._state_energy_kwh(house_daily_entity)
        daily_source = "integration_house_demand_daily"
        if house_daily_kwh is None:
            house_daily_kwh = self._state_energy_kwh("sensor.house_demand_daily")
            daily_source = "legacy_external_house_demand_daily"

        ac_daily_kwh = 0.0
        net_meter_kwh: float | None = None
        if house_daily_kwh is not None and house_daily_kwh > 0:
            if daily_source == "legacy_external_house_demand_daily":
                ac_daily_kwh = self._state_energy_kwh(_AC_CHARGING_DAILY_ENTITY) or 0.0
                net_meter_kwh = max(0.0, house_daily_kwh - ac_daily_kwh)
            else:
                net_meter_kwh = house_daily_kwh

        house_empty, occupants = self._house_empty_state()
        demand_floor_kwh = (
            _EMPTY_HOUSE_DAILY_DEMAND_FLOOR_KWH
            if house_empty
            else _OCCUPIED_DAILY_DEMAND_FLOOR_KWH
        )
        fallback_kwh = demand_floor_kwh
        source = "empty_house_floor" if house_empty else "occupied_house_floor"
        if net_meter_kwh is not None:
            fallback_kwh = max(fallback_kwh, net_meter_kwh)
            source = "daily_meter_with_empty_house_floor" if house_empty else "daily_meter_with_occupied_floor"

        return fallback_kwh, {
            "fallback_daily_demand_kwh": round(fallback_kwh, 3),
            "fallback_daily_demand_source": source,
            "house_empty_mode": house_empty,
            "house_occupants": occupants,
            "house_occupancy_entity": _HOUSE_OCCUPANCY_ENTITY,
            "house_occupancy_basis": "home_zone_empty",
            "away_mode": house_empty,
            "away_mode_entity": _HOUSE_OCCUPANCY_ENTITY,
            "away_mode_basis": "deprecated_alias_for_house_empty_mode",
            "daily_demand_floor_kwh": demand_floor_kwh,
            "occupied_daily_demand_floor_kwh": _OCCUPIED_DAILY_DEMAND_FLOOR_KWH,
            "empty_house_daily_demand_floor_kwh": _EMPTY_HOUSE_DAILY_DEMAND_FLOOR_KWH,
            "house_demand_daily_entity": house_daily_entity,
            "house_demand_daily_source": daily_source,
            "house_demand_daily_kwh": round(house_daily_kwh, 3) if house_daily_kwh is not None else None,
            "ac_charging_daily_kwh": round(ac_daily_kwh, 3),
            "net_meter_house_demand_kwh": round(net_meter_kwh, 3) if net_meter_kwh is not None else None,
            "ac_charging_note": (
                "The integration House Demand Daily sensor already subtracts battery charging. "
                "AC charging is only subtracted when using the legacy external daily helper fallback."
            ),
        }

    def _house_demand_daily_entity_id(self) -> str:
        registry = er.async_get(self.hass)
        entity_id = registry.async_get_entity_id(
            "sensor",
            DOMAIN,
            f"{self._config_entry.entry_id}_house_demand_daily",
        )
        return entity_id or _HOUSE_DEMAND_DAILY_ENTITY

    def _away_mode_active(self) -> bool:
        house_empty, _occupants = self._house_empty_state()
        return house_empty

    def _house_empty_state(self) -> tuple[bool, int | None]:
        state = self.hass.states.get(_HOUSE_OCCUPANCY_ENTITY)
        if state is None:
            return False, None
        house_empty, _occupants = _house_empty_from_state(state.state)
        return bool(house_empty), _occupants

    def _project_peak_window(
        self,
        start: datetime,
        end: datetime,
        now: datetime,
        fallback_demand_w: float,
        solar_unavailable: bool,
    ) -> dict[str, Any]:
        forecasts = [] if solar_unavailable else self._load_solcast_forecasts()
        if forecasts or solar_unavailable:
            return self._simulate_peak_window(
                start,
                end,
                now,
                fallback_demand_w,
                forecasts,
                solar_unavailable,
            )
        return self._fallback_projection_without_timed_forecast(
            start,
            end,
            now,
            fallback_demand_w,
            solar_unavailable,
        )

    def _simulate_peak_window(
        self,
        start: datetime,
        end: datetime,
        now: datetime,
        fallback_demand_w: float,
        forecasts: list[dict[str, Any]],
        solar_unavailable: bool = False,
    ) -> dict[str, Any]:
        profile = self._profile_by_bucket()
        profile_start = self._recorder_profile_start or now
        forecast_periods = self._forecast_periods(forecasts, start, end)
        interval = _RUNTIME_PROFILE_INTERVAL
        current = start
        cumulative_deficit_kwh = 0.0
        maximum_deficit_kwh = 0.0
        demand_kwh = 0.0
        solar_kwh = 0.0
        pre_useful_solar_open = True
        pre_sunrise_net_need_kwh = 0.0
        pre_sunrise_demand_kwh = 0.0
        pre_sunrise_solar_kwh = 0.0
        pre_sunrise_profile_bucket_count = 0
        pre_sunrise_fallback_bucket_count = 0
        first_solar_start_at: datetime | None = None
        useful_solar_start_at: datetime | None = None
        useful_solar_consecutive_periods = 0
        useful_solar_threshold_w: float | None = None
        useful_solar_break_even_threshold_w: float | None = None
        profile_bucket_count = 0
        fallback_bucket_count = 0

        while current < end:
            segment_end = min(end, current + interval)
            hours = (segment_end - current).total_seconds() / 3600
            demand_w, used_profile = self._demand_w_for_time(current, profile_start, profile, fallback_demand_w)
            segment_demand_kwh = demand_w * hours / 1000
            segment_solar_kwh = self._solar_kwh_for_segment(current, segment_end, forecast_periods)
            segment_solar_w = segment_solar_kwh * 1000 / hours if hours > 0 else 0.0
            if first_solar_start_at is None and segment_solar_w >= _RUNTIME_SOLAR_ACTIVE_THRESHOLD_W:
                first_solar_start_at = current
            useful_solar_threshold_w = max(
                _RUNTIME_SOLAR_ACTIVE_THRESHOLD_W,
                demand_w + _OVERNIGHT_USEFUL_SOLAR_MARGIN_W,
                demand_w * _OVERNIGHT_USEFUL_SOLAR_DEMAND_FACTOR,
            )
            solar_covers_house = segment_solar_w >= useful_solar_threshold_w
            is_pre_sunrise_segment = pre_useful_solar_open
            demand_kwh += segment_demand_kwh
            solar_kwh += segment_solar_kwh
            cumulative_deficit_kwh += segment_demand_kwh - segment_solar_kwh
            maximum_deficit_kwh = max(maximum_deficit_kwh, cumulative_deficit_kwh)
            if is_pre_sunrise_segment:
                pre_sunrise_demand_kwh += segment_demand_kwh
                pre_sunrise_solar_kwh += segment_solar_kwh
                pre_sunrise_net_need_kwh = max(
                    pre_sunrise_net_need_kwh,
                    cumulative_deficit_kwh,
                )
                if used_profile:
                    pre_sunrise_profile_bucket_count += 1
                else:
                    pre_sunrise_fallback_bucket_count += 1
            if used_profile:
                profile_bucket_count += 1
            else:
                fallback_bucket_count += 1
            if pre_useful_solar_open:
                if solar_covers_house:
                    useful_solar_consecutive_periods += 1
                    if useful_solar_consecutive_periods >= _OVERNIGHT_USEFUL_SOLAR_CONSECUTIVE_PERIODS:
                        pre_useful_solar_open = False
                        useful_solar_start_at = segment_end
                        useful_solar_break_even_threshold_w = useful_solar_threshold_w
                else:
                    useful_solar_consecutive_periods = 0
            current = segment_end

        no_useful_solar_forecast = useful_solar_start_at is None
        solar_to_demand_ratio = solar_kwh / demand_kwh if demand_kwh > 0 else 0.0
        if no_useful_solar_forecast:
            pre_sunrise_solar_credit_factor = _OVERNIGHT_NO_USEFUL_SOLAR_CREDIT_FACTOR
            solar_credit_mode = "low_solar_day_partial_forecast_credit"
        elif solar_to_demand_ratio >= _OVERNIGHT_STRONG_SOLAR_RATIO:
            pre_sunrise_solar_credit_factor = _OVERNIGHT_STRONG_SOLAR_PRE_USEFUL_CREDIT_FACTOR
            solar_credit_mode = "strong_solar_pre_useful_ramp_credit"
        elif solar_to_demand_ratio >= _OVERNIGHT_BALANCED_SOLAR_RATIO:
            pre_sunrise_solar_credit_factor = _OVERNIGHT_BALANCED_SOLAR_PRE_USEFUL_CREDIT_FACTOR
            solar_credit_mode = "balanced_solar_pre_useful_ramp_credit"
        else:
            pre_sunrise_solar_credit_factor = _OVERNIGHT_PRE_USEFUL_SOLAR_CREDIT_FACTOR
            solar_credit_mode = "pre_useful_solar_ramp_partial_credit"
        pre_sunrise_credited_solar_kwh = pre_sunrise_solar_kwh * pre_sunrise_solar_credit_factor
        pre_sunrise_guard_need_kwh = max(
            0.0,
            pre_sunrise_demand_kwh - pre_sunrise_credited_solar_kwh,
        )
        required_start_energy_kwh = max(maximum_deficit_kwh, pre_sunrise_guard_need_kwh)
        pre_sunrise_basis = (
            "no_sustained_useful_solar_forecast_with_partial_day_solar_credit"
            if no_useful_solar_forecast
            else "until_sustained_forecast_solar_exceeds_house_demand_with_partial_early_solar_credit"
        )

        return {
            "method": "timed_solcast_forecast_minus_time_of_day_house_demand",
            "solar_forecast_source": (
                "Solar Unavailable override" if solar_unavailable else "Solcast detailed forecast file"
            ),
            "solar_forecast_path": None if solar_unavailable else self._forecast_cache_path,
            "projected_peak_house_demand_kwh": round(demand_kwh, 3),
            "projected_peak_solar_kwh": round(solar_kwh, 3),
            "solar_unavailable_override": solar_unavailable,
            "solar_override_status": "Batteries Only" if solar_unavailable else "Solar forecast active",
            "required_start_energy_kwh": round(required_start_energy_kwh, 3),
            "maximum_cumulative_deficit_kwh": round(maximum_deficit_kwh, 3),
            "pre_sunrise_need_kwh": round(pre_sunrise_guard_need_kwh, 3),
            "pre_sunrise_net_need_kwh": round(pre_sunrise_net_need_kwh, 3),
            "pre_sunrise_guard_need_kwh": round(pre_sunrise_guard_need_kwh, 3),
            "pre_sunrise_house_demand_kwh": round(pre_sunrise_demand_kwh, 3),
            "pre_sunrise_solar_kwh": round(pre_sunrise_solar_kwh, 3),
            "pre_sunrise_credited_solar_kwh": round(pre_sunrise_credited_solar_kwh, 3),
            "pre_sunrise_solar_credit_factor": pre_sunrise_solar_credit_factor,
            "no_useful_solar_forecast": no_useful_solar_forecast,
            "low_solar_day_credit_factor": _OVERNIGHT_NO_USEFUL_SOLAR_CREDIT_FACTOR,
            "balanced_solar_day_credit_factor": _OVERNIGHT_BALANCED_SOLAR_PRE_USEFUL_CREDIT_FACTOR,
            "strong_solar_day_credit_factor": _OVERNIGHT_STRONG_SOLAR_PRE_USEFUL_CREDIT_FACTOR,
            "solar_to_demand_ratio": round(solar_to_demand_ratio, 2),
            "balanced_solar_ratio": _OVERNIGHT_BALANCED_SOLAR_RATIO,
            "strong_solar_ratio": _OVERNIGHT_STRONG_SOLAR_RATIO,
            "solar_credit_mode": solar_credit_mode,
            "pre_sunrise_solar_start_at": (
                first_solar_start_at.isoformat() if first_solar_start_at else None
            ),
            "useful_solar_start_at": useful_solar_start_at.isoformat() if useful_solar_start_at else None,
            "solar_break_even_at": useful_solar_start_at.isoformat() if useful_solar_start_at else None,
            "pre_sunrise_basis": pre_sunrise_basis,
            "useful_solar_consecutive_periods_required": _OVERNIGHT_USEFUL_SOLAR_CONSECUTIVE_PERIODS,
            "useful_solar_margin_w": _OVERNIGHT_USEFUL_SOLAR_MARGIN_W,
            "useful_solar_demand_factor": _OVERNIGHT_USEFUL_SOLAR_DEMAND_FACTOR,
            "useful_solar_threshold_w": (
                round(useful_solar_break_even_threshold_w, 1)
                if useful_solar_break_even_threshold_w
                else None
            ),
            "pre_sunrise_profile_buckets_used": pre_sunrise_profile_bucket_count,
            "pre_sunrise_fallback_buckets_used": pre_sunrise_fallback_bucket_count,
            "pre_sunrise_label": "Pre-Sunrise Need",
            "morning_pre_solar_shortfall_kwh": round(pre_sunrise_guard_need_kwh, 3),
            "morning_solar_start_at": (
                first_solar_start_at.isoformat() if first_solar_start_at else None
            ),
            "morning_solar_break_even_at": useful_solar_start_at.isoformat() if useful_solar_start_at else None,
            "morning_solar_active_threshold_w": _RUNTIME_SOLAR_ACTIVE_THRESHOLD_W,
            "demand_profile_buckets_used": profile_bucket_count,
            "fallback_demand_buckets_used": fallback_bucket_count,
            "fallback_demand_w": round(fallback_demand_w, 1),
        }

    def _fallback_projection_without_timed_forecast(
        self,
        start: datetime,
        end: datetime,
        now: datetime,
        fallback_demand_w: float,
        solar_unavailable: bool = False,
    ) -> dict[str, Any]:
        peak_demand_kwh = self._project_demand_energy_kwh(start, end, now, fallback_demand_w)
        morning_end = min(end, start + timedelta(hours=5))
        morning_gap_kwh = self._project_demand_energy_kwh(start, morning_end, now, fallback_demand_w)
        forecast_kwh = 0.0 if solar_unavailable else (self._state_energy_kwh(_SOLCAST_TOMORROW_ENTITY) or 0.0)
        daily_deficit_kwh = max(0.0, peak_demand_kwh - forecast_kwh)
        required_kwh = max(morning_gap_kwh, daily_deficit_kwh)
        return {
            "method": "daily_solcast_sensor_with_morning_gap_fallback",
            "solar_forecast_source": "Solar Unavailable override" if solar_unavailable else _SOLCAST_TOMORROW_ENTITY,
            "projected_peak_house_demand_kwh": round(peak_demand_kwh, 3),
            "projected_peak_solar_kwh": round(forecast_kwh, 3),
            "solar_unavailable_override": solar_unavailable,
            "solar_override_status": "Batteries Only" if solar_unavailable else "Solar forecast active",
            "pre_sunrise_need_kwh": round(morning_gap_kwh, 3),
            "pre_sunrise_net_need_kwh": round(morning_gap_kwh, 3),
            "pre_sunrise_guard_need_kwh": round(morning_gap_kwh, 3),
            "pre_sunrise_house_demand_kwh": round(morning_gap_kwh, 3),
            "pre_sunrise_solar_kwh": 0.0,
            "pre_sunrise_credited_solar_kwh": 0.0,
            "pre_sunrise_solar_credit_factor": _OVERNIGHT_PRE_USEFUL_SOLAR_CREDIT_FACTOR,
            "no_useful_solar_forecast": None,
            "low_solar_day_credit_factor": _OVERNIGHT_NO_USEFUL_SOLAR_CREDIT_FACTOR,
            "solar_credit_mode": "daily_sensor_fallback",
            "pre_sunrise_solar_start_at": None,
            "useful_solar_start_at": None,
            "solar_break_even_at": None,
            "pre_sunrise_basis": "fixed_morning_gap_without_timed_forecast",
            "useful_solar_consecutive_periods_required": _OVERNIGHT_USEFUL_SOLAR_CONSECUTIVE_PERIODS,
            "useful_solar_margin_w": _OVERNIGHT_USEFUL_SOLAR_MARGIN_W,
            "useful_solar_demand_factor": _OVERNIGHT_USEFUL_SOLAR_DEMAND_FACTOR,
            "useful_solar_threshold_w": None,
            "pre_sunrise_profile_buckets_used": None,
            "pre_sunrise_fallback_buckets_used": None,
            "pre_sunrise_label": "Pre-Sunrise Need",
            "morning_gap_demand_kwh": round(morning_gap_kwh, 3),
            "morning_pre_solar_shortfall_kwh": round(morning_gap_kwh, 3),
            "morning_solar_start_at": None,
            "morning_solar_break_even_at": None,
            "morning_solar_active_threshold_w": _RUNTIME_SOLAR_ACTIVE_THRESHOLD_W,
            "daily_deficit_kwh": round(daily_deficit_kwh, 3),
            "required_start_energy_kwh": round(required_kwh, 3),
            "fallback_demand_w": round(fallback_demand_w, 1),
        }

    def _project_demand_energy_kwh(
        self,
        start: datetime,
        end: datetime,
        now: datetime,
        fallback_demand_w: float,
    ) -> float:
        profile = self._profile_by_bucket()
        profile_start = self._recorder_profile_start or now
        interval = _RUNTIME_PROFILE_INTERVAL
        current = start
        total_kwh = 0.0
        while current < end:
            segment_end = min(end, current + interval)
            hours = (segment_end - current).total_seconds() / 3600
            demand_w, _used_profile = self._demand_w_for_time(current, profile_start, profile, fallback_demand_w)
            total_kwh += demand_w * hours / 1000
            current = segment_end
        return total_kwh

    def _profile_by_bucket(self) -> dict[int, float]:
        profile: dict[int, float] = {}
        for entry in self._recorder_demand_profile:
            try:
                bucket = int(entry["bucket"])
                watts = float(entry["average_w"])
            except (KeyError, TypeError, ValueError):
                continue
            if watts > 0:
                profile[bucket] = watts
        return profile

    def _demand_w_for_time(
        self,
        sample_time: datetime,
        profile_start: datetime,
        profile: dict[int, float],
        fallback_demand_w: float,
    ) -> tuple[float, bool]:
        if profile:
            interval_seconds = _RUNTIME_PROFILE_INTERVAL.total_seconds()
            offset_seconds = (sample_time - profile_start).total_seconds() % _RUNTIME_PROFILE_HORIZON.total_seconds()
            bucket = int(offset_seconds // interval_seconds)
            demand_w = profile.get(bucket)
            if demand_w is not None:
                return max(0.0, demand_w), True
        return max(0.0, fallback_demand_w), False

    def _forecast_periods(
        self,
        forecasts: list[dict[str, Any]],
        start: datetime,
        end: datetime,
    ) -> list[tuple[datetime, datetime, float]]:
        periods: list[tuple[datetime, datetime, float]] = []
        for item in forecasts:
            period_start = AeccEstimatedChargeTimeSensor._parse_datetime(item.get("period_start"))
            forecast_kw = _as_float(item.get("pv_estimate"), 0.0) or 0.0
            if period_start is None or forecast_kw <= 0:
                continue
            period_end = period_start + _FORECAST_PERIOD
            if period_end <= start or period_start >= end:
                continue
            periods.append((period_start, period_end, forecast_kw))
        return periods

    @staticmethod
    def _solar_kwh_for_segment(
        segment_start: datetime,
        segment_end: datetime,
        forecast_periods: list[tuple[datetime, datetime, float]],
    ) -> float:
        total_kwh = 0.0
        for period_start, period_end, forecast_kw in forecast_periods:
            overlap_start = max(segment_start, period_start)
            overlap_end = min(segment_end, period_end)
            if overlap_end <= overlap_start:
                continue
            total_kwh += forecast_kw * (overlap_end - overlap_start).total_seconds() / 3600
        return total_kwh

    def _load_solcast_forecasts(self) -> list[dict[str, Any]]:
        for relative_path in _SOLCAST_DETAILED_FORECAST_PATHS:
            path = self.hass.config.path(relative_path)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue

            if path == self._forecast_cache_path and mtime == self._forecast_cache_mtime:
                return self._forecast_cache

            try:
                with open(path, encoding="utf-8") as forecast_file:
                    data = json.load(forecast_file)
            except (OSError, json.JSONDecodeError) as exc:
                _LOGGER.debug("Could not read Solcast forecast file %s: %s", path, exc)
                continue

            forecasts = self._combine_solcast_site_forecasts(data)
            self._forecast_cache_path = path
            self._forecast_cache_mtime = mtime
            self._forecast_cache = forecasts
            return forecasts

        return []

    @staticmethod
    def _combine_solcast_site_forecasts(data: dict[str, Any]) -> list[dict[str, Any]]:
        siteinfo = data.get("siteinfo")
        if not isinstance(siteinfo, dict):
            return []

        combined: dict[str, float] = {}
        for site in siteinfo.values():
            if not isinstance(site, dict):
                continue
            forecasts = site.get("forecasts")
            if not isinstance(forecasts, list):
                continue
            for item in forecasts:
                if not isinstance(item, dict):
                    continue
                period_start = item.get("period_start")
                forecast_kw = _as_float(item.get("pv_estimate"), 0.0) or 0.0
                if period_start:
                    combined[str(period_start)] = combined.get(str(period_start), 0.0) + forecast_kw

        return [
            {"period_start": period_start, "pv_estimate": forecast_kw}
            for period_start, forecast_kw in sorted(combined.items())
        ]

    def _state_energy_kwh(self, entity_id: str) -> float | None:
        value = _state_float(self.hass, entity_id)
        if value is None:
            return None
        state = self.hass.states.get(entity_id)
        unit = (state.attributes.get("unit_of_measurement") if state else "") or ""
        if unit.lower() == "wh":
            return value / 1000
        return value


class AeccFirmwareSensor(CoordinatorEntity[AeccBatteryCoordinator], SensorEntity):
    """Firmware version from DeviceManagement probe (supported on some AECC devices)."""

    _attr_has_entity_name = True
    _attr_name = "Firmware Version"
    _attr_icon = "mdi:chip"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AeccBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_firmware_version"

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def native_value(self) -> str | None:
        return self.coordinator.firmware_version
