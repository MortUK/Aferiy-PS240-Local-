"""Config flow for AECC Battery (Local TCP) integration."""

from __future__ import annotations

import logging
import re
import contextlib
import random
import socket
import struct
import time
from typing import Any

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
import voluptuous as vol

from .const import (
    CONF_ADVANCED_ENERGY_SENSORS,
    CONF_DEPENDENCY_HOME_OCCUPANCY,
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
_DEPENDENCY_SOLCAST_FIELD = "1. Solcast installed"
_DEPENDENCY_HOME_OCCUPANCY_FIELD = "2. Home Occupancy enabled"
_AECC_ZEROCONF_SERVICE_PREFIX = "SXD-mDNS-IF-"
_AECC_ZEROCONF_TYPE = "131"
_AECC_ZEROCONF_SERIAL_PREFIX = "SXDI"
_AECC_ZEROCONF_SERVICE = "_http._tcp.local."
_AECC_ZEROCONF_HOST_PREFIX = "SXD-mDNS"
_AECC_DISCOVERY_SECONDS = 1.5
_AECC_DISCOVERY_RESOLVE_TIMEOUT_MS = 900
_AECC_DISCOVERY_CACHE_SECONDS = 60
_AECC_UDP_SERVICE_DISCOVERY_SECONDS = 1.5
_AECC_UDP_DETAIL_DISCOVERY_SECONDS = 1.0
_MDNS_ADDRESS = "224.0.0.251"
_MDNS_PORT = 5353
_DISCOVERED_DEVICE_FIELD = "Master Device"
_DISCOVERED_DEVICE_MANUAL = "__manual__"
_DISCOVERY_CACHE_KEY = "__aecc_discovery_cache__"
_TARIFF_PRESET_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=[
            selector.SelectOptionDict(value=value, label=TARIFF_PRESET_LABELS[value])
            for value in TARIFF_PRESETS
        ],
        mode=selector.SelectSelectorMode.DROPDOWN,
    )
)


class AeccBatteryConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the initial configuration step."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered_host: str | None = None
        self._discovered_port: int | None = None
        self._discovered_serial: str | None = None
        self._discovered_devices: dict[str, tuple[str, int]] = {}

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> AeccBatteryOptionsFlow:
        return AeccBatteryOptionsFlow(config_entry)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return await self._async_create_entry_from_input(user_input)

        self._discovered_devices = await _async_discover_aecc_devices(self.hass)
        return self.async_show_form(step_id="user", data_schema=self._user_schema())

    async def async_step_zeroconf(self, discovery_info: Any) -> FlowResult:
        """Handle a discovered AECC/Aferiy HTTP advertisement."""
        if not _is_aecc_zeroconf_device(discovery_info):
            return self.async_abort(reason="not_supported")
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")

        host = _zeroconf_property(discovery_info, "s_ip") or discovery_info.host
        if not host:
            return self.async_abort(reason="not_supported")

        port = _zeroconf_port(discovery_info)
        serial = _zeroconf_property(discovery_info, "s_sn")

        await self.async_set_unique_id(f"{host}:{port}")
        self._abort_if_unique_id_configured()

        self._discovered_host = host
        self._discovered_port = port
        self._discovered_serial = serial
        self.context["title_placeholders"] = {
            "name": f"AFERIY PS240 {serial or host}",
        }

        return self.async_show_form(
            step_id="zeroconf_confirm",
            data_schema=self._user_schema(),
            description_placeholders=self._description_placeholders(),
        )

    async def async_step_zeroconf_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Confirm a discovered AECC/Aferiy device should be configured."""
        if user_input is not None:
            return await self._async_create_entry_from_input(user_input)

        return self.async_show_form(
            step_id="zeroconf_confirm",
            data_schema=self._user_schema(),
            description_placeholders=self._description_placeholders(),
        )

    async def _async_create_entry_from_input(self, user_input: dict[str, Any]) -> FlowResult:
        selected_device = user_input.get(_DISCOVERED_DEVICE_FIELD, _DISCOVERED_DEVICE_MANUAL)
        if selected_device in self._discovered_devices:
            discovered_host, discovered_port = self._discovered_devices[selected_device]
            user_input[CONF_HOST] = discovered_host
            user_input[CONF_PORT] = discovered_port

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

    def _user_schema(self) -> vol.Schema:
        discovered_options = _discovered_device_options(self._discovered_devices)
        return vol.Schema(
            {
                vol.Optional(
                    _DISCOVERED_DEVICE_FIELD,
                    default=_DISCOVERED_DEVICE_MANUAL,
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=discovered_options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_HOST, default=self._discovered_host or DEFAULT_HOST): str,
                vol.Required(CONF_PORT, default=self._discovered_port or DEFAULT_PORT): vol.Coerce(int),
                vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
            }
        )

    def _description_placeholders(self) -> dict[str, str]:
        return {
            "default_host": DEFAULT_HOST,
            "discovered_host": self._discovered_host or "not discovered",
            "discovered_serial": self._discovered_serial or "unknown",
        }


class AeccBatteryOptionsFlow(config_entries.OptionsFlow):
    """Allow the user to update host/port/name without removing the entry."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry
        self._discovered_devices: dict[str, tuple[str, int]] = {}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            errors: dict[str, str] = {}
            selected_device = user_input.get(_DISCOVERED_DEVICE_FIELD, _DISCOVERED_DEVICE_MANUAL)
            if selected_device in self._discovered_devices:
                discovered_host, discovered_port = self._discovered_devices[selected_device]
                user_input[CONF_HOST] = discovered_host
                user_input[CONF_PORT] = discovered_port

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
                CONF_DEPENDENCY_SOLCAST: user_input.get(
                    _DEPENDENCY_SOLCAST_FIELD,
                    user_input.get(CONF_DEPENDENCY_SOLCAST, False),
                ),
                CONF_DEPENDENCY_HOME_OCCUPANCY: user_input.get(
                    _DEPENDENCY_HOME_OCCUPANCY_FIELD,
                    user_input.get(CONF_DEPENDENCY_HOME_OCCUPANCY, False),
                ),
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

        self._discovered_devices = await _async_discover_aecc_devices(self.hass)
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
                vol.Optional(
                    _DISCOVERED_DEVICE_FIELD,
                    default=source.get(_DISCOVERED_DEVICE_FIELD, _DISCOVERED_DEVICE_MANUAL),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=_discovered_device_options(self._discovered_devices),
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
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
                    _DEPENDENCY_SOLCAST_FIELD,
                    default=source.get(
                        _DEPENDENCY_SOLCAST_FIELD,
                        source.get(CONF_DEPENDENCY_SOLCAST, False),
                    ),
                ): bool,
                vol.Optional(
                    _DEPENDENCY_HOME_OCCUPANCY_FIELD,
                    default=source.get(
                        _DEPENDENCY_HOME_OCCUPANCY_FIELD,
                        source.get(CONF_DEPENDENCY_HOME_OCCUPANCY, False),
                    ),
                ): bool,
            }
        )


