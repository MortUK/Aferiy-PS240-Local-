"""Select platform - clean operating mode control."""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    BATTERY_CAPACITY_PRESET_MODULE_COUNTS,
    DEFAULT_BATTERY_CAPACITY_KWH,
    DOMAIN,
    MODE_CUSTOM,
    MODE_SELF_CONSUMPTION,
    WORK_MODES,
    battery_capacity_for_modules,
    battery_capacity_preset_label,
)
from .coordinator import AeccBatteryCoordinator

_LOGGER = logging.getLogger(__name__)

OPERATING_MODE_SELF_GEN = "Self-Gen/Zero Export"
OPERATING_MODE_OPTIONS = [OPERATING_MODE_SELF_GEN, "Idle", "Charge", "Discharge"]
DIRECTION_OPTIONS = ["Charge", "Discharge", "Idle"]
CAPACITY_PRESET_OPTIONS = [
    battery_capacity_preset_label(module_count)
    for module_count in BATTERY_CAPACITY_PRESET_MODULE_COUNTS
]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AeccBatteryCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities(
        [
            AeccOperatingModeSelect(coordinator, config_entry),
            AeccBatteryCapacityPresetSelect(coordinator, config_entry),
        ]
    )


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
    """Single clean operating mode selector.

    Self-Gen/Zero Export -> robust/safe self-consumption reset
    Idle             -> manual/custom idle
    Charge           -> manual/custom charge using Charge Power
    Discharge        -> manual/custom discharge using Discharge Power
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

        _LOGGER.warning("Unknown operating mode selected: %s", option)


class AeccBatteryCapacityPresetSelect(
    CoordinatorEntity[AeccBatteryCoordinator],
    SelectEntity,
    RestoreEntity,
):
    """Passive capacity preset selector based on AFERIY module count."""

    _attr_icon = "mdi:battery-high"
    _attr_has_entity_name = True
    _attr_name = "Battery Capacity Preset"
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
        return self.coordinator.last_update_success

    async def async_select_option(self, option: str) -> None:
        module_count = _module_count_from_option(option)
        if module_count is None or option not in CAPACITY_PRESET_OPTIONS:
            _LOGGER.warning("Unknown battery capacity preset selected: %s", option)
            return

        capacity_kwh = battery_capacity_for_modules(module_count)
        self._selected_option = option
        self.coordinator.battery_capacity_kwh = capacity_kwh
        _LOGGER.info(
            "Stored AECC battery capacity preset %s as %.3f kWh. No battery command sent.",
            option,
            capacity_kwh,
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
