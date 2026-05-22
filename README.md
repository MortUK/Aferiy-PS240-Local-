# AFERIY PS240 (Local)

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://www.hacs.xyz/)
[![HACS validation](https://github.com/MortUK/Aferiy-PS240-Local-/actions/workflows/hacs.yml/badge.svg)](https://github.com/MortUK/Aferiy-PS240-Local-/actions/workflows/hacs.yml)
[![Hassfest validation](https://github.com/MortUK/Aferiy-PS240-Local-/actions/workflows/hassfest.yml/badge.svg)](https://github.com/MortUK/Aferiy-PS240-Local-/actions/workflows/hassfest.yml)

Home Assistant custom integration for local TCP monitoring and control of an AFERIY PS240 battery.

This is a cleaned-up, AFERIY-focused fork of the AECC local TCP integration. It keeps the original `aecc_battery` integration domain so existing entities, dashboards, and automations do not need to be renamed.

## Features

- Local TCP connection to the battery, usually on port `8080`
- Battery state of charge, power, PV, charge, discharge, and diagnostic sensors
- Manual charge, discharge, idle, and self-consumption controls
- Charge and discharge SOC limits
- Optional extended power range up to 2400 W when enabled on the battery
- Physics-aware filtering for occasional invalid SOC/power readings
- Home Assistant diagnostics export support
- Custom AFERIY PS240 icon
- Connection health and last-command result sensors

## Install With HACS

[![Add to HACS via My Home Assistant](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=MortUK&repository=Aferiy-PS240-Local-&category=Integration)

1. Open HACS in Home Assistant.
2. Go to Integrations.
3. Open the three-dot menu and choose Custom repositories.
4. Add this repository URL as an Integration.
5. Search for `AFERIY PS240 (Local)` and install it.
6. Restart Home Assistant.

## Manual Install

Copy `custom_components/aecc_battery` into your Home Assistant `config/custom_components/` folder, then restart Home Assistant.

## Configuration

1. In Home Assistant, go to Settings > Devices & services.
2. Choose Add integration.
3. Search for `AFERIY PS240 (Local)`.
4. Enter the battery's local IP address, TCP port, display name, brand, and model.

Use a static IP address or DHCP reservation for the battery so Home Assistant can always find it.

## Options

Open the integration options to adjust:

- Extended power range up to 2400 W
- Polling interval
- Advanced energy estimate sensors

The advanced estimate sensors are disabled by default because they can depend on external Home Assistant entities such as grid meters, solar forecast data, or household demand history.

## Extended Power

The battery and AECC app may cap local control at 800 W unless the matching app-side output limit is increased. Only enable the extended power option if the battery installation and circuit are suitable for the higher load.

## Documentation

- [Entities](docs/entities.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Changelog](CHANGELOG.md)

## Attribution

This integration is based on the MIT-licensed `StekkerDeal/aecc-battery-local` project, which itself was forked from `slaapyhoofd/Lunergy-Local-TCP`.

## License

MIT. See `LICENSE`.
