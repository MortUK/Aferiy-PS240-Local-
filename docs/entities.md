# Entities

## Enabled By Default

Core entities focus on data and controls that come directly from the local battery connection:

- System Average Battery SOC
- Battery 1 SOC, Battery 2 SOC, and further generic battery slots when exposed by the master
- PV, AC charge, battery charge, battery discharge, grid, backup, and battery power
- Energy charged, discharged, and generated
- Local Operating Mode
- Charge Power Target and Discharge Power Target
- Charge Limit and Discharge Limit
- Battery Capacity
- Overnight Charge, Manual SOC, Off-Peak Tariff, Off-Peak Start, and Off-Peak End
- Solar Availability
- Overnight Status and Recommended Overnight SOC
- House Demand Energy and House Demand Daily
- Battery Status
- Connection Status

Battery Capacity is selected in 1.958 kWh module steps. Individual Battery N SOC
entities follow the selected installed module count, but Home Assistant may need
a full restart after the count changes to rebuild the entity list. Sensors are
only created when the master exposes matching local `Storage_list` entries.

`Local Operating Mode` is the last mode commanded through this local
integration. If the AEC Cloud app changes the system, the selector may not
reflect that external action. Use Battery Status, power sensors, Last Command
Result, and control-register snapshots when comparing local behaviour with app
or cloud-originated changes.

## Diagnostic Entities

Diagnostic entities are intended for troubleshooting rather than dashboards:

- Last Successful Update
- Consecutive Poll Failures
- Last Command Result
- Firmware Version
- Grid Meter Agreement
- Charging Reason
- Selected raw or derived diagnostic readings

## Energy Estimate Sensors

- Estimated House Demand
- Estimated Charge Time
- Will Fill Today
- Recommended Overnight SOC

### Recommended Overnight SOC

Recommended Overnight SOC is designed for homes with a cheap overnight tariff, for example Octopus Go. It suggests the battery target to charge to overnight, so the battery has enough energy for the morning without always charging to 100%.

The calculation can use:

- The configured usable battery capacity
- A weighted 14-day household energy-use profile from Home Assistant history
- Solar forecast data, ideally from Solcast
- The expected Pre-Sunrise Need before solar generation is useful
- Home occupancy from `zone.home`, so empty-house days can be treated separately from normal household demand
- Battery discharge and grid charge efficiency allowances
- A dynamic buffer and confidence adjustment that increase when forecast or demand history is less certain

Pre-Sunrise Need is the estimated energy needed after the cheap-rate window ends and before sustained forecast solar should cover house demand. Weak early-morning forecast solar is only given partial credit until sustained useful solar is expected. If the forecast never reaches sustained useful solar, part of the day forecast is still credited so low winter solar can reduce the target instead of forcing 100%. If Solar Availability is set to Solar Unavailable, the calculation treats forecast solar as 0 kWh and reports Batteries Only. The recommended target percentage is calculated from that kWh need, the configured battery capacity, the wider peak-rate window, recent use, expected solar, efficiency losses, a dynamic buffer, and a confidence adjustment. It is then kept within practical SOC limits.

Useful attributes include `target_breakdown_summary`, `recommendation_reason`, `pre_sunrise_need_kwh`, `pre_sunrise_net_need_kwh`, `pre_sunrise_credited_solar_kwh`, `no_useful_solar_forecast`, `solar_credit_mode`, `solar_unavailable_override`, `solar_override_status`, `solar_break_even_at`, `recorder_history_weighting`, `recorder_history_daily_averages`, `forecast_confidence`, `stale_data_guard_active`, `dynamic_buffer_soc`, `battery_loss_allowance_kwh`, `estimated_grid_charge_energy_to_target_kwh`, and `target_jump_guard`.

For best results, configure the battery capacity correctly, keep Home Assistant recorder history available, and provide Solcast forecast sensors. Without enough data the sensor may use conservative defaults or show that it cannot calculate a reliable target.

## Local Overnight Scheduling

AFERIY/AEC Cloud app schedule captures showed a cloud-side schedule object, but
direct local writes of the mirrored schedule-slot registers behaved as immediate
commands on the PS240. The integration therefore performs the timing in Home
Assistant and uses the proven local controls:

- Start local Charge one minute after the configured off-peak start time.
- Use System Average Battery SOC as the target feedback.
- Idle or hold when the target is reached.
- Restore Self-Gen/Zero Export five minutes before the off-peak end time.

This avoids relying on the cloud scheduler and keeps the local integration's
control path predictable.

## External Helpers

The options page includes confirmation checkboxes for common external helpers:

- Solcast PV Forecast, for timed solar forecast data
- Home occupancy through `zone.home`, for empty-house demand handling

These checkboxes are installer reminders. They do not install integrations or block setup.
