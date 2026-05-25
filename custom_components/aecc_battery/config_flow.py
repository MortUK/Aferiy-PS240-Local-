"""Config flow for AECC Battery (Local TCP) integration."""

from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_ADVANCED_ENERGY_SENSORS,
    CONF_DEPENDENCY_GRID_METER,
    CONF_DEPENDENCY_HOME_OCCUPANCY,
    CONF_DEPENDENCY_OCTOPUS_ENERGY,
    CONF_DEPENDENCY_RECORDER,
    CONF_DEPENDENCY_SOLCAST,
    CONF_OFF_PEAK_END,
    CONF_OFF_PEAK_START,
    CONF_TARIFF_PRESET,
    CONF_HOST,
    CONF_MANUFACTURER,
    CONF_MODEL,
    CONF_NAME,
    CONF_POLL_INTERVAL,
    CONF_PORT,
    DEFAULT_HOST,
    DEFAULT_MANUFACTURER,
    DEFAULT_MODEL,
    DEFAULT_NAME,
    DEFAULT_OFF_PEAK_END,
    DEFAULT_OFF_PEAK_START,
    DEFAULT_TARIFF_PRESET,
    DEFAULT_PORT,
    DOMAIN,
    MIN_POLL_INTERVAL,
    POLL_INTERVAL,
    TARIFF_PRESET_LABELS,
    TARIFF_PRESETS,
)

_LOGGER = logging.getLogger(__name__)
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_TARIFF_PRESET_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=[
            selector.SelectOptionDict(value=value, label=TARIFF_PRESET_LABELS[value])
            for value in TARIFF_PRESETS
        ],
        mode=selector.SelectSelectorMode.DROPDOWN,
    )
)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.Coerce(int),
        vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
    }
)


class AeccBatteryConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the initial configuration step."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> AeccBatteryOptionsFlow:
        return AeccBatteryOptionsFlow(config_entry)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input[CONF_PORT]
            name = user_input[CONF_NAME].strip()

            await self.async_set_unique_id(f"{host}:{port}")
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=name,
                data={
                    CONF_HOST: host,
                    CONF_PORT: port,
                    CONF_NAME: name,
                    CONF_MANUFACTURER: DEFAULT_MANUFACTURER,
                    CONF_MODEL: DEFAULT_MODEL,
                },
            )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            description_placeholders={"default_host": DEFAULT_HOST},
        )


