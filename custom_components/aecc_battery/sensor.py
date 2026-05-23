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

from .const import CONF_ADVANCED_ENERGY_SENSORS, DEFAULT_BATTERY_CAPACITY_KWH, DOMAIN
from .coordinator import AeccBatteryCoordinator

_LOGGER = logging.getLogger(__name__)

# ── Standard power/measurement sensors ────────────────────────────────────────
# (key, name, canonical_key, unit, icon, is_power)
_SENSORS = [
    ("ac_charging_power", "AC Charging Power", "ac_charging_power", UnitOfPower.WATT, "mdi:power-plug", True),
    (
        "battery_discharging_power",
        "Battery Discharging Power",
        "battery_discharging_power",
        UnitOfPower.WATT,
        "mdi:battery-arrow-down",
        True,
    ),
    ("battery_soc", "Battery SOC", "battery_soc", PERCENTAGE, "mdi:battery", False),
    (
        "system_average_battery_soc",
        "System Average Battery SOC",
        "average_battery_soc",
        PERCENTAGE,
        "mdi:battery-sync",
        False,
    ),
    (
        "local_unit_battery_soc",
        "Local Unit Battery SOC",
        "local_battery_soc",
        PERCENTAGE,
        "mdi:battery-outline",
        False,
    ),
    ("pv_power", "PV Power", "pv_power", UnitOfPower.WATT, "mdi:solar-power", True),
    ("pv_charging_power", "PV Charging Power", "pv_charging_power", UnitOfPower.WATT, "mdi:solar-panel", True),
    ("grid_power", "Grid / Meter Power", "grid_power", UnitOfPower.WATT, "mdi:transmission-tower", True),
    ("backup_power", "Backup Power", "backup_power", UnitOfPower.WATT, "mdi:power-plug-battery", True),
    ("pv1_power", "PV String 1 Power", "pv1_power", UnitOfPower.WATT, "mdi:solar-panel", True),
    ("pv2_power", "PV String 2 Power", "pv2_power", UnitOfPower.WATT, "mdi:solar-panel", True),
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
    "system_average_battery_soc",
    "local_unit_battery_soc",
}

