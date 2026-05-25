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
- Charge/discharge power targets from 800 W to 1200 W for cautious PS240 testing
- Physics-aware filtering for occasional invalid SOC/power readings
- Home Assistant diagnostics export support
- Custom AFERIY PS240 icon
- Connection health and last-command result sensors
- Grid meter agreement and charging reason diagnostics

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
4. Enter the battery's local IP address, TCP port, and display name.

Use a static IP address or DHCP reservation for the battery so Home Assistant can always find it.

### Multiple PS240 Units

If you have more than one PS240 in the same AFERIY/AEC Cloud system, add only the master unit to this integration.

The master controls the slave units. You do not need a separate local integration entry for each battery. In testing, one local connection to the master has been more reliable than trying to connect to every unit.

System-level readings are reported through the master. Battery SOC is the average state of charge across all connected units, matching the behaviour shown in the AEC Cloud app.

## Options

Open the integration options to adjust:

- Polling interval
- Advanced energy estimate sensors
- Off-peak tariff preset
- Off-peak start and end times
- External helper confirmations for advanced estimates

The advanced estimate sensors are disabled by default because they can depend on external Home Assistant entities such as grid meters, solar forecast data, or household demand history.

The off-peak window defaults to Octopus Intelligent Go, 23:30 to 05:30. Named presets are available for Snug Octopus, Octopus Go, Octopus Intelligent Go, E.ON Next Drive, British Gas Electric Driver, and British Gas Economy 7. If your tariff uses different cheap-rate hours, choose Custom and set the start and end times manually in 24-hour `HH:MM` format. These times are used by the overnight target and Pre-Sunrise Need calculations.

The external helper checkboxes are reminders for installers. They do not install or validate integrations. Smart estimates look for standard Solcast forecast files and sensors and use `zone.home` for home occupancy. Battery control and the overnight target use the configured tariff window and AECC grid reading; Shelly comparison remains diagnostic only.

### Smart Overnight Charging Target

The optional Recommended Overnight SOC sensor is aimed at users with cheap overnight electricity, such as Octopus Go or similar tariffs.

It estimates the battery percentage needed at the end of the configured overnight charging window. The estimate uses the configured battery capacity, recent household energy use, expected solar generation, and the morning period before useful solar production begins. If Solcast forecast sensors are available, they can be used as the solar forecast source.

Empty-house mode can reduce the recommended target because normal household demand is expected to be lower. For public installs this is based on Home Assistant's `zone.home` occupancy count, not a hardcoded person entity, so it works for households with multiple residents. Pre-Sunrise Need shows the estimated energy gap between the end of the cheap-rate window and the point where solar should start helping. The target percentage is calculated from the energy needed to cover that gap, the wider peak-rate window, battery efficiency losses, and a dynamic safety buffer.

The dynamic buffer grows when timed Solcast data is unavailable, there is limited house-demand history, the forecast is low, or the pre-sunrise period needs more cover. The sensor also exposes a plain-English recommendation reason and flags unusually large target changes for review without blocking the recommendation.

For this entity to work well, Home Assistant needs suitable energy history and, ideally, Solcast solar forecast sensors. Without those inputs, the estimate may fall back to conservative defaults or report that there is not enough data.

### Solar Clipping And Export

The PS240 can clip or hold back surplus PV when the battery is full or when the system is running in zero-feed behaviour.

Bypassing this PV clipping has been possible in testing, but the mode switching became unreliable. The current self-consumption switching is deliberately conservative because it reliably returns the battery to a safe local operating mode after charging or idling.

At the moment, bypassing clipping/export reliably is not supported by this integration. It may become possible in the future, but it may also need a firmware or app/API update from AFERIY before it can be made stable.

## Output Limit Notes

The PS240 has been observed to accept 800 W reliably over local TCP. The integration keeps register `3039` visible in diagnostics so higher output-limit behaviour can be investigated, but it does not write that register during normal commands.

## Documentation

- [Entities](docs/entities.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Changelog](CHANGELOG.md)

## Attribution

This integration is based on the MIT-licensed `StekkerDeal/aecc-battery-local` project, which itself was forked from `slaapyhoofd/Lunergy-Local-TCP`.

## License

MIT. See `LICENSE`.
