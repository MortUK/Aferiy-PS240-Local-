"""Number platform - separate charge/discharge power sliders, Min SOC, Max SOC.

Patched behaviour:
- Charge Power is passive: stores desired charge power only.
- Discharge Power is passive: stores desired discharge power only.
- Sliders do NOT send charge/discharge commands by themselves.
- Operating Mode applies the selected value.
"""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DEFAULT_BATTERY_CAPACITY_KWH,
    DOMAIN,
    MAX_REGISTER_POWER_DEFAULT,
    OVERNIGHT_CHARGE_MODE_MANUAL,
    PS240_EXPERIMENTAL_MAX_OUTPUT_W,
)
from .coordinator import AeccBatteryCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AeccBatteryCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities(
        [
            AeccChargePowerSlider(coordinator, config_entry),
            AeccDischargePowerSlider(coordinator, config_entry),
            AeccFeedPowerSlider(coordinator, config_entry),
            AeccPvSurplusChargeTrigger(coordinator, config_entry),
            AeccSmartOvernightBuffer(coordinator, config_entry),
            AeccManualOvernightChargeTarget(coordinator, config_entry),
            AeccMinSoc(coordinator, config_entry),
            AeccMaxSoc(coordinator, config_entry),
        ]
    )


def _clamp_number(value: float, minimum: float, maximum: float) -> float:
    """Clamp a number to the supplied range."""
    return max(minimum, min(value, maximum))