def _discovered_device_options(
    discovered_devices: dict[str, tuple[str, int]],
) -> list[selector.SelectOptionDict]:
    canonical_devices = _canonical_discovered_devices(discovered_devices)
    return [
        selector.SelectOptionDict(
            value=_DISCOVERED_DEVICE_MANUAL,
            label="Enter IP manually",
        ),
        *[
            selector.SelectOptionDict(value=value, label=value)
            for value in sorted(canonical_devices)
        ],
    ]


def _canonical_discovered_devices(
    discovered_devices: dict[str, tuple[str, int]],
) -> dict[str, tuple[str, int]]:
    canonical: dict[tuple[str, int], tuple[str, tuple[str, int]]] = {}
    for label, (host, port) in discovered_devices.items():
        normalized_host = str(host).strip()
        normalized_port = int(port)
        key = (normalized_host, normalized_port)
        existing = canonical.get(key)
        if existing is None or _is_better_discovery_label(label, existing[0]):
            clean_label = _clean_discovery_label(label, normalized_host, normalized_port)
            canonical[key] = (clean_label, key)
    return {label: value for label, value in canonical.values()}


def _is_better_discovery_label(candidate: str, existing: str) -> bool:
    candidate_prefix = candidate.split(" - ", 1)[0]
    existing_prefix = existing.split(" - ", 1)[0]
    if candidate_prefix.startswith(_AECC_ZEROCONF_SERIAL_PREFIX):
        return True
    if existing_prefix.startswith(_AECC_ZEROCONF_SERIAL_PREFIX):
        return False
    return len(candidate_prefix) < len(existing_prefix)


def _clean_discovery_label(label: str, host: str, port: int) -> str:
    prefix = label.split(" - ", 1)[0].strip()
    return f"{prefix} - {host}:{port}"


def _is_aecc_zeroconf_device(discovery_info: Any) -> bool:
    """Return true for the SXD HTTP adverts used by AECC/Aferiy devices."""
    service_name = discovery_info.name or ""
    if not service_name.lower().startswith(_AECC_ZEROCONF_SERVICE_PREFIX.lower()):
        return False

    device_type = _zeroconf_property(discovery_info, "s_type")
    serial = _zeroconf_property(discovery_info, "s_sn") or ""
    port = _zeroconf_property(discovery_info, "s_port")
    return (
        device_type == _AECC_ZEROCONF_TYPE
        and serial.startswith(_AECC_ZEROCONF_SERIAL_PREFIX)
        and port is not None
    )


