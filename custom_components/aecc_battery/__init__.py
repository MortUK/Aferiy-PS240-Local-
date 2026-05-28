"""AECC Battery - local TCP integration for Home Assistant."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.util import slugify

from .const import (
    BRAND_PROFILES,
    CONF_EXTENDED_POWER,
    CONF_HOST,
    CONF_MANUFACTURER,
    CONF_MODEL,
    CONF_NAME,
    CONF_PORT,
    DEFAULT_BRAND_PROFILE,
    DEFAULT_TIMEOUT,
    DOMAIN,
)
from .coordinator import AeccBatteryCoordinator
from .diagnostics import _fetch_control_registers
from .tcp_client import AeccTcpClient
from .tcp_manager import TCPClientManager

_LOGGER = logging.getLogger(__name__)

# The switch platform only exposes local helper switches. Raw EMS control is
# deliberately not exposed; normal battery control should go through the clean
# Operating Mode select.
PLATFORMS = [Platform.SENSOR, Platform.NUMBER, Platform.SELECT, Platform.SWITCH]

SERVICE_SNAPSHOT_CONTROL_REGISTERS = "snapshot_control_registers"
SERVICE_SNAPSHOT_POWER_FLOW = "snapshot_power_flow"
SERVICE_RESTORE_ORIGINAL_SELF_CONSUMPTION = "restore_original_self_consumption"
SERVICE_RESTORE_SCHEDULE_3_SELF_CONSUMPTION = "restore_schedule_3_self_consumption"
MAX_SNAPSHOT_REGISTER_COUNT = 250

POWER_FLOW_ENTITY_IDS = (
    "sensor.aecc_battery_battery_soc",
    "sensor.aecc_battery_system_average_battery_soc",
    "sensor.aecc_battery_local_unit_battery_soc",
    "sensor.aecc_battery_pv_power",
    "sensor.aecc_battery_ac_charging_power",
    "sensor.aecc_battery_total_charge_power",
    "sensor.aecc_battery_total_battery_output_power",
    "sensor.aecc_battery_grid_meter_power",
    "sensor.aecc_battery_grid_export_power",
    "sensor.aecc_battery_total_grid_output_power",
    "sensor.aecc_battery_battery_status",
    "sensor.aecc_battery_control_enable_status",
    "sensor.aecc_battery_estimated_house_demand",
    "sensor.aecc_battery_runtime_at_current_house_demand",
    "sensor.aecc_battery_recommended_overnight_soc",
    "sensor.aecc_battery_grid_meter_agreement",
    "sensor.aecc_battery_charging_reason",
    "select.aecc_battery_operating_mode",
    "select.aecc_battery_battery_capacity_preset",
    "number.aecc_battery_charge_power_target",
    "number.aecc_battery_discharge_power_target",
    "number.aecc_battery_battery_capacity",
    "number.aecc_battery_charge_limit",
    "number.aecc_battery_discharge_limit",
    "sensor.shelly_grid_import_power",
    "sensor.shelly_grid_export_power",
    "sensor.shellypro3em_841fe8916604_phase_a_power",
    "sensor.aferiy_actual_system_mode",
    "sensor.aferiy_actual_energy_mode",
    "sensor.aferiy_actual_power_mode",
    "sensor.aferiy_actual_ai_mode",
    "sensor.aferiy_actual_bat_basic_discharge_power",
    "sensor.aferiy_zero_feed_in",
    "sensor.aferiy_generation_self_consumption",
    "switch.aecc_battery_solar_unavailable",
)

POWER_FLOW_ATTRIBUTE_KEYS = (
    "friendly_name",
    "status",
    "reason",
    "target_soc",
    "current_soc",
    "soc_needed",
    "battery_capacity_kwh",
    "energy_needed_kwh",
    "window_hours",
    "required_total_power_w",
    "can_reach_target",
    "estimated_hours_to_target",
    "pre_sunrise_label",
    "pre_sunrise_need_kwh",
    "pre_sunrise_net_need_kwh",
    "pre_sunrise_guard_need_kwh",
    "pre_sunrise_house_demand_kwh",
    "pre_sunrise_solar_kwh",
    "pre_sunrise_credited_solar_kwh",
    "pre_sunrise_solar_credit_factor",
    "no_useful_solar_forecast",
    "low_solar_day_credit_factor",
    "balanced_solar_day_credit_factor",
    "strong_solar_day_credit_factor",
    "solar_to_demand_ratio",
    "solar_credit_mode",
    "solar_unavailable_override",
    "solar_override_status",
    "pre_sunrise_solar_start_at",
    "useful_solar_start_at",
    "solar_break_even_at",
    "pre_sunrise_basis",
    "useful_solar_consecutive_periods_required",
    "useful_solar_margin_w",
    "useful_solar_demand_factor",
    "useful_solar_threshold_w",
    "dynamic_buffer_soc",
    "dynamic_buffer_reasons",
    "forecast_confidence",
    "forecast_confidence_adjustment_soc",
    "forecast_confidence_reasons",
    "solar_forecast_status",
    "solar_forecast_age_hours",
    "stale_data_guard_active",
    "stale_data_guard_min_soc",
    "stale_data_guard_reasons",
    "target_breakdown_summary",
    "target_breakdown",
    "why_target",
    "battery_discharge_efficiency",
    "grid_charge_efficiency",
    "battery_loss_allowance_kwh",
    "required_battery_energy_before_buffer_kwh",
    "confidence_adjustment_energy_kwh",
    "estimated_grid_charge_energy_to_target_kwh",
    "recommendation_reason",
    "house_empty_mode",
    "house_occupants",
    "target_jump_guard",
    "target_change_soc",
    "unit_of_measurement",
    "device_class",
    "last_refresh",
    "source",
    "energy_mode",
    "power_mode",
    "ai_mode",
    "bat_basic_discharge_power",
    "current_work_mode",
    "mode_str",
    "current_power",
    "anti_reflux_set",
    "ct_enable",
    "time_mode",
    "max_charge_power",
    "max_feed_power",
    "aecc_grid_meter_power_w",
    "shelly_grid_power_w",
    "difference_w",
    "absolute_difference_w",
    "shelly_source",
    "free_octopus_session",
    "off_peak",
    "overnight_smart_charge",
    "operating_mode",
    "charge_limit",
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    host: str = entry.data[CONF_HOST]
    port: int = entry.data[CONF_PORT]
    name: str = entry.data[CONF_NAME]
    manufacturer: str = entry.data.get(CONF_MANUFACTURER, "AECC")
    model: str = entry.data.get(CONF_MODEL, "")

    client = AeccTcpClient(host, port, timeout=DEFAULT_TIMEOUT)
    try:
        await client.async_connect()
    except (TimeoutError, OSError, ConnectionError) as exc:
        raise ConfigEntryNotReady(f"Cannot connect to {host}:{port} - {exc}") from exc

    extended_power = entry.options.get(CONF_EXTENDED_POWER, False)
    brand_profile = BRAND_PROFILES.get(manufacturer, DEFAULT_BRAND_PROFILE)
    coordinator = AeccBatteryCoordinator(
        hass,
        client,
        name,
        manufacturer=manufacturer,
        model=model,
        extended_power=extended_power,
        brand_profile=brand_profile,
    )
    await coordinator.async_config_entry_first_refresh()

    # Read initial register state and probe DeviceManagement
    await coordinator.async_read_initial_state()
    await coordinator.async_probe_device_management()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_register_services(hass)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    _LOGGER.info("AECC Battery '%s' (%s) set up at %s:%s", name, manufacturer, host, port)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator: AeccBatteryCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.client.async_disconnect()
        TCPClientManager.remove_instance(entry.data[CONF_HOST], entry.data[CONF_PORT])
    return unloaded


def _async_register_services(hass: HomeAssistant) -> None:
    """Register integration-level diagnostic services once."""
    if (
        hass.services.has_service(DOMAIN, SERVICE_SNAPSHOT_CONTROL_REGISTERS)
        and hass.services.has_service(DOMAIN, SERVICE_SNAPSHOT_POWER_FLOW)
        and hass.services.has_service(DOMAIN, SERVICE_RESTORE_ORIGINAL_SELF_CONSUMPTION)
        and hass.services.has_service(DOMAIN, SERVICE_RESTORE_SCHEDULE_3_SELF_CONSUMPTION)
    ):
        return

    async def async_snapshot_control_registers(call: ServiceCall) -> None:
        """Read control registers through the existing coordinator connection."""
        label = str(call.data.get("label") or "manual").strip() or "manual"
        requested_entry_id = call.data.get("entry_id")
        start_register = call.data.get("start_register")
        end_register = call.data.get("end_register")

        try:
            start_register = int(start_register) if start_register is not None else None
            end_register = int(end_register) if end_register is not None else None
        except (TypeError, ValueError):
            _LOGGER.warning(
                "AECC register snapshot ignored invalid range: start=%r end=%r",
                call.data.get("start_register"),
                call.data.get("end_register"),
            )
            return

        if (start_register is None) != (end_register is None):
            _LOGGER.warning(
                "AECC register snapshot needs both start_register and end_register, "
                "or neither"
            )
            return

        if start_register is not None and end_register is not None:
            if end_register < start_register:
                _LOGGER.warning(
                    "AECC register snapshot ignored reversed range: %s-%s",
                    start_register,
                    end_register,
                )
                return
            if (end_register - start_register + 1) > MAX_SNAPSHOT_REGISTER_COUNT:
                _LOGGER.warning(
                    "AECC register snapshot ignored wide range %s-%s; max is %d registers",
                    start_register,
                    end_register,
                    MAX_SNAPSHOT_REGISTER_COUNT,
                )
                return

        coordinators: list[tuple[str, AeccBatteryCoordinator]] = []
        for entry_id, value in (hass.data.get(DOMAIN) or {}).items():
            if requested_entry_id and entry_id != requested_entry_id:
                continue
            if isinstance(value, AeccBatteryCoordinator):
                coordinators.append((entry_id, value))

        if not coordinators:
            _LOGGER.warning(
                "AECC register snapshot requested, but no matching coordinator was found"
            )
            return

        for entry_id, coordinator in coordinators:
            if start_register is None or end_register is None:
                snapshot = await _fetch_control_registers(coordinator)
            else:
                snapshot = await _fetch_control_register_range(
                    coordinator,
                    start_register,
                    end_register,
                )
            normalised = snapshot.get("registers") or {}
            if not isinstance(normalised, dict):
                normalised = {}

            previous_key = f"_last_register_snapshot_{entry_id}"
            if start_register is not None and end_register is not None:
                previous_key = (
                    f"{previous_key}_{start_register}_{end_register}"
                )
            previous = hass.data[DOMAIN].get(previous_key)
            changed = _diff_registers(previous, normalised)
            hass.data[DOMAIN][previous_key] = dict(normalised)

            entity_id = (
                "sensor."
                f"{slugify(coordinator.device_name)}_control_register_snapshot"
            )
            fetched_at = datetime.now(UTC).isoformat()
            hass.states.async_set(
                entity_id,
                label,
                {
                    "friendly_name": f"{coordinator.device_name} Control Register Snapshot",
                    "icon": "mdi:memory",
                    "label": label,
                    "entry_id": entry_id,
                    "host": coordinator.client.host,
                    "fetched_at": fetched_at,
                    "range": snapshot.get("range"),
                    "error": snapshot.get("error"),
                    "key_registers": snapshot.get("key_registers"),
                    "changed_registers": changed,
                    "changed_count": len(changed),
                    "registers": normalised,
                },
            )

            _LOGGER.info(
                "AECC control register snapshot %r captured for %s; %d registers changed",
                label,
                coordinator.device_name,
                len(changed),
            )

    async def async_snapshot_power_flow(call: ServiceCall) -> None:
        """Capture the current HA power-flow entities into one readable sensor."""
        label = str(call.data.get("label") or "manual").strip() or "manual"
        captured_at = datetime.now(UTC).isoformat()
        entities: dict[str, dict[str, Any]] = {}
        missing_entities: list[str] = []

        for entity_id in POWER_FLOW_ENTITY_IDS:
            state = hass.states.get(entity_id)
            if state is None:
                missing_entities.append(entity_id)
                continue

            attrs = {
                key: state.attributes.get(key)
                for key in POWER_FLOW_ATTRIBUTE_KEYS
                if key in state.attributes
            }
            entities[entity_id] = {
                "state": state.state,
                "attributes": attrs,
            }

        hass.states.async_set(
            "sensor.aecc_battery_power_flow_snapshot",
            label,
            {
                "friendly_name": "AECC Battery Power Flow Snapshot",
                "icon": "mdi:chart-sankey",
                "label": label,
                "captured_at": captured_at,
                "entity_count": len(entities),
                "missing_count": len(missing_entities),
                "missing_entities": missing_entities,
                "entities": entities,
            },
        )

        _LOGGER.info(
            "AECC power flow snapshot %r captured; %d entities present, %d missing",
            label,
            len(entities),
            len(missing_entities),
        )

    async def async_restore_original_self_consumption(call: ServiceCall) -> None:
        """Run the original StekkerDeal self-consumption register write."""
        requested_entry_id = call.data.get("entry_id")

        coordinators: list[tuple[str, AeccBatteryCoordinator]] = []
        for entry_id, value in (hass.data.get(DOMAIN) or {}).items():
            if requested_entry_id and entry_id != requested_entry_id:
                continue
            if isinstance(value, AeccBatteryCoordinator):
                coordinators.append((entry_id, value))

        if not coordinators:
            _LOGGER.warning(
                "Original self-consumption requested, but no matching AECC coordinator was found"
            )
            return

        for entry_id, coordinator in coordinators:
            success = await coordinator.async_restore_original_self_consumption()
            state = "Applied" if success else "Failed"
            hass.states.async_set(
                "sensor."
                f"{slugify(coordinator.device_name)}_original_self_consumption_result",
                state,
                {
                    "friendly_name": f"{coordinator.device_name} Original Self-Consumption Result",
                    "icon": "mdi:battery-sync",
                    "entry_id": entry_id,
                    "applied_at": datetime.now(UTC).isoformat(),
                    "success": success,
                    "registers": {
                        "3000": "1",
                        "3021": "1",
                        "3022": "1",
                        "3030": "0",
                    },
                    "note": (
                        "Original StekkerDeal self-consumption write; leaves "
                        "schedule mode and manual slot unchanged."
                    ),
                },
            )

    async def async_restore_schedule_3_self_consumption(call: ServiceCall) -> None:
        """Run the upstream schedule-mode 3 self-consumption register write."""
        requested_entry_id = call.data.get("entry_id")

        coordinators: list[tuple[str, AeccBatteryCoordinator]] = []
        for entry_id, value in (hass.data.get(DOMAIN) or {}).items():
            if requested_entry_id and entry_id != requested_entry_id:
                continue
            if isinstance(value, AeccBatteryCoordinator):
                coordinators.append((entry_id, value))

        if not coordinators:
            _LOGGER.warning(
                "Schedule-3 self-consumption requested, but no matching AECC coordinator was found"
            )
            return

        for entry_id, coordinator in coordinators:
            success = await coordinator.async_restore_schedule_3_self_consumption()
            state = "Applied" if success else "Failed"
            hass.states.async_set(
                "sensor."
                f"{slugify(coordinator.device_name)}_schedule_3_self_consumption_result",
                state,
                {
                    "friendly_name": f"{coordinator.device_name} Schedule 3 Self-Consumption Result",
                    "icon": "mdi:battery-sync",
                    "entry_id": entry_id,
                    "applied_at": datetime.now(UTC).isoformat(),
                    "success": success,
                    "registers": {
                        "3000": "1",
                        "3003": "0,00:00,00:00,0,0,0,0,0,0,100,10",
                        "3020": "3",
                        "3021": "1",
                        "3022": "1",
                        "3030": "0",
                    },
                    "note": (
                        "Upstream-style diagnostic self-consumption write; "
                        "uses schedule mode 3 and clears the manual slot."
                    ),
                },
            )

    if not hass.services.has_service(DOMAIN, SERVICE_SNAPSHOT_CONTROL_REGISTERS):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SNAPSHOT_CONTROL_REGISTERS,
            async_snapshot_control_registers,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_SNAPSHOT_POWER_FLOW):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SNAPSHOT_POWER_FLOW,
            async_snapshot_power_flow,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_RESTORE_ORIGINAL_SELF_CONSUMPTION):
        hass.services.async_register(
            DOMAIN,
            SERVICE_RESTORE_ORIGINAL_SELF_CONSUMPTION,
            async_restore_original_self_consumption,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_RESTORE_SCHEDULE_3_SELF_CONSUMPTION):
        hass.services.async_register(
            DOMAIN,
            SERVICE_RESTORE_SCHEDULE_3_SELF_CONSUMPTION,
            async_restore_schedule_3_self_consumption,
        )

def _diff_registers(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Return changed register values compared with the previous snapshot."""
    if not previous:
        return {}

    changes: dict[str, dict[str, Any]] = {}
    keys = set(previous) | set(current)
    for key in sorted(keys, key=lambda item: int(item) if str(item).isdigit() else str(item)):
        before = previous.get(key)
        after = current.get(key)
        if before != after:
            changes[str(key)] = {
                "before": before,
                "after": after,
            }
    return changes


async def _fetch_control_register_range(
    coordinator: AeccBatteryCoordinator,
    start_register: int,
    end_register: int,
) -> dict[str, Any]:
    """Read a caller-selected control-register range."""
    section: dict[str, Any] = {
        "fetched_at": datetime.now(UTC).isoformat(),
        "registers": {},
        "key_registers": {},
        "range": [start_register, end_register],
        "error": None,
    }
    registers = list(range(start_register, end_register + 1))
    try:
        resp = await coordinator.client.get_control_parameters(registers)
    except Exception as exc:  # noqa: BLE001 - diagnostic service must not raise
        _LOGGER.debug("AECC register snapshot range read failed: %s", exc)
        section["error"] = f"range read failed: {exc}"
        return section

    if resp is None:
        section["error"] = "range read returned no response"
        return section

    params = resp.get("ControlInfo") or resp.get("GetParameters") or resp.get("Parameters") or {}
    if not isinstance(params, dict):
        section["error"] = f"unexpected response shape: {type(params).__name__}, keys={list(resp.keys())}"
        return section

    normalised: dict[str, Any] = {}
    for key, value in params.items():
        normalised[str(key)] = value

    section["registers"] = normalised
    return section
