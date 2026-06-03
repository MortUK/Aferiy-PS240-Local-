"""Time platform for SMART Config off-peak schedule settings."""

from __future__ import annotations

import logging
from datetime import time as dt_time

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DEFAULT_OFF_PEAK_END,
    DEFAULT_OFF_PEAK_START,
    DOMAIN,
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
            AeccSmartOffPeakStartTime(coordinator, config_entry),
            AeccSmartOffPeakEndTime(coordinator, config_entry),
        ]
    )


def _time_to_hhmm(value: dt_time) -> str:
    return f"{value.hour:02d}:{value.minute:02d}"


def _parse_hhmm(value: str, fallback: str) -> dt_time:
    try:
        hour_s, minute_s = str(value).split(":", 1)
        return dt_time(hour=int(hour_s), minute=int(minute_s[:2]))
    except (TypeError, ValueError):
        hour_s, minute_s = fallback.split(":", 1)
        return dt_time(hour=int(hour_s), minute=int(minute_s))


class AeccSmartOffPeakTime(
    CoordinatorEntity[AeccBatteryCoordinator],
    TimeEntity,
    RestoreEntity,
):
    """Base class for manual SMART Config off-peak time controls."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    coordinator_attr: str = ""
    default_hhmm: str = "00:00"

    def __init__(self, coordinator: AeccBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def native_value(self) -> dt_time | None:
        return _parse_hhmm(
            getattr(self.coordinator, self.coordinator_attr, self.default_hhmm),
            self.default_hhmm,
        )

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    async def async_added_to_hass(self) -> None:
        """Restore manual custom time without forcing Custom mode at startup."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state is None or last_state.state in ("unknown", "unavailable"):
            return

        if getattr(self.coordinator, "smart_tariff_preset", None) != "custom":
            return

        restored = _parse_hhmm(last_state.state, self.default_hhmm)
        setattr(self.coordinator, self.coordinator_attr, _time_to_hhmm(restored))
        self.coordinator.set_smart_tariff_preset("custom")

    async def async_set_value(self, value: dt_time) -> None:
        """Set a manual time and switch the SMART tariff preset to Custom."""
        hhmm = _time_to_hhmm(value)
        if self.coordinator_attr == "manual_off_peak_start":
            self.coordinator.set_manual_off_peak_time(start=hhmm)
        else:
            self.coordinator.set_manual_off_peak_time(end=hhmm)

        _LOGGER.info("Stored AECC SMART off-peak %s as %s", self._attr_name, hhmm)
        self.coordinator.async_set_updated_data(self.coordinator.data or {})
        self.async_write_ha_state()


class AeccSmartOffPeakStartTime(AeccSmartOffPeakTime):
    """Manual off-peak start time for the Custom tariff preset."""

    _attr_name = "Off-Peak Start"
    _attr_icon = "mdi:clock-start"
    coordinator_attr = "manual_off_peak_start"
    default_hhmm = DEFAULT_OFF_PEAK_START

    def __init__(self, coordinator: AeccBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_smart_off_peak_start"


class AeccSmartOffPeakEndTime(AeccSmartOffPeakTime):
    """Manual off-peak end time for the Custom tariff preset."""

    _attr_name = "Off-Peak End"
    _attr_icon = "mdi:clock-end"
    coordinator_attr = "manual_off_peak_end"
    default_hhmm = DEFAULT_OFF_PEAK_END

    def __init__(self, coordinator: AeccBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_smart_off_peak_end"