def _zeroconf_port(discovery_info: Any) -> int:
    port = _zeroconf_property(discovery_info, "s_port")
    if port:
        try:
            return int(port)
        except ValueError:
            _LOGGER.debug("Invalid AECC zeroconf TCP port: %s", port)
    return DEFAULT_PORT


def _zeroconf_property(discovery_info: Any, key: str) -> str | None:
    value = discovery_info.properties.get(key)
    if value is None:
        raw_key = key.encode()
        value = discovery_info.properties.get(raw_key)
    if isinstance(value, bytes):
        return value.decode("utf-8", "ignore")
    if value is None:
        return None
    return str(value)


async def _async_discover_aecc_devices(hass: Any) -> dict[str, tuple[str, int]]:
    """Run a short mDNS browse for AECC/SXD devices visible to Home Assistant."""
    cached = _discovery_cache(hass)
    if cached is not None:
        return cached

    try:
        from homeassistant.components import zeroconf as ha_zeroconf
        from zeroconf import ServiceStateChange
        from zeroconf.asyncio import (
            AsyncServiceBrowser,
            AsyncServiceInfo,
            AsyncZeroconfServiceTypes,
        )
    except ImportError as exc:
        _LOGGER.debug("AECC rediscovery is unavailable: %s", exc)
        return {}

    aiozc = await ha_zeroconf.async_get_async_instance(hass)
    found: dict[str, tuple[str, int]] = {}
    tasks: set[Any] = set()
    udp_task = hass.async_add_executor_job(_discover_aecc_devices_via_mdns_udp)

    async def _resolve_service(service_type: str, name: str) -> None:
        if not name.startswith(_AECC_ZEROCONF_SERVICE_PREFIX):
            return

        info = AsyncServiceInfo(service_type, name)
        try:
            if not await info.async_request(
                aiozc.zeroconf,
                _AECC_DISCOVERY_RESOLVE_TIMEOUT_MS,
            ):
                return
        except Exception as exc:  # pragma: no cover - depends on network timing
            _LOGGER.debug("Could not resolve AECC zeroconf service %s: %s", name, exc)
            return

        properties = {
            _decode_zeroconf_value(key): _decode_zeroconf_value(value)
            for key, value in info.properties.items()
        }
        if (
            properties.get("s_type") != _AECC_ZEROCONF_TYPE
            or not properties.get("s_sn", "").startswith(_AECC_ZEROCONF_SERIAL_PREFIX)
            or not properties.get("s_port")
        ):
            return

        host = properties.get("s_ip")
        addresses = _zeroconf_info_addresses(info)
        if not host and addresses:
            host = addresses[0]
        if not host:
            return

        try:
            port = int(properties.get("s_port", DEFAULT_PORT))
        except ValueError:
            port = DEFAULT_PORT

        serial = properties.get("s_sn") or name.removesuffix(f".{_AECC_ZEROCONF_SERVICE}")
        _add_discovered_device(found, serial, host, port)

    def _on_service_state_change(
        zeroconf: Any,
        service_type: str,
        name: str,
        state_change: Any,
    ) -> None:
        if state_change is ServiceStateChange.Removed:
            return
        task = hass.async_create_task(_resolve_service(service_type, name))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    browser = AsyncServiceBrowser(
        aiozc.zeroconf,
        _AECC_ZEROCONF_SERVICE,
        handlers=[_on_service_state_change],
    )
    try:
        await _async_prime_aecc_discovery(aiozc, _resolve_service, AsyncZeroconfServiceTypes)
        await _async_sleep(hass, _AECC_DISCOVERY_SECONDS)
        if tasks:
            await _async_wait_for_tasks(tasks)
    finally:
        with contextlib.suppress(Exception):
            cancel_result = browser.async_cancel()
            if _is_awaitable(cancel_result):
                await cancel_result

    udp_found = await udp_task
    found.update(udp_found)
    _set_discovery_cache(hass, found)
    return found


def _discovery_cache(hass: Any) -> dict[str, tuple[str, int]] | None:
    cache = (hass.data.get(DOMAIN) or {}).get(_DISCOVERY_CACHE_KEY)
    if not isinstance(cache, dict):
        return None

    cached_at = cache.get("cached_at")
    devices = cache.get("devices")
    if not isinstance(cached_at, (int, float)) or not isinstance(devices, dict):
        return None
    if time.monotonic() - cached_at > _AECC_DISCOVERY_CACHE_SECONDS:
        return None

    return dict(devices)


