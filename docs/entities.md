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
- Firmware Version
- Grid Meter Agreement
- Charging Reason
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
- The expected Pre-Sunrise Need before solar generation is useful
- Home occupancy from `zone.home`, so empty-house days can be treated separately from normal household demand
- Battery discharge and grid charge efficiency allowances
- A dynamic buffer and confidence adjustment that increase when forecast or demand history is less certain

Pre-Sunrise Need is the estimated energy needed after the cheap-rate window ends and before sustained forecast solar should cover house demand. The recommended target percentage is calculated from that need, the wider peak-rate window, the battery capacity, recent use, expected solar, efficiency losses, a dynamic buffer, and a confidence adjustment. It is then kept within practical SOC limits.

Useful attributes include `target_breakdown_summary`, `recommendation_reason`, `pre_sunrise_need_kwh`, `solar_break_even_at`, `forecast_confidence`, `stale_data_guard_active`, `dynamic_buffer_soc`, `battery_loss_allowance_kwh`, `estimated_grid_charge_energy_to_target_kwh`, and `target_jump_guard`.

For best results, enable the advanced estimate sensors, configure the battery capacity correctly, keep Home Assistant recorder history available, and provide Solcast forecast sensors. Without enough data the sensor may use conservative defaults or show that it cannot calculate a reliable target.

## External Helpers

The options page includes confirmation checkboxes for common external helpers:

- Solcast PV Forecast, for timed solar forecast data
- Home occupancy through `zone.home`, for empty-house demand handling

These checkboxes are installer reminders. They do not install integrations or block setup.