class AeccPassivePowerSlider(
    CoordinatorEntity[AeccBatteryCoordinator], NumberEntity, RestoreEntity
):
    """Base class for passive charge/discharge target sliders."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:battery-sync"
    _attr_device_class = NumberDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_native_step = 100
    _attr_mode = NumberMode.SLIDER

    attr_name_on_coordinator: str = ""
    default_value: int = 800

    def __init__(self, coordinator: AeccBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._commanded: float = self.default_value

    async def async_added_to_hass(self) -> None:
        """Restore previous slider value after Home Assistant restarts.

        Restoring this value only restores the dashboard slider and coordinator
        memory. It does not send a command to the battery.
        """
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state is not None:
            try:
                restored = float(last_state.state)
                restored = _clamp_number(
                    restored,
                    float(self._attr_native_min_value),
                    float(self._attr_native_max_value),
                )
                self._commanded = restored
            except (TypeError, ValueError):
                self._commanded = self.default_value

        setattr(self.coordinator, self.attr_name_on_coordinator, int(self._commanded))
        _LOGGER.info(
            "Restored AECC %s to %s W per unit",
            self._attr_name,
            int(self._commanded),
        )

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def native_value(self) -> float:
        return self._commanded

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    async def async_set_native_value(self, value: float) -> None:
        """Store desired per-device power without sending a battery command."""
        power_w = int(value)
        power_w = int(
            _clamp_number(
                power_w,
                float(self._attr_native_min_value),
                float(self._attr_native_max_value),
            )
        )

        self._commanded = power_w
        setattr(self.coordinator, self.attr_name_on_coordinator, power_w)

        # Keep the legacy commanded_power in sync for backwards compatibility.
        self.coordinator.commanded_power = power_w

        _LOGGER.info(
            "Stored AECC %s as %s W per unit. No battery command sent.",
            self._attr_name,
            power_w,
        )

        self.async_write_ha_state()


class AeccChargePowerSlider(AeccPassivePowerSlider):
    """Charge power target slider.

    Local AECC charge control appears to treat this value as per-device power.
    The AFERIY PS240 appears to clamp lower requests to 800 W per unit.
    """

    _attr_name = "Charge Power"
    _attr_icon = "mdi:battery-arrow-up"
    _attr_native_min_value = MAX_REGISTER_POWER_DEFAULT
    _attr_native_max_value = PS240_EXPERIMENTAL_MAX_OUTPUT_W
    _attr_unique_id_suffix = "charge_power_target"
    attr_name_on_coordinator = "commanded_charge_power"
    default_value = 800

    def __init__(self, coordinator: AeccBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_charge_power_target"


class AeccDischargePowerSlider(AeccPassivePowerSlider):
    """Discharge power target slider.

    Discharge is kept at 800–1200 W per unit.
    """

    _attr_name = "Discharge Power"
    _attr_icon = "mdi:battery-arrow-down"
    _attr_native_min_value = MAX_REGISTER_POWER_DEFAULT
    _attr_native_max_value = PS240_EXPERIMENTAL_MAX_OUTPUT_W
    _attr_unique_id_suffix = "discharge_power_target"
    attr_name_on_coordinator = "commanded_discharge_power"
    default_value = 800

    def __init__(self, coordinator: AeccBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_discharge_power_target"


class AeccFeedPowerSlider(AeccPassivePowerSlider):
    """Base feed power target slider.

    This is applied by the Operating Mode "Feed" option. It maps to the
    cloud-app "Battery base grid-connected power" setting and is separate
    from the manual schedule Discharge mode.
    """

    _attr_name = "Base Feed Power"
    _attr_icon = "mdi:transmission-tower-export"
    _attr_native_min_value = 0
    _attr_native_max_value = MAX_REGISTER_POWER_DEFAULT
    _attr_native_step = 10
    _attr_unique_id_suffix = "feed_power_target"
    attr_name_on_coordinator = "commanded_feed_power"
    default_value = 0

    def __init__(self, coordinator: AeccBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_feed_power_target"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.coordinator.initial_base_discharge_power is not None:
            self._commanded = _clamp_number(
                self.coordinator.initial_base_discharge_power,
                float(self._attr_native_min_value),
                float(self._attr_native_max_value),
            )
            self.coordinator.commanded_feed_power = int(self._commanded)
            self.async_write_ha_state()


class AeccPvSurplusChargeTrigger(
    CoordinatorEntity[AeccBatteryCoordinator],
    NumberEntity,
    RestoreEntity,
):
    """PV surplus threshold that triggers grid-connected battery charging."""

    _attr_has_entity_name = True
    _attr_name = "PV Surplus Charge Trigger"
    _attr_icon = "mdi:solar-power-variant"
    _attr_device_class = NumberDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_native_min_value = 0
    _attr_native_max_value = 50
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: AeccBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_pv_surplus_charge_trigger"
        initial = coordinator.initial_surplus_charge_trigger
        self._commanded: float = float(initial if initial is not None else 50)

    async def async_added_to_hass(self) -> None:
        """Restore previous value only if the device did not report one."""
        await super().async_added_to_hass()

        if self.coordinator.initial_surplus_charge_trigger is not None:
            self._commanded = self.coordinator.initial_surplus_charge_trigger
            return

        last_state = await self.async_get_last_state()
        if last_state is None:
            return

        try:
            restored = float(last_state.state)
        except (TypeError, ValueError):
            return

        self._commanded = _clamp_number(
            restored,
            float(self._attr_native_min_value),
            float(self._attr_native_max_value),
        )

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def native_value(self) -> float:
        return self._commanded

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    async def async_set_native_value(self, value: float) -> None:
        trigger_w = int(
            _clamp_number(
                float(value),
                float(self._attr_native_min_value),
                float(self._attr_native_max_value),
            )
        )
        success = await self.coordinator.async_set_surplus_charge_trigger(trigger_w)
        if success:
            self._commanded = trigger_w
            self.async_write_ha_state()
        else:
            _LOGGER.warning("Failed to set PV surplus charge trigger to %s W", trigger_w)


class AeccSmartOvernightBuffer(
    CoordinatorEntity[AeccBatteryCoordinator],
    NumberEntity,
    RestoreEntity,
):
    """User-selected baseline buffer for SMART overnight charging."""

    _attr_has_entity_name = True
    _attr_name = "Overnight Buffer"
    _attr_icon = "mdi:shield-battery"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_native_min_value = 0
    _attr_native_max_value = 20
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: AeccBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_smart_overnight_buffer_soc"
        self._commanded: float = 3

    async def async_added_to_hass(self) -> None:
        """Restore the buffer without touching the battery."""
        await super().async_added_to_hass()

        self._commanded = _clamp_number(
            float(getattr(self.coordinator, "smart_overnight_buffer_soc", 3.0)),
            float(self._attr_native_min_value),
            float(self._attr_native_max_value),
        )

        last_state = await self.async_get_last_state()
        if last_state is not None:
            try:
                self._commanded = _clamp_number(
                    float(last_state.state),
                    float(self._attr_native_min_value),
                    float(self._attr_native_max_value),
                )
            except (TypeError, ValueError):
                pass

        self.coordinator.smart_overnight_buffer_soc = self._commanded
        await self.coordinator.async_save_runtime_preferences(
            smart_overnight_buffer_soc=self._commanded
        )

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def native_value(self) -> float:
        return round(float(getattr(self.coordinator, "smart_overnight_buffer_soc", 3.0)), 0)

    @property
    def available(self) -> bool:
        return True

    async def async_set_native_value(self, value: float) -> None:
        buffer_soc = int(
            _clamp_number(
                float(value),
                float(self._attr_native_min_value),
                float(self._attr_native_max_value),
            )
        )
        self._commanded = buffer_soc
        self.coordinator.smart_overnight_buffer_soc = float(buffer_soc)
        await self.coordinator.async_save_runtime_preferences(
            smart_overnight_buffer_soc=float(buffer_soc)
        )
        self.coordinator.async_set_updated_data(self.coordinator.data or {})
        self.async_write_ha_state()


class AeccBatteryCapacity(CoordinatorEntity[AeccBatteryCoordinator], NumberEntity, RestoreEntity):
    """Editable total usable battery capacity used by display-only estimates."""

    _attr_has_entity_name = True
    _attr_name = "Battery Capacity"
    _attr_icon = "mdi:battery-high"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_native_min_value = 1
    _attr_native_max_value = 30
    _attr_native_step = 0.001
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: AeccBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_battery_capacity"
        self._capacity_kwh: float = float(
            getattr(coordinator, "battery_capacity_kwh", DEFAULT_BATTERY_CAPACITY_KWH)
        )

    async def async_added_to_hass(self) -> None:
        """Restore the capacity after restarts without sending device commands."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state is not None:
            try:
                restored = float(last_state.state)
                if abs(restored - 5.82) < 0.05:
                    restored = DEFAULT_BATTERY_CAPACITY_KWH
                self._capacity_kwh = _clamp_number(
                    restored,
                    float(self._attr_native_min_value),
                    float(self._attr_native_max_value),
                )
            except (TypeError, ValueError):
                self._capacity_kwh = DEFAULT_BATTERY_CAPACITY_KWH

        self.coordinator.battery_capacity_kwh = self._capacity_kwh
        _LOGGER.info("Restored AECC battery capacity to %.2f kWh", self._capacity_kwh)

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def native_value(self) -> float:
        return round(float(getattr(self.coordinator, "battery_capacity_kwh", self._capacity_kwh)), 3)

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    async def async_set_native_value(self, value: float) -> None:
        """Store total battery capacity locally for estimate sensors only."""
        capacity = _clamp_number(
            float(value),
            float(self._attr_native_min_value),
            float(self._attr_native_max_value),
        )
        self._capacity_kwh = round(capacity, 2)
        self.coordinator.battery_capacity_kwh = self._capacity_kwh
        _LOGGER.info(
            "Stored AECC battery capacity as %.2f kWh. No battery command sent.",
            self._capacity_kwh,
        )
        self.async_write_ha_state()