def _set_discovery_cache(hass: Any, devices: dict[str, tuple[str, int]]) -> None:
    hass.data.setdefault(DOMAIN, {})[_DISCOVERY_CACHE_KEY] = {
        "cached_at": time.monotonic(),
        "devices": dict(devices),
    }


async def _async_prime_aecc_discovery(
    aiozc: Any,
    resolve_service: Any,
    service_types_class: Any,
) -> None:
    """Ask mDNS for current service names so quiet devices are included."""
    with contextlib.suppress(Exception):
        services = await service_types_class.async_find(aiozc=aiozc, timeout=1.5)
        if _AECC_ZEROCONF_SERVICE not in services:
            return

    # Browse callbacks normally resolve devices, but this explicit query helps
    # when a device is present in the cache yet does not re-announce during the
    # short options-flow window.
    with contextlib.suppress(Exception):
        service_names = aiozc.zeroconf.cache.async_entries_with_name(
            _AECC_ZEROCONF_SERVICE,
        )
        for entry in service_names:
            name = getattr(entry, "alias", None) or getattr(entry, "name", None)
            if name and name.startswith(_AECC_ZEROCONF_SERVICE_PREFIX):
                await resolve_service(_AECC_ZEROCONF_SERVICE, name)


async def _async_sleep(hass: Any, delay: float) -> None:
    """Sleep without importing asyncio at module import time."""
    import asyncio

    await asyncio.sleep(delay)


async def _async_wait_for_tasks(tasks: set[Any]) -> None:
    """Wait for outstanding zeroconf resolution tasks."""
    import asyncio

    await asyncio.gather(*list(tasks), return_exceptions=True)


def _decode_zeroconf_value(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "ignore")
    return str(value)


def _zeroconf_info_addresses(info: Any) -> list[str]:
    if hasattr(info, "parsed_scoped_addresses"):
        return list(info.parsed_scoped_addresses())
    if hasattr(info, "parsed_addresses"):
        return list(info.parsed_addresses())
    return []


def _is_awaitable(value: Any) -> bool:
    import inspect

    return inspect.isawaitable(value)