# ── Energy counter definitions ────────────────────────────────────────────────
# (key, name, power_keys, icon)
_ENERGY_SENSORS = [
    ("energy_charged", "Energy Charged", ["ac_charging_power", "pv_charging_power"], "mdi:battery-charging"),
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
_GRID_METER_POWER_ENTITY_FALLBACK = "sensor.aecc_battery_grid_meter_power"
_HOUSE_DEMAND_DAILY_ENTITY = "sensor.house_demand_daily"
_AC_CHARGING_DAILY_ENTITY = "sensor.aferiy_ac_charging_daily"
_AWAY_MODE_PERSON_ENTITY = "person.richard_owen"
_FORECAST_PERIOD = timedelta(minutes=30)
_RUNTIME_DEMAND_HISTORY_WINDOW = timedelta(hours=3)
_RUNTIME_DEMAND_MIN_HISTORY = timedelta(minutes=15)
_RUNTIME_RECORDER_HISTORY_DAYS = 7
_RUNTIME_RECORDER_REFRESH_INTERVAL = timedelta(minutes=30)
_RUNTIME_PROFILE_HORIZON = timedelta(hours=24)
_RUNTIME_PROFILE_INTERVAL = timedelta(minutes=30)
_RUNTIME_PROFILE_MAX_CYCLES = 14
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
_OVERNIGHT_WINDOW_START_HOUR = 5
_OVERNIGHT_WINDOW_START_MINUTE = 30
_OVERNIGHT_WINDOW_END_HOUR = 23
_OVERNIGHT_WINDOW_END_MINUTE = 30
_OVERNIGHT_BUFFER_SOC = 4.0
_DEFAULT_DAILY_HOUSE_DEMAND_KWH = 11.0
_AWAY_MODE_DAILY_DEMAND_KWH = 9.0


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

    for key, name, power_keys, icon in _ENERGY_SENSORS:
        entities.append(AeccEnergySensor(coordinator, config_entry, key, name, power_keys, icon))

    entities.append(AeccGridExportSensor(coordinator, config_entry))
    entities.append(AeccTotalBatteryOutputPowerSensor(coordinator, config_entry))
    entities.append(AeccBatteryPowerSensor(coordinator, config_entry))
    entities.append(AeccBatteryStatusSensor(coordinator, config_entry))
    entities.append(AeccConnectionStatusSensor(coordinator, config_entry))
    entities.append(AeccLastSuccessfulUpdateSensor(coordinator, config_entry))
    entities.append(AeccConsecutiveFailuresSensor(coordinator, config_entry))
    entities.append(AeccLastCommandResultSensor(coordinator, config_entry))

    if config_entry.options.get(CONF_ADVANCED_ENERGY_SENSORS, False):
        entities.append(AeccEstimatedHouseDemandSensor(coordinator, config_entry))
        entities.append(AeccEstimatedChargeTimeSensor(coordinator, config_entry))
        entities.append(AeccWillFillTodaySensor(coordinator, config_entry))
        entities.append(AeccRuntimeAtCurrentHouseDemandSensor(coordinator, config_entry))
        entities.append(AeccRecommendedOvernightSocSensor(coordinator, config_entry))

    if coordinator.firmware_version is not None:
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

        if any_valid and self._last_update_time is not None:
            delta_seconds = (now - self._last_update_time).total_seconds()
            if 0 < delta_seconds <= _MAX_GAP_SECONDS:
                delta_kwh = total_power_w * delta_seconds / 3_600_000
                self._accumulated_kwh += delta_kwh

        if any_valid:
            self._last_update_time = now

        self.async_write_ha_state()


class AeccGridExportSensor(CoordinatorEntity[AeccBatteryCoordinator], SensorEntity):
    """Grid export power derived from grid_power. Export = positive grid values only."""

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
    _attr_name = "Total Battery Output Power"
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
    _attr_name = "Estimated House Demand"
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
    _attr_entity_category = EntityCategory.DIAGNOSTIC

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
    _attr_name = "Runtime Estimate"
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
            local_now = now.astimezone()
            self._recorder_profile_start = now

            for days_ago in range(1, _RUNTIME_RECORDER_HISTORY_DAYS + 1):
                window_start_local = local_now - timedelta(days=days_ago)
                window_end_local = window_start_local + _RUNTIME_PROFILE_HORIZON
                start = window_start_local.astimezone(UTC)
                end = window_end_local.astimezone(UTC)

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
                daily_results.append(
                    {
                        "days_ago": days_ago,
                        "profile_buckets": profile_buckets,
                        "duration_seconds": duration_seconds,
                        "watt_seconds": watt_seconds,
                        "average_w": round(average_w, 1),
                        "energy_kwh": round(watt_seconds / 3_600_000, 3),
                        "source": history_source,
                    }
                )

            refreshed_at = datetime.now(UTC)
            self._last_recorder_refresh = refreshed_at
            daily_averages, rejected_daily_averages = self._filter_runtime_history_days(daily_results)
            profile_totals: dict[int, dict[str, float]] = {}
            total_duration_seconds = 0.0
            total_watt_seconds = 0.0
            for day in daily_averages:
                total_duration_seconds += float(day["duration_seconds"])
                total_watt_seconds += float(day["watt_seconds"])
                for bucket_index, bucket in day["profile_buckets"].items():
                    profile_bucket = profile_totals.setdefault(
                        bucket_index,
                        {"duration_seconds": 0.0, "watt_seconds": 0.0},
                    )
                    profile_bucket["duration_seconds"] += bucket["duration_seconds"]
                    profile_bucket["watt_seconds"] += bucket["watt_seconds"]

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
                    "recorder_history_rejected_daily_averages": rejected_daily_averages,
                    "recorder_history_last_refresh": refreshed_at.isoformat(),
                    "recorder_history_profile_start": now.isoformat(),
                    "recorder_history_reason": "No plausible forward house-demand history is available yet",
                }
            else:
                average_w = total_watt_seconds / total_duration_seconds
                average_window_energy_kwh = total_watt_seconds / len(daily_averages) / 3_600_000
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
                    "recorder_history_rejected_days": len(rejected_daily_averages),
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
                    "recorder_history_reason": str(exc),
                }
        finally:
            self._recorder_refresh_in_progress = False
            try:
                self.async_write_ha_state()
            except RuntimeError:
                pass

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


