# AFERIY PS240 (Local)

![AFERIY PS240 local battery control for Home Assistant](docs/images/aferiy-ps240-readme-hero.jpeg)

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://www.hacs.xyz/)
[![Version](https://img.shields.io/badge/version-v1.6.2-blue.svg)](CHANGELOG.md)
[![HACS validation](https://github.com/MortUK/Aferiy-PS240-Local-/actions/workflows/hacs.yml/badge.svg)](https://github.com/MortUK/Aferiy-PS240-Local-/actions/workflows/hacs.yml)
[![Hassfest validation](https://github.com/MortUK/Aferiy-PS240-Local-/actions/workflows/hassfest.yml/badge.svg)](https://github.com/MortUK/Aferiy-PS240-Local-/actions/workflows/hassfest.yml)

Home Assistant custom integration for local TCP monitoring and control of an AFERIY PS240.

This is a cleaned-up, AFERIY-focused fork of the AECC local TCP integration. It keeps the original `aecc_battery` integration domain so existing entities, dashboards, and automations do not need to be renamed.

## Features

- Local TCP connection to the battery, usually on port `8080`
- Battery state of charge, power, PV, charge, discharge, and diagnostic sensors
- Manual charge, discharge, idle, and self-consumption controls
- Local-first automatic overnight charging with smart or manual SOC targets
- Charge and discharge SOC limits
- Charge/discharge power targets from 800 W to 1200 W for cautious PS240 testing
- PV surplus charge trigger for systems with unmanaged microinverters
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

System-level readings are reported through the master. System Average Battery SOC is the main multi-unit SOC source and matches the behaviour shown in the AEC Cloud app.

The integration creates generic `Battery 1 SOC`, `Battery 2 SOC`, and similar entities from the local `Storage_list` entries reported by the master. This avoids tying dashboards to a particular serial number when a unit is replaced.

After adding, removing, or replacing a battery/inverter, restart Home Assistant or reload the integration so the individual battery entity list is rebuilt from the master. The Battery Capacity preset does not control battery identification.

## Options

Open the integration options to adjust:

- Polling interval
- Advanced energy estimate sensors
- Off-peak tariff preset
- Off-peak start and end times
- External helper confirmations for advanced estimates

The device Configuration section also provides Overnight Charge mode, Manual SOC, Off-Peak Tariff, Off-Peak Start/End, Solar Availability, Overnight Status, and Recommended Overnight SOC. Battery Capacity is available when Advanced Energy Estimate Sensors is enabled.

The advanced estimate sensors are disabled by default because they can depend on external Home Assistant entities such as grid meters, solar forecast data, or household demand history.

Battery Capacity is an advanced estimate input selected in 1.958 kWh module steps. It is used by the charge and overnight energy calculations only. It does not limit the Battery N SOC sensors reported by the master.

The off-peak window defaults to Octopus Intelligent Go, 23:30 to 05:30. Named presets are available for Snug Octopus, Octopus Go, Octopus Intelligent Go, E.ON Next Drive, British Gas Electric Driver, and British Gas Economy 7. If your tariff uses different cheap-rate hours, choose Custom and set the start and end times manually in 24-hour `HH:MM` format. These times are used by the overnight target and Pre-Sunrise Need calculations.

The external helper checkboxes are reminders for installers. They do not install or validate integrations. Smart estimates look for standard Solcast forecast files and sensors and use `zone.home` for home occupancy. Battery control and the overnight target use the configured tariff window and AECC grid reading; Shelly comparison remains diagnostic only.

### Smart Overnight Charging

Automatic Overnight Charging is disabled by default and runs entirely through the local TCP connection when enabled:

- **On** charges to Recommended Overnight SOC.
- **Manual** charges to the Manual SOC setting.
- **Off** leaves overnight charging disabled.
- Starts one minute after the configured off-peak start to avoid peak-rate overlap.
- Monitors System Average Battery SOC throughout the window and starts charging if SOC falls to or below the target.
- Holds the battery at the target after charging begins.
- Restores Self-Gen/Zero Export five minutes before off-peak ends because the PS240 can take several minutes to change power source.

Recommended Overnight SOC helps users with cheap overnight electricity charge only what they are likely to need before the next off-peak window, using tomorrow's solar forecast to reduce unnecessary grid charging.

- Uses the configured battery capacity and reserve/minimum SOC.
- Learns a weighted 14-day time-of-day house demand profile from Home Assistant history.
- Ignores mostly empty-house days using `zone.home` occupancy.
- Weights recent days more strongly and gives matching weekdays a small boost.
- Subtracts AFERIY AC charging from daily fallback demand.
- Uses Solcast, preferably timed forecast data, instead of previous clipped solar output.
- Estimates Pre-Sunrise Need from the off-peak end until sustained forecast solar should cover house demand.
- Gives weak early-morning forecast solar partial credit until sustained useful solar is expected.
- On low-solar days with no useful solar handover, still credits part of the forecast solar before converting the remaining kWh need into a battery-size-aware SOC target.
- Includes a Solar Availability dropdown; Solar Unavailable treats forecast solar as 0 kWh and shows Batteries Only status.
- Adds battery loss, grid charge loss, a dynamic buffer, and a forecast confidence adjustment.
- Applies a safer minimum SOC if Solcast or demand history is stale or missing.
- Exposes a plain-English target breakdown so dashboards can show why the target was chosen.

The overnight target also tracks the whole-day demand versus solar forecast and
the Post-Sunset Need, so the battery target can keep enough reserve after solar
falls away to reach the next cheap-rate window without unnecessary peak import.

Local schedule-slot registers mirrored from the AEC Cloud app were tested and
behaved as immediate commands on the PS240, so they are not exposed as normal
controls. The integration-owned scheduler instead performs the timing in Home
Assistant and sends the same proven local Charge, Idle, and Self-Gen commands.

### Solar Clipping And Export

The PS240 can clip or hold back surplus PV when the battery is full or when the system is running in zero-feed behaviour.

Bypassing this PV clipping has been possible in testing, but the mode switching became unreliable. The current self-consumption switching is deliberately conservative because it reliably returns the battery to a safe local operating mode after charging or idling.

At the moment, bypassing clipping/export reliably is not supported by this integration. It may become possible in the future, but it may also need a firmware or app/API update from AFERIY before it can be made stable.

### PV Surplus Charge Trigger

This setting is useful when the AFERIY system shares the same electrical system
with an unmanaged micro-inverter or another PV source that it does not directly
control.

As soon as the system sees that you are exporting more than your set value
between `0 W` and `50 W`, it tells your home batteries to ramp up and start
charging from the extra energy.

Having a small buffer gives the system a stable threshold to trigger clean,
sustained battery charging without constantly hunting or "chattering" around
zero.

## Output Limit Notes

The PS240 has been observed to accept 800 W reliably over local TCP. The integration keeps register `3039` visible in diagnostics so higher output-limit behaviour can be investigated, but it does not write that register during normal commands.

## Documentation

- [Entities](docs/entities.md)
- [AEC Cloud App Findings](docs/cloud-app-findings.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Changelog](CHANGELOG.md)

## Attribution

This integration is based on the MIT-licensed `StekkerDeal/aecc-battery-local` project, which itself was forked from `slaapyhoofd/Lunergy-Local-TCP`.

## License

MIT. See `LICENSE`.