def _discover_aecc_devices_via_mdns_udp() -> dict[str, tuple[str, int]]:
    """Discover SXD AECC devices with a direct mDNS query fallback."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(0.35)
        sock.sendto(_mdns_query(_AECC_ZEROCONF_SERVICE, 12), (_MDNS_ADDRESS, _MDNS_PORT))
    except OSError as exc:
        _LOGGER.debug("AECC mDNS UDP rediscovery is unavailable: %s", exc)
        return {}

    records: list[tuple[str, str, int, str | None]] = []
    end = time.monotonic() + _AECC_UDP_SERVICE_DISCOVERY_SECONDS
    while time.monotonic() < end:
        try:
            data, addr = sock.recvfrom(9000)
        except socket.timeout:
            continue
        except OSError:
            break
        records.extend((addr[0], name, record_type, value) for name, record_type, value in _parse_mdns_records(data))

    service_names = {
        value
        for _, name, record_type, value in records
        if record_type == 12
        and name.rstrip(".") == _AECC_ZEROCONF_SERVICE.rstrip(".")
        and value
        and value.startswith(_AECC_ZEROCONF_SERVICE_PREFIX)
    }
    for service_name in service_names:
        with contextlib.suppress(OSError):
            sock.sendto(_mdns_query(service_name, 16), (_MDNS_ADDRESS, _MDNS_PORT))
            sock.sendto(_mdns_query(service_name, 33), (_MDNS_ADDRESS, _MDNS_PORT))
    for host_name in _candidate_sxd_hostnames():
        with contextlib.suppress(OSError):
            sock.sendto(_mdns_query(host_name, 1), (_MDNS_ADDRESS, _MDNS_PORT))

    end = time.monotonic() + _AECC_UDP_DETAIL_DISCOVERY_SECONDS
    while time.monotonic() < end:
        try:
            data, addr = sock.recvfrom(9000)
        except socket.timeout:
            continue
        except OSError:
            break
        records.extend((addr[0], name, record_type, value) for name, record_type, value in _parse_mdns_records(data))

    with contextlib.suppress(OSError):
        sock.close()

    return _aecc_devices_from_mdns_records(records)


def _candidate_sxd_hostnames() -> list[str]:
    return [
        f"{_AECC_ZEROCONF_HOST_PREFIX}.local",
        *[
            f"{_AECC_ZEROCONF_HOST_PREFIX}-{index}.local"
            for index in range(1, 16)
        ],
    ]


def _aecc_devices_from_mdns_records(
    records: list[tuple[str, str, int, str | None]],
) -> dict[str, tuple[str, int]]:
    found: dict[str, tuple[str, int]] = {}
    for source_ip, name, record_type, value in records:
        if (
            record_type == 1
            and name.startswith(_AECC_ZEROCONF_HOST_PREFIX)
            and value
        ):
            _add_discovered_device(found, name, value, DEFAULT_PORT)
            continue

        if record_type != 16 or not name.startswith(_AECC_ZEROCONF_SERVICE_PREFIX) or not value:
            continue

        properties = _parse_mdns_txt(value)
        serial = properties.get("s_sn", "")
        if (
            properties.get("s_type") != _AECC_ZEROCONF_TYPE
            or not serial.startswith(_AECC_ZEROCONF_SERIAL_PREFIX)
        ):
            continue

        host = properties.get("s_ip") or source_ip
        try:
            port = int(properties.get("s_port", DEFAULT_PORT))
        except ValueError:
            port = DEFAULT_PORT

        _add_discovered_device(found, serial, host, port)
    return found


def _add_discovered_device(
    found: dict[str, tuple[str, int]],
    label_prefix: str,
    host: str,
    port: int,
) -> None:
    existing_label = _find_discovered_device_label(found, host, port)
    label = f"{label_prefix} - {host}:{port}"
    if existing_label is None:
        found[label] = (host, port)
        return

    if label_prefix.startswith(_AECC_ZEROCONF_SERIAL_PREFIX):
        found.pop(existing_label)
        found[label] = (host, port)


def _find_discovered_device_label(
    found: dict[str, tuple[str, int]],
    host: str,
    port: int,
) -> str | None:
    for label, existing in found.items():
        if existing == (host, port):
            return label
    return None


def _parse_mdns_txt(value: str) -> dict[str, str]:
    properties: dict[str, str] = {}
    for item in value.split("; "):
        if "=" not in item:
            continue
        key, item_value = item.split("=", 1)
        properties[key] = item_value
    return properties


def _mdns_query(name: str, query_type: int) -> bytes:
    return (
        struct.pack("!HHHHHH", random.randrange(65536), 0, 1, 0, 0, 0)
        + _encode_mdns_name(name)
        + struct.pack("!HH", query_type, 1)
    )


def _encode_mdns_name(name: str) -> bytes:
    encoded = b""
    for part in name.strip(".").split("."):
        encoded += bytes([len(part)]) + part.encode()
    return encoded + b"\0"


def _read_mdns_name(data: bytes, offset: int) -> tuple[str, int]:
    labels: list[str] = []
    jumped = False
    start = offset
    seen: set[int] = set()
    while offset < len(data):
        length = data[offset]
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(data):
                return ".".join(labels), offset + 1
            pointer = ((length & 0x3F) << 8) | data[offset + 1]
            if pointer in seen:
                return ".".join(labels), offset + 2
            seen.add(pointer)
            if not jumped:
                start = offset + 2
            offset = pointer
            jumped = True
            continue
        offset += 1
        if length == 0:
            break
        labels.append(data[offset : offset + length].decode("utf-8", "replace"))
        offset += length
    return ".".join(labels), start if jumped else offset


def _parse_mdns_records(data: bytes) -> list[tuple[str, int, str | None]]:
    if len(data) < 12:
        return []
    questions, answers, authorities, additionals = struct.unpack("!HHHH", data[4:12])
    offset = 12
    for _ in range(questions):
        _, offset = _read_mdns_name(data, offset)
        offset += 4

    records: list[tuple[str, int, str | None]] = []
    for count in (answers, authorities, additionals):
        for _ in range(count):
            name, offset = _read_mdns_name(data, offset)
            if offset + 10 > len(data):
                return records
            record_type, _, _, data_length = struct.unpack("!HHIH", data[offset : offset + 10])
            offset += 10
            record_start = offset
            record_data = data[offset : offset + data_length]
            offset += data_length
            value: str | None = None
            if record_type == 12:
                value, _ = _read_mdns_name(data, record_start)
            elif record_type == 16:
                value = _decode_mdns_txt(record_data)
            elif record_type == 1 and data_length == 4:
                value = socket.inet_ntoa(record_data)
            records.append((name, record_type, value))
    return records


def _decode_mdns_txt(data: bytes) -> str:
    parts: list[str] = []
    offset = 0
    while offset < len(data):
        length = data[offset]
        offset += 1
        parts.append(data[offset : offset + length].decode("utf-8", "replace"))
        offset += length
    return "; ".join(parts)
