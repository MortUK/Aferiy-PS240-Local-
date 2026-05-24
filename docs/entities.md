# Entities

## Enabled By Default

Core entities focus on data and controls that come directly from the local battery connection:

- Battery SOC
- PV, AC charge, battery charge, battery discharge, grid, backup, and battery power
- Energy charged, discharged, and generated
- Operating Mode
- Charge Power Target and Discharge Power Target
- Charge Limit and Discharge Limit
- Battery Capacity and Battery Capacity Preset
- Battery Status
- Connection Status

## Diagnostic Entities

Diagnostic entities are intended for troubleshooting rather than dashboards:

- Last Successful Update
- Consecutive Poll Failures
- Last Command Result
- Firmware Version, when exposed by the battery
- Selected raw or derived diagnostic readings

## Advanced Energy Estimate Sensors

The optional advanced estimate sensors are disabled by default because they may depend on external Home Assistant entities such as grid meters, solar forecasts, or household demand history.

Enable them from the integration options if you want:

- Estimated House Demand
- Estimated Charge Time
- Will Fill Today
- Runtime at Current House Demand
- Recommended Overnight SOC

### Recommended Overnight SOC

Recommended Overnight SOC is designed for homes with a cheap overnight tariff, for example Octopus Go. It suggests the battery target to charge to overnight, so the battery has enough energy for the morning without always charging to 100%.

The calculation can use:

- The configured usable battery capacity
- Recent household energy use from Home Assistant history
- Solar forecast data, ideally from Solcast
- The expected morning period before solar generation is useful
- Holiday or away mode, where household demand may be lower than normal

Morning shortfall is the estimated energy needed after the cheap-rate window ends and before solar should start covering the house. The recommended target percentage is calculated from that shortfall, the battery capacity, recent use, and the solar forecast. It is then kept within practical SOC limits.

For best results, enable the advanced estimate sensors, configure the battery capacity correctly, keep Home Assistant recorder history available, and provide Solcast forecast sensors. Without enough data the sensor may use conservative defaults or show that it cannot calculate a reliable target.