class AeccBatteryOptionsFlow(config_entries.OptionsFlow):
    """Allow the user to update host/port/name without removing the entry."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            errors: dict[str, str] = {}
            tariff_preset = user_input.get(CONF_TARIFF_PRESET, DEFAULT_TARIFF_PRESET)
            preset_start, preset_end = TARIFF_PRESETS.get(
                tariff_preset,
                (DEFAULT_OFF_PEAK_START, DEFAULT_OFF_PEAK_END),
            )
            if tariff_preset == "custom":
                off_peak_start = user_input.get(CONF_OFF_PEAK_START, DEFAULT_OFF_PEAK_START).strip()
                off_peak_end = user_input.get(CONF_OFF_PEAK_END, DEFAULT_OFF_PEAK_END).strip()
            else:
                off_peak_start = preset_start
                off_peak_end = preset_end
            if not _TIME_RE.match(off_peak_start):
                errors[CONF_OFF_PEAK_START] = "invalid_time"
            if not _TIME_RE.match(off_peak_end):
                errors[CONF_OFF_PEAK_END] = "invalid_time"
            if errors:
                return self.async_show_form(
                    step_id="init",
                    data_schema=self._options_schema(user_input),
                    errors=errors,
                )

            new_options = {
                CONF_ADVANCED_ENERGY_SENSORS: user_input.get(CONF_ADVANCED_ENERGY_SENSORS, False),
                CONF_POLL_INTERVAL: user_input.get(CONF_POLL_INTERVAL, POLL_INTERVAL),
                CONF_TARIFF_PRESET: tariff_preset,
                CONF_OFF_PEAK_START: off_peak_start,
                CONF_OFF_PEAK_END: off_peak_end,
                CONF_DEPENDENCY_SOLCAST: user_input.get(CONF_DEPENDENCY_SOLCAST, False),
                CONF_DEPENDENCY_GRID_METER: user_input.get(CONF_DEPENDENCY_GRID_METER, False),
                CONF_DEPENDENCY_RECORDER: user_input.get(CONF_DEPENDENCY_RECORDER, False),
                CONF_DEPENDENCY_OCTOPUS_ENERGY: user_input.get(CONF_DEPENDENCY_OCTOPUS_ENERGY, False),
                CONF_DEPENDENCY_HOME_OCCUPANCY: user_input.get(CONF_DEPENDENCY_HOME_OCCUPANCY, False),
            }
            self.hass.config_entries.async_update_entry(
                self._entry,
                data={
                    CONF_HOST: user_input[CONF_HOST].strip(),
                    CONF_PORT: user_input[CONF_PORT],
                    CONF_NAME: user_input[CONF_NAME].strip(),
                    CONF_MANUFACTURER: DEFAULT_MANUFACTURER,
                    CONF_MODEL: DEFAULT_MODEL,
                },
            )
            return self.async_create_entry(title="", data=new_options)

        return self.async_show_form(step_id="init", data_schema=self._options_schema())

    def _options_schema(self, user_input: dict[str, Any] | None = None) -> vol.Schema:
        current = self._entry.data
        current_options = self._entry.options
        source = user_input or current_options
        tariff_preset = source.get(CONF_TARIFF_PRESET, DEFAULT_TARIFF_PRESET)
        preset_start, preset_end = TARIFF_PRESETS.get(
            tariff_preset,
            (DEFAULT_OFF_PEAK_START, DEFAULT_OFF_PEAK_END),
        )
        off_peak_start = source.get(CONF_OFF_PEAK_START, preset_start)
        off_peak_end = source.get(CONF_OFF_PEAK_END, preset_end)
        return vol.Schema(
            {
                vol.Required(CONF_HOST, default=source.get(CONF_HOST, current.get(CONF_HOST, DEFAULT_HOST))): str,
                vol.Required(CONF_PORT, default=source.get(CONF_PORT, current.get(CONF_PORT, DEFAULT_PORT))): vol.Coerce(int),
                vol.Required(CONF_NAME, default=source.get(CONF_NAME, current.get(CONF_NAME, DEFAULT_NAME))): str,
                vol.Optional(
                    CONF_ADVANCED_ENERGY_SENSORS,
                    default=source.get(CONF_ADVANCED_ENERGY_SENSORS, False),
                ): bool,
                vol.Optional(
                    CONF_POLL_INTERVAL,
                    default=source.get(CONF_POLL_INTERVAL, POLL_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=MIN_POLL_INTERVAL, max=300)),
                vol.Optional(
                    CONF_TARIFF_PRESET,
                    default=tariff_preset,
                ): _TARIFF_PRESET_SELECTOR,
                vol.Optional(CONF_OFF_PEAK_START, default=off_peak_start): str,
                vol.Optional(CONF_OFF_PEAK_END, default=off_peak_end): str,
                vol.Optional(
                    CONF_DEPENDENCY_SOLCAST,
                    default=source.get(CONF_DEPENDENCY_SOLCAST, False),
                ): bool,
                vol.Optional(
                    CONF_DEPENDENCY_GRID_METER,
                    default=source.get(CONF_DEPENDENCY_GRID_METER, False),
                ): bool,
                vol.Optional(
                    CONF_DEPENDENCY_RECORDER,
                    default=source.get(CONF_DEPENDENCY_RECORDER, False),
                ): bool,
                vol.Optional(
                    CONF_DEPENDENCY_OCTOPUS_ENERGY,
                    default=source.get(CONF_DEPENDENCY_OCTOPUS_ENERGY, False),
                ): bool,
                vol.Optional(
                    CONF_DEPENDENCY_HOME_OCCUPANCY,
                    default=source.get(CONF_DEPENDENCY_HOME_OCCUPANCY, False),
                ): bool,
            }
        )
