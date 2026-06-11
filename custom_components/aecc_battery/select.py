"""Select platform - clean operating mode control."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    BATTERY_CAPACITY_PRESET_MODULE_COUNTS,
    CONF_ADVANCED_ENERGY_SENSORS,
    DEFAULT_BATTERY_CAPACITY_KWH,
    DEFAULT_OFF_PEAK_END,
    DEFAULT_OFF_PEAK_START,
    DEFAULT_TARIFF_PRESET,
    DOMAIN,
    CONF_OFF_PEAK_END,
    CONF_OFF_PEAK_START,
    CONF_TARIFF_PRESET,
    MODE_CUSTOM,
    MODE_SELF_CONSUMPTION,
    MAX_REGISTER_POWER_DEFAULT,
    OVERNIGHT_CHARGE_MODE_FROM_LABEL,
    OVERNIGHT_CHARGE_MODE_LABELS,
    OVERNIGHT_CHARGE_MODE_DISABLED,
    TARIFF_PRESET_LABELS,
    TARIFF_PRESETS,
    WORK_MODES,
    battery_capacity_for_modules,
    battery_capacity_preset_label,
)
from .coordinator import AeccBatteryCoordinator

_LOGGER = logging.getLogger(__name__)

OPERATING_MODE_SELF_GEN = "Self-Gen/Zero Export"
OPERATING_MODE_OPTIONS = [OPERATING_MODE_SELF_GEN, "Idle", "Charge", "Discharge", "Feed"]
DIRECTION_OPTIONS = ["Charge", "Discharge", "Idle"]
CAPACITY_PRESET_OPTIONS = [
    battery_capacity_preset_label(module_count)
    for module_count in BATTERY_CAPACITY_PRESET_MODULE_COUNTS
]
OVERNIGHT_CHARGE_MODE_OPTIONS = list(OVERNIGHT_CHARGE_MODE_LABELS.values())
TARIFF_PRESET_SHORT_LABELS = {
    "snug_octopus": "Snug Octopus",
    "octopus_go": "Octopus Go",
    "octopus_intelligent_go": "Octopus Intelligent Go",
    "eon_next_drive": "E.ON Next Drive",
    "british_gas_electric_driver": "British Gas Electric Driver",
    "british_gas_economy_7": "British Gas Economy 7",
    "custom": "Custom",
}
TARIFF_PRESET_OPTIONS = [TARIFF_PRESET_SHORT_LABELS[value] for value in TARIFF_PRESETS]
TARIFF_PRESET_FROM_LABEL = {
    label: value for value, label in TARIFF_PRESET_SHORT_LABELS.items()
}
SOLAR_AVAILABLE = "Solar Available"
SOLAR_UNAVAILABLE = "Solar Unavailable"
SOLAR_AVAILABILITY_OPTIONS = [SOLAR_AVAILABLE, SOLAR_UNAVAILABLE]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AeccBatteryCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    entities: list[SelectEntity] = [
        AeccOperatingModeSelect(coordinator, config_entry),
        AeccAutomaticOvernightChargingSelect(coordinator, config_entry),
        AeccSmartTariffPresetSelect(coordinator, config_entry),
        AeccSolarAvailabilitySelect(coordinator, config_entry),
    ]
    if config_entry.options.get(CONF_ADVANCED_ENERGY_SENSORS, False):
        entities.append(AeccBatteryCapacityPresetSelect(coordinator, config_entry))
    async_add_entities(entities)


def _clamp(value: int, minimum: int, maximum: int) -> int:
    """Clamp int to range."""
    return max(minimum, min(value, maximum))


def _module_count_from_option(option: str) -> int | None:
    try:
        module_text = option.split(" ", 1)[0]
        return int(module_text)
    except (AttributeError, IndexError, ValueError):
        return None


def _closest_capacity_preset(capacity_kwh: float) -> str:
    closest_count = min(
        BATTERY_CAPACITY_PRESET_MODULE_COUNTS,
        key=lambda count: abs(battery_capacity_for_modules(count) - capacity_kwh),
    )
    return battery_capacity_preset_label(closest_count)


class AeccOperatingModeSelect(CoordinatorEntity[AeccBatteryCoordinator], SelectEntity):
    """Single clean local command mode selector.

    Self-Gen/Zero Export -> robust/safe self-consumption reset
    Idle             -> manual/custom idle
    Charge           -> manual/custom charge using Charge Power
    Discharge        -> manual/custom discharge using Discharge Power
    Feed             -> EMS/base grid-connected feed using Feed Power
    """

    _attr_icon = "mdi:battery-sync"
    _attr_has_entity_name = True
    _attr_name = "Operating Mode"
    _attr_options = OPERATING_MODE_OPTIONS

    def __init__(self, coordinator: AeccBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_operating_mode"

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def current_option(self) -> str | None:
        operating_mode = getattr(self.coordinator, "commanded_operating_mode", None)
        if operating_mode in OPERATING_MODE_OPTIONS:
            return operating_mode

        work_mode = self.coordinator.commanded_work_mode
        direction = self.coordinator.commanded_direction or "Idle"

        if work_mode == MODE_SELF_CONSUMPTION:
            return OPERATING_MODE_SELF_GEN

        if direction in OPERATING_MODE_OPTIONS:
            return direction

        return "Idle"

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        latest_write = self.coordinator.latest_write or {}
        return {
            "source": "local_commanded_state",
            "note": (
                "Cloud/app-originated changes may not update this selector; "
                "use Battery Status and power sensors for observed behaviour."
            ),
            "last_local_command": latest_write.get("operation"),
            "last_local_command_at": latest_write.get("timestamp"),
        }

    async def async_select_option(self, option: str) -> None:
        _LOGGER.info("User selected operating mode: %s", option)

        if option in (OPERATING_MODE_SELF_GEN, "Self-Consumption"):
            success = await self.coordinator.async_set_work_mode(MODE_SELF_CONSUMPTION)
            if success:
                self.coordinator.commanded_direction = "Idle"
                self.coordinator.commanded_work_mode = MODE_SELF_CONSUMPTION
                self.coordinator.commanded_operating_mode = OPERATING_MODE_SELF_GEN
                self.coordinator.async_set_updated_data(self.coordinator.data or {})
                self.async_write_ha_state()
            else:
                _LOGGER.error("Failed to set operating mode to Self-Gen/Zero Export")
            return

        if option == "Idle":
            success = await self.coordinator.async_set_battery_control("Idle", 0)
            if success:
                self.coordinator.commanded_power = 0
                self.coordinator.commanded_direction = "Idle"
                self.coordinator.commanded_work_mode = MODE_CUSTOM
                self.coordinator.commanded_operating_mode = "Idle"
                self.coordinator.async_set_updated_data(self.coordinator.data or {})
                self.async_write_ha_state()
            else:
                _LOGGER.error("Failed to set operating mode to Idle")
            return

        if option == "Charge":
            power = int(getattr(self.coordinator, "commanded_charge_power", 800) or 800)
            power = _clamp(power, 400, 1200)

            success = await self.coordinator.async_set_battery_control("Charge", power)
            if success:
                self.coordinator.commanded_power = power
                self.coordinator.commanded_direction = "Charge"
                self.coordinator.commanded_work_mode = MODE_CUSTOM
                self.coordinator.commanded_operating_mode = "Charge"
                self.coordinator.async_set_updated_data(self.coordinator.data or {})
                self.async_write_ha_state()
            else:
                _LOGGER.error("Failed to set operating mode to Charge")
            return

        if option == "Discharge":
            power = int(getattr(self.coordinator, "commanded_discharge_power", 800) or 800)
            power = _clamp(power, 800, 1200)

            success = await self.coordinator.async_set_battery_control("Discharge", power)
            if success:
                self.coordinator.commanded_power = power
                self.coordinator.commanded_direction = "Discharge"
                self.coordinator.commanded_work_mode = MODE_CUSTOM
                self.coordinator.commanded_operating_mode = "Discharge"
                self.coordinator.async_set_updated_data(self.coordinator.data or {})
                self.async_write_ha_state()
            else:
                _LOGGER.error("Failed to set operating mode to Discharge")
            return

        if option == "Feed":
            power = int(getattr(self.coordinator, "commanded_feed_power", 0) or 0)
            power = _clamp(power, 0, MAX_REGISTER_POWER_DEFAULT)

            success = await self.coordinator.async_set_feed_power(power)
            if success:
                self.coordinator.commanded_power = power
                self.coordinator.commanded_direction = "Feed"
                self.coordinator.commanded_work_mode = MODE_CUSTOM
                self.coordinator.commanded_operating_mode = "Feed"
                self.coordinator.async_set_updated_data(self.coordinator.data or {})
                self.async_write_ha_state()
            else:
                _LOGGER.error("Failed to set operating mode to Feed")
            return

        _LOGGER.warning("Unknown operating mode selected: %s", option)


class AeccBatteryCapacityPresetSelect(
    CoordinatorEntity[AeccBatteryCoordinator],
    SelectEntity,
    RestoreEntity,
):
    """Passive capacity preset selector based on AFERIY module count."""

    _attr_icon = "mdi:battery-high"
    _attr_has_entity_name = True
    _attr_name = "Battery Capacity"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = CAPACITY_PRESET_OPTIONS

    def __init__(self, coordinator: AeccBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_battery_capacity_preset"
        self._selected_option = _closest_capacity_preset(
            float(getattr(coordinator, "battery_capacity_kwh", DEFAULT_BATTERY_CAPACITY_KWH))
        )

    async def async_added_to_hass(self) -> None:
        """Restore the selected preset after restarts."""
        await super().async_added_to_hass()

        if getattr(self.coordinator, "runtime_preferences_loaded", False):
            self._selected_option = _closest_capacity_preset(
                float(getattr(self.coordinator, "battery_capacity_kwh", DEFAULT_BATTERY_CAPACITY_KWH))
            )
            return

        last_state = await self.async_get_last_state()
        if last_state is None or last_state.state not in CAPACITY_PRESET_OPTIONS:
            self._selected_option = _closest_capacity_preset(
                float(getattr(self.coordinator, "battery_capacity_kwh", DEFAULT_BATTERY_CAPACITY_KWH))
            )
            return

        self._selected_option = last_state.state
        module_count = _module_count_from_option(self._selected_option)
        if module_count is not None:
            self.coordinator.battery_capacity_kwh = battery_capacity_for_modules(module_count)
            await self.coordinator.async_save_runtime_preferences(
                battery_capacity_kwh=round(self.coordinator.battery_capacity_kwh, 3)
            )

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def current_option(self) -> str | None:
        capacity = float(
            getattr(self.coordinator, "battery_capacity_kwh", DEFAULT_BATTERY_CAPACITY_KWH)
        )
        return _closest_capacity_preset(capacity)

    @property
    def available(self) -> bool:
        return True

    async def async_select_option(self, option: str) -> None:
        module_count = _module_count_from_option(option)
        if module_count is None or option not in CAPACITY_PRESET_OPTIONS:
            _LOGGER.warning("Unknown battery capacity preset selected: %s", option)
            return

        capacity_kwh = battery_capacity_for_modules(module_count)
        self._selected_option = option
        self.coordinator.battery_capacity_kwh = capacity_kwh
        await self.coordinator.async_save_runtime_preferences(
            battery_capacity_kwh=round(capacity_kwh, 3)
        )
        _LOGGER.info(
            "Stored AECC battery capacity preset %s as %.3f kWh. No battery command sent.",
            option,
            capacity_kwh,
        )
        self.coordinator.async_set_updated_data(self.coordinator.data or {})
        self.async_write_ha_state()


class AeccAutomaticOvernightChargingSelect(
    CoordinatorEntity[AeccBatteryCoordinator],
    SelectEntity,
    RestoreEntity,
):
    """SMART Config selector for the local overnight charge scheduler."""

    _attr_icon = "mdi:calendar-clock"
    _attr_has_entity_name = True
    _attr_name = "Overnight Charge"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = OVERNIGHT_CHARGE_MODE_OPTIONS

    def __init__(self, coordinator: AeccBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_automatic_overnight_charging"
        self._selected_mode = getattr(
            coordinator,
            "overnight_charging_mode",
            OVERNIGHT_CHARGE_MODE_DISABLED,
        )

    async def async_added_to_hass(self) -> None:
        """Restore the selected scheduler mode after restarts."""
        await super().async_added_to_hass()

        if not getattr(self.coordinator, "runtime_preferences_loaded", False):
            last_state = await self.async_get_last_state()
            if last_state is not None and last_state.state in OVERNIGHT_CHARGE_MODE_FROM_LABEL:
                self._selected_mode = OVERNIGHT_CHARGE_MODE_FROM_LABEL[last_state.state]
        else:
            self._selected_mode = getattr(
                self.coordinator,
                "overnight_charging_mode",
                OVERNIGHT_CHARGE_MODE_DISABLED,
            )

        self.coordinator.set_overnight_charging_mode(self._selected_mode)
        await self.coordinator.async_save_runtime_preferences(
            overnight_charging_mode=self._selected_mode
        )

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def current_option(self) -> str | None:
        mode = getattr(self.coordinator, "overnight_charging_mode", self._selected_mode)
        return OVERNIGHT_CHARGE_MODE_LABELS.get(mode, "Off")

    @property
    def available(self) -> bool:
        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        status = self.coordinator.overnight_charging_status
        return {
            "status": status.get("state"),
            "reason": status.get("reason"),
            "off_peak_start": getattr(self.coordinator, "off_peak_start", None),
            "off_peak_end": getattr(self.coordinator, "off_peak_end", None),
            "target_source": status.get("target_source"),
            "target_soc": status.get("target_soc"),
            "note": (
                "On uses Recommended Overnight SOC. Manual uses the SMART Config "
                "Manual Overnight Target slider."
            ),
        }

    async def async_select_option(self, option: str) -> None:
        if option not in OVERNIGHT_CHARGE_MODE_FROM_LABEL:
            _LOGGER.warning("Unknown overnight charging mode selected: %s", option)
            return

        mode = OVERNIGHT_CHARGE_MODE_FROM_LABEL[option]
        self._selected_mode = mode
        self.coordinator.set_overnight_charging_mode(mode)
        await self.coordinator.async_save_runtime_preferences(overnight_charging_mode=mode)
        self.coordinator.async_set_updated_data(self.coordinator.data or {})
        self.async_write_ha_state()


class AeccSmartTariffPresetSelect(
    CoordinatorEntity[AeccBatteryCoordinator],
    SelectEntity,
    RestoreEntity,
):
    """SMART Config tariff preset selector used by local overnight charging."""

    _attr_icon = "mdi:clock-star-four-points"
    _attr_has_entity_name = True
    _attr_name = "Off-Peak Tariff"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = TARIFF_PRESET_OPTIONS

    def __init__(self, coordinator: AeccBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_smart_tariff_preset"
        self._selected_preset = config_entry.options.get(
            CONF_TARIFF_PRESET,
            DEFAULT_TARIFF_PRESET,
        )

    async def async_added_to_hass(self) -> None:
        """Restore the SMART tariff preset without reloading the integration."""
        await super().async_added_to_hass()

        if not getattr(self.coordinator, "runtime_preferences_loaded", False):
            last_state = await self.async_get_last_state()
            if last_state is not None and last_state.state in TARIFF_PRESET_FROM_LABEL:
                self._selected_preset = TARIFF_PRESET_FROM_LABEL[last_state.state]
        else:
            self._selected_preset = getattr(
                self.coordinator,
                "smart_tariff_preset",
                DEFAULT_TARIFF_PRESET,
            )

        self.coordinator.set_smart_tariff_preset(self._selected_preset)
        await self.coordinator.async_save_runtime_preferences(
            smart_tariff_preset=self._selected_preset,
            off_peak_start=getattr(self.coordinator, "off_peak_start", DEFAULT_OFF_PEAK_START),
            off_peak_end=getattr(self.coordinator, "off_peak_end", DEFAULT_OFF_PEAK_END),
            manual_off_peak_start=getattr(
                self.coordinator,
                "manual_off_peak_start",
                DEFAULT_OFF_PEAK_START,
            ),
            manual_off_peak_end=getattr(
                self.coordinator,
                "manual_off_peak_end",
                DEFAULT_OFF_PEAK_END,
            ),
        )

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def current_option(self) -> str | None:
        preset = getattr(self.coordinator, "smart_tariff_preset", self._selected_preset)
        return TARIFF_PRESET_SHORT_LABELS.get(
            preset,
            TARIFF_PRESET_SHORT_LABELS[DEFAULT_TARIFF_PRESET],
        )

    @property
    def available(self) -> bool:
        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        preset = getattr(self.coordinator, "smart_tariff_preset", self._selected_preset)
        start, end = self._window_for_preset(preset)
        return {
            "preset": preset,
            "preset_label": TARIFF_PRESET_LABELS.get(preset),
            "off_peak_start": start,
            "off_peak_end": end,
            "effective_charge_start": self._add_minutes(start, 1),
            "effective_charge_end": self._add_minutes(end, -5),
            "note": (
                "The integration starts 1 minute after off-peak begins and restores "
                "Self-Gen/Zero Export 5 minutes before it ends."
            ),
        }

    async def async_select_option(self, option: str) -> None:
        if option not in TARIFF_PRESET_FROM_LABEL:
            _LOGGER.warning("Unknown tariff preset selected: %s", option)
            return

        preset = TARIFF_PRESET_FROM_LABEL[option]
        start, end = self._window_for_preset(preset)
        self._selected_preset = preset
        self.coordinator.set_smart_tariff_preset(preset)
        if preset == "custom":
            start, end = self._window_for_preset(preset)
            self.coordinator.set_off_peak_window(start, end)
        await self.coordinator.async_save_runtime_preferences(
            smart_tariff_preset=preset,
            off_peak_start=start,
            off_peak_end=end,
            manual_off_peak_start=getattr(self.coordinator, "manual_off_peak_start", start),
            manual_off_peak_end=getattr(self.coordinator, "manual_off_peak_end", end),
        )
        self.coordinator.async_set_updated_data(self.coordinator.data or {})
        self.async_write_ha_state()

    def _window_for_preset(self, preset: str) -> tuple[str, str]:
        if preset == "custom":
            return (
                getattr(self.coordinator, "manual_off_peak_start", DEFAULT_OFF_PEAK_START),
                getattr(self.coordinator, "manual_off_peak_end", DEFAULT_OFF_PEAK_END),
            )
        return TARIFF_PRESETS.get(
            preset,
            (DEFAULT_OFF_PEAK_START, DEFAULT_OFF_PEAK_END),
        )

    @staticmethod
    def _add_minutes(value: str, minutes: int) -> str:
        try:
            hour_s, minute_s = value.split(":", 1)
            total_minutes = (int(hour_s) * 60 + int(minute_s) + minutes) % (24 * 60)
            return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"
        except (AttributeError, TypeError, ValueError):
            return value


class AeccSolarAvailabilitySelect(
    CoordinatorEntity[AeccBatteryCoordinator],
    SelectEntity,
    RestoreEntity,
):
    """SMART Config selector for whether forecast solar should be considered available."""

    _attr_icon = "mdi:solar-power"
    _attr_has_entity_name = True
    _attr_name = "Solar Availability"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = SOLAR_AVAILABILITY_OPTIONS

    def __init__(self, coordinator: AeccBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_solar_availability"
        self._selected_option = SOLAR_AVAILABLE

    async def async_added_to_hass(self) -> None:
        """Restore solar availability mode after restarts."""
        await super().async_added_to_hass()

        if getattr(self.coordinator, "runtime_preferences_loaded", False):
            self._selected_option = (
                SOLAR_UNAVAILABLE
                if bool(getattr(self.coordinator, "solar_unavailable_override", False))
                else SOLAR_AVAILABLE
            )
        else:
            last_state = await self.async_get_last_state()
            if last_state is not None and last_state.state in SOLAR_AVAILABILITY_OPTIONS:
                self._selected_option = last_state.state
            else:
                self._selected_option = SOLAR_AVAILABLE

        self.coordinator.solar_unavailable_override = self._selected_option == SOLAR_UNAVAILABLE
        await self.coordinator.async_save_runtime_preferences(
            solar_unavailable_override=self.coordinator.solar_unavailable_override
        )

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def current_option(self) -> str | None:
        if bool(getattr(self.coordinator, "solar_unavailable_override", False)):
            return SOLAR_UNAVAILABLE
        return SOLAR_AVAILABLE

    @property
    def available(self) -> bool:
        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "solar_unavailable_override": bool(
                getattr(self.coordinator, "solar_unavailable_override", False)
            ),
            "status": (
                "Batteries Only"
                if bool(getattr(self.coordinator, "solar_unavailable_override", False))
                else "Forecast solar"
            ),
            "note": (
                "Solar Unavailable tells the overnight recommendation to treat "
                "forecast solar as zero, for example if panels are covered."
            ),
        }

    async def async_select_option(self, option: str) -> None:
        if option not in SOLAR_AVAILABILITY_OPTIONS:
            _LOGGER.warning("Unknown solar availability option selected: %s", option)
            return

        self._selected_option = option
        self.coordinator.solar_unavailable_override = option == SOLAR_UNAVAILABLE
        await self.coordinator.async_save_runtime_preferences(
            solar_unavailable_override=self.coordinator.solar_unavailable_override
        )
        self.coordinator.async_set_updated_data(self.coordinator.data or {})
        self.async_write_ha_state()


class AeccWorkModeSelect(CoordinatorEntity[AeccBatteryCoordinator], SelectEntity):
    """Raw Work Mode selector. Keep hidden from normal dashboards."""

    _attr_icon = "mdi:battery-sync"
    _attr_has_entity_name = True
    _attr_name = "Work Mode"
    _attr_options = WORK_MODES

    def __init__(
        self,
        coordinator: AeccBatteryCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_work_mode"
        self._current_mode: str | None = coordinator.initial_work_mode
        if self._current_mode:
            coordinator.commanded_work_mode = self._current_mode

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def current_option(self) -> str | None:
        return self.coordinator.commanded_work_mode or self._current_mode

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    async def async_select_option(self, option: str) -> None:
        _LOGGER.info("User selected work mode: %s", option)
        success = await self.coordinator.async_set_work_mode(option)
        if success:
            self._current_mode = option
            self.coordinator.commanded_work_mode = option

            if option == MODE_SELF_CONSUMPTION:
                self.coordinator.commanded_direction = "Idle"
                self.coordinator.commanded_operating_mode = OPERATING_MODE_SELF_GEN
            else:
                self.coordinator.commanded_operating_mode = None

            self.coordinator.async_set_updated_data(self.coordinator.data or {})
            self.async_write_ha_state()
        else:
            _LOGGER.error("Failed to set work mode to '%s'", option)


class AeccBatteryDirection(CoordinatorEntity[AeccBatteryCoordinator], SelectEntity):
    """Raw Battery Direction selector. Keep hidden from normal dashboards."""

    _attr_icon = "mdi:battery-charging-wireless"
    _attr_has_entity_name = True
    _attr_name = "Battery Direction"
    _attr_options = DIRECTION_OPTIONS

    def __init__(self, coordinator: AeccBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_battery_direction"

        charge = coordinator.get_value("battery_charging_power") or 0
        discharge = coordinator.get_value("battery_discharging_power") or 0

        try:
            if float(charge) > 0:
                self._current_direction = "Charge"
            elif float(discharge) > 0:
                self._current_direction = "Discharge"
            else:
                self._current_direction = "Idle"
        except (TypeError, ValueError):
            self._current_direction = "Idle"

        coordinator.commanded_direction = self._current_direction

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def current_option(self) -> str:
        return self.coordinator.commanded_direction or self._current_direction

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    async def async_select_option(self, option: str) -> None:
        _LOGGER.info("User selected raw battery direction: %s", option)

        if option == "Idle":
            power = 0
        elif option == "Charge":
            power = int(getattr(self.coordinator, "commanded_charge_power", 800) or 800)
            power = _clamp(power, 400, 1200)
        else:
            power = int(getattr(self.coordinator, "commanded_discharge_power", 800) or 800)
            power = _clamp(power, 800, 1200)

        success = await self.coordinator.async_set_battery_control(option, power)
        if success:
            self._current_direction = option
            self.coordinator.commanded_power = power
            self.coordinator.commanded_direction = option
            self.coordinator.commanded_work_mode = MODE_CUSTOM
            self.coordinator.commanded_operating_mode = option
            self.coordinator.async_set_updated_data(self.coordinator.data or {})
            self.async_write_ha_state()
        else:
            _LOGGER.error("Failed to set raw battery direction to '%s'", option)