class AeccRecommendedOvernightSocSensor(AeccRuntimeAtCurrentHouseDemandSensor):
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

    @property
    def native_value(self) -> int | None:
        self._record_house_demand_sample()
        state, attrs = self._calculate_recommendation()
        self._last_attributes = attrs
        return state

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
        buffer_soc = _OVERNIGHT_BUFFER_SOC
        start, end = self._next_peak_window(now)
        fallback_daily_kwh, fallback_attrs = self._fallback_daily_demand_kwh()
        fallback_demand_w = fallback_daily_kwh * 1000 / 24

        attrs: dict[str, Any] = {
            "calculated_at": now.isoformat(),
            "target_window_start": start.isoformat(),
            "target_window_end": end.isoformat(),
            "battery_capacity_kwh": round(capacity_kwh, 3),
            "current_soc": round(soc, 1) if soc is not None else None,
            "reserve_soc": round(reserve_soc, 1),
            "buffer_soc": buffer_soc,
            "solcast_tomorrow_kwh": self._state_energy_kwh(_SOLCAST_TOMORROW_ENTITY),
            **fallback_attrs,
            **self._current_recorder_history_attrs(now),
        }

        if capacity_kwh <= 0:
            attrs["status"] = "missing_data"
            attrs["reason"] = "Battery capacity is unavailable"
            return None, attrs

        projection = self._project_peak_window(start, end, now, fallback_demand_w)
        attrs.update(projection)

        buffer_kwh = capacity_kwh * buffer_soc / 100
        reserve_kwh = capacity_kwh * reserve_soc / 100
        usable_capacity_kwh = capacity_kwh * max(0.0, _FULL_SOC - reserve_soc) / 100
        required_usable_kwh = max(0.0, float(projection["required_start_energy_kwh"])) + buffer_kwh
        uncovered_shortfall_kwh = max(0.0, required_usable_kwh - usable_capacity_kwh)
        required_usable_kwh = min(required_usable_kwh, usable_capacity_kwh)

        raw_target_soc = reserve_soc + (required_usable_kwh / capacity_kwh * 100)
        minimum_target_soc = min(_FULL_SOC, reserve_soc + buffer_soc)
        rounded_target_soc = self._round_soc_up(raw_target_soc, 1)
        rounded_target_soc = int(min(_FULL_SOC, max(minimum_target_soc, rounded_target_soc)))

        attrs.update(
            {
                "reserve_energy_kwh": round(reserve_kwh, 3),
                "buffer_energy_kwh": round(buffer_kwh, 3),
                "usable_capacity_above_reserve_kwh": round(usable_capacity_kwh, 3),
                "required_usable_energy_before_rounding_kwh": round(required_usable_kwh, 3),
                "uncovered_shortfall_kwh": round(uncovered_shortfall_kwh, 3),
                "target_soc_before_rounding": round(raw_target_soc, 1),
                "target_soc_rounding_step": 1,
                "recommended_soc": rounded_target_soc,
                "status": "full_capacity_recommended" if rounded_target_soc >= 100 else "estimated",
                "note": (
                    "Recommendation covers the peak-rate window after 05:30, subtracts expected solar "
                    "by forecast period, and keeps a small buffer above the discharge limit."
                ),
            }
        )
        return rounded_target_soc, attrs

    @staticmethod
    def _round_soc_up(value: float, step: int) -> int:
        return int(math.ceil(max(0.0, value) / step) * step)

    def _next_peak_window(self, now: datetime) -> tuple[datetime, datetime]:
        local_now = now.astimezone()
        start = local_now.replace(
            hour=_OVERNIGHT_WINDOW_START_HOUR,
            minute=_OVERNIGHT_WINDOW_START_MINUTE,
            second=0,
            microsecond=0,
        )
        if local_now >= start:
            start += timedelta(days=1)
        end = start.replace(
            hour=_OVERNIGHT_WINDOW_END_HOUR,
            minute=_OVERNIGHT_WINDOW_END_MINUTE,
        )
        if end <= start:
            end += timedelta(days=1)
        return start.astimezone(UTC), end.astimezone(UTC)

    def _fallback_daily_demand_kwh(self) -> tuple[float, dict[str, Any]]:
        house_daily_kwh = self._state_energy_kwh(_HOUSE_DEMAND_DAILY_ENTITY)
        ac_daily_kwh = self._state_energy_kwh(_AC_CHARGING_DAILY_ENTITY) or 0.0
        net_meter_kwh: float | None = None
        if house_daily_kwh is not None and house_daily_kwh > 0:
            net_meter_kwh = max(0.0, house_daily_kwh - ac_daily_kwh)

        away_mode = self._away_mode_active()
        demand_floor_kwh = _AWAY_MODE_DAILY_DEMAND_KWH if away_mode else _DEFAULT_DAILY_HOUSE_DEMAND_KWH
        fallback_kwh = demand_floor_kwh
        source = "away_mode_floor" if away_mode else "baseline_from_pre_solar_octopus_metering"
        if net_meter_kwh is not None:
            fallback_kwh = max(fallback_kwh, net_meter_kwh)
            source = "daily_meter_with_away_floor" if away_mode else "daily_meter_with_baseline_floor"

        return fallback_kwh, {
            "fallback_daily_demand_kwh": round(fallback_kwh, 3),
            "fallback_daily_demand_source": source,
            "away_mode": away_mode,
            "away_mode_entity": _AWAY_MODE_PERSON_ENTITY,
            "daily_demand_floor_kwh": demand_floor_kwh,
            "house_demand_daily_kwh": round(house_daily_kwh, 3) if house_daily_kwh is not None else None,
            "ac_charging_daily_kwh": round(ac_daily_kwh, 3),
            "net_meter_house_demand_kwh": round(net_meter_kwh, 3) if net_meter_kwh is not None else None,
            "ac_charging_note": (
                "Recorder history uses Estimated House Demand, where battery AC charging is already subtracted. "
                "The daily-meter fallback subtracts AFERIY AC charging as a guard."
            ),
        }

    def _away_mode_active(self) -> bool:
        state = self.hass.states.get(_AWAY_MODE_PERSON_ENTITY)
        if state is None or state.state in ("unknown", "unavailable"):
            return False
        return state.state != "home"

    def _project_peak_window(
        self,
        start: datetime,
        end: datetime,
        now: datetime,
        fallback_demand_w: float,
    ) -> dict[str, Any]:
        forecasts = self._load_solcast_forecasts()
        if forecasts:
            return self._simulate_peak_window(start, end, now, fallback_demand_w, forecasts)
        return self._fallback_projection_without_timed_forecast(start, end, now, fallback_demand_w)

    def _simulate_peak_window(
        self,
        start: datetime,
        end: datetime,
        now: datetime,
        fallback_demand_w: float,
        forecasts: list[dict[str, Any]],
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
        pre_solar_morning_open = True
        morning_pre_solar_shortfall_kwh = 0.0
        morning_solar_start_at: datetime | None = None
        profile_bucket_count = 0
        fallback_bucket_count = 0

        while current < end:
            segment_end = min(end, current + interval)
            hours = (segment_end - current).total_seconds() / 3600
            demand_w, used_profile = self._demand_w_for_time(current, profile_start, profile, fallback_demand_w)
            segment_demand_kwh = demand_w * hours / 1000
            segment_solar_kwh = self._solar_kwh_for_segment(current, segment_end, forecast_periods)
            segment_solar_w = segment_solar_kwh * 1000 / hours if hours > 0 else 0.0
            if pre_solar_morning_open and segment_solar_w >= _RUNTIME_SOLAR_ACTIVE_THRESHOLD_W:
                pre_solar_morning_open = False
                morning_solar_start_at = current
            demand_kwh += segment_demand_kwh
            solar_kwh += segment_solar_kwh
            cumulative_deficit_kwh += segment_demand_kwh - segment_solar_kwh
            maximum_deficit_kwh = max(maximum_deficit_kwh, cumulative_deficit_kwh)
            if pre_solar_morning_open:
                morning_pre_solar_shortfall_kwh = max(
                    morning_pre_solar_shortfall_kwh,
                    cumulative_deficit_kwh,
                )
            if used_profile:
                profile_bucket_count += 1
            else:
                fallback_bucket_count += 1
            current = segment_end

        return {
            "method": "timed_solcast_forecast_minus_time_of_day_house_demand",
            "solar_forecast_source": "Solcast detailed forecast file",
            "solar_forecast_path": self._forecast_cache_path,
            "projected_peak_house_demand_kwh": round(demand_kwh, 3),
            "projected_peak_solar_kwh": round(solar_kwh, 3),
            "required_start_energy_kwh": round(maximum_deficit_kwh, 3),
            "maximum_cumulative_deficit_kwh": round(maximum_deficit_kwh, 3),
            "morning_pre_solar_shortfall_kwh": round(morning_pre_solar_shortfall_kwh, 3),
            "morning_solar_start_at": morning_solar_start_at.isoformat() if morning_solar_start_at else None,
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
    ) -> dict[str, Any]:
        peak_demand_kwh = self._project_demand_energy_kwh(start, end, now, fallback_demand_w)
        morning_end = min(end, start + timedelta(hours=5))
        morning_gap_kwh = self._project_demand_energy_kwh(start, morning_end, now, fallback_demand_w)
        forecast_kwh = self._state_energy_kwh(_SOLCAST_TOMORROW_ENTITY) or 0.0
        daily_deficit_kwh = max(0.0, peak_demand_kwh - forecast_kwh)
        required_kwh = max(morning_gap_kwh, daily_deficit_kwh)
        return {
            "method": "daily_solcast_sensor_with_morning_gap_fallback",
            "solar_forecast_source": _SOLCAST_TOMORROW_ENTITY,
            "projected_peak_house_demand_kwh": round(peak_demand_kwh, 3),
            "projected_peak_solar_kwh": round(forecast_kwh, 3),
            "morning_gap_demand_kwh": round(morning_gap_kwh, 3),
            "morning_pre_solar_shortfall_kwh": round(morning_gap_kwh, 3),
            "morning_solar_start_at": None,
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
    _attr_entity_category = "diagnostic"

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
