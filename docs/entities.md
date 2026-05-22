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