class AeccManualOvernightChargeTarget(
    CoordinatorEntity[AeccBatteryCoordinator],
    NumberEntity,
    RestoreEntity,
):
    """Manual SOC target used by the SMART Config overnight scheduler."""

    _attr_has_entity_name = True
    _attr_name = "Manual SOC"
    _attr_icon = "mdi:battery-clock"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_class = NumberDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_native_min_value = 10
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: AeccBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_manual_overnight_charge_target"
        self._commanded: float = float(getattr(coordinator, "manual_overnight_target_soc", 80))

    async def async_added_to_hass(self) -> None:
        """Restore the manual overnight target without sending a battery command."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state is not None:
            try:
                restored = float(last_state.state)
                self._commanded = _clamp_number(
                    restored,
                    float(self._attr_native_min_value),
                    float(self._attr_native_max_value),
                )
            except (TypeError, ValueError):
                self._commanded = 80

        self.coordinator.manual_overnight_target_soc = int(self._commanded)
        await self.coordinator.async_save_runtime_preferences(
            manual_overnight_target_soc=int(self._commanded)
        )
        _LOGGER.info(
            "Restored AECC manual overnight charge target to %s%%",
            int(self._commanded),
        )

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def native_value(self) -> float:
        return float(getattr(self.coordinator, "manual_overnight_target_soc", self._commanded))

    @property
    def available(self) -> bool:
        return getattr(self.coordinator, "overnight_charging_mode", None) == OVERNIGHT_CHARGE_MODE_MANUAL

    async def async_set_native_value(self, value: float) -> None:
        target = int(
            _clamp_number(
                float(value),
                float(self._attr_native_min_value),
                float(self._attr_native_max_value),
            )
        )
        self._commanded = target
        self.coordinator.manual_overnight_target_soc = target
        await self.coordinator.async_save_runtime_preferences(
            manual_overnight_target_soc=target
        )
        self.coordinator.async_set_updated_data(self.coordinator.data or {})
        self.async_write_ha_state()


class AeccMinSoc(CoordinatorEntity[AeccBatteryCoordinator], NumberEntity, RestoreEntity):
    """Minimum discharge SOC (register 3023)."""

    _attr_has_entity_name = True
    _attr_name = "Discharge Limit"
    _attr_icon = "mdi:battery-arrow-down"
    _attr_device_class = NumberDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_native_min_value = 5
    _attr_native_max_value = 50
    _attr_native_step = 5
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: AeccBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_min_soc"
        self._commanded: float = coordinator.initial_min_soc if coordinator.initial_min_soc is not None else 10

    async def async_added_to_hass(self) -> None:
        """Restore previous value only if the device did not report one."""
        await super().async_added_to_hass()

        if self.coordinator.initial_min_soc is not None:
            self._commanded = self.coordinator.initial_min_soc
            return

        last_state = await self.async_get_last_state()
        if last_state is None:
            return

        try:
            restored = float(last_state.state)
        except (TypeError, ValueError):
            return

        self._commanded = _clamp_number(
            restored,
            float(self._attr_native_min_value),
            float(self._attr_native_max_value),
        )

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def native_value(self) -> float:
        return self._commanded

    async def async_set_native_value(self, value: float) -> None:
        soc = int(value)
        success = await self.coordinator.async_set_min_soc(soc)
        if success:
            self._commanded = soc
            self.async_write_ha_state()
        else:
            _LOGGER.warning("Failed to set min SOC to %s%%", soc)


class AeccMaxSoc(CoordinatorEntity[AeccBatteryCoordinator], NumberEntity, RestoreEntity):
    """Maximum charge SOC (register 3024)."""

    _attr_has_entity_name = True
    _attr_name = "Charge Limit"
    _attr_icon = "mdi:battery-arrow-up"
    _attr_device_class = NumberDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_native_min_value = 10
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: AeccBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_max_soc"
        self._commanded: float = coordinator.initial_max_soc if coordinator.initial_max_soc is not None else 98

    async def async_added_to_hass(self) -> None:
        """Restore previous value only if the device did not report one."""
        await super().async_added_to_hass()

        if self.coordinator.initial_max_soc is not None:
            self._commanded = self.coordinator.initial_max_soc
            return

        last_state = await self.async_get_last_state()
        if last_state is None:
            return

        try:
            restored = float(last_state.state)
        except (TypeError, ValueError):
            return

        self._commanded = _clamp_number(
            restored,
            float(self._attr_native_min_value),
            float(self._attr_native_max_value),
        )

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def native_value(self) -> float:
        return self._commanded

    async def async_set_native_value(self, value: float) -> None:
        soc = int(value)
        success = await self.coordinator.async_set_max_soc(soc)
        if success:
            self._commanded = soc
            self.async_write_ha_state()
        else:
            _LOGGER.warning("Failed to set max SOC to %s%%", soc)
