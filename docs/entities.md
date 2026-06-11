# Entities

## Enabled By Default

Core entities focus on data and controls that come directly from the local battery connection:

- System Average Battery SOC
- Battery 1 SOC, Battery 2 SOC, and further generic battery slots when exposed by the master
- PV, AC charge, battery charge, battery discharge, grid, backup, and battery power
- Energy charged, discharged, and generated
- Operating Mode
- Charge Power Target and Discharge Power Target
- PV Surplus Charge Trigger
- Charge Limit and Discharge Limit
- Overnight Charge, Manual SOC, Off-Peak Tariff, Off-Peak Start, and Off-Peak End
- Solar Availability
- Overnight Status and Recommended Overnight SOC
- House Demand Energy and House Demand Daily
- Battery Status
- Connection Status

Individual Battery N SOC entities are created from local `Storage_list` entries
reported by the master. Restart Home Assistant or reload the integration after
adding, removing, or replacing a battery/inverter so the entity list is rebuilt.

`Operating Mode` is the last mode commanded through this local
integration. If the AEC Cloud app changes the system, the selector may not
reflect that external action. Use Battery Status, power sensors, Last Command
Result, and control-register snapshots when comparing local behaviour with app
or cloud-originated changes.

`PV Surplus Charge Trigger` is useful when the AFERIY system shares the same
electrical system with an unmanaged micro-inverter or another PV source that it
does not directly control. When your Smart Meter (usually Shelly) sees export rise above the chosen `0 W` to `50 W`
threshold, the batteries ramp up and start charging from the extra energy. A
small buffer helps avoid hunting or "chattering" around zero export.

## Diagnostic Entities

Diagnostic entities are intended for troubleshooting rather than dashboards:

- Consecutive Poll Failures
- Last Command Result
- Firmware Version
- SMART Overnight Accuracy
- SMART Morning Accuracy
- Selected raw or derived diagnostic readings

`SMART Overnight Accuracy` reviews the last completed SMART overnight cycle.
On solar-shortfall days, its signed percentage shows how much SOC was left
above or below the planned reserve floor when the next off-peak window started.
A positive value means the target was probably higher than needed; a negative
value means it was too low. Solar-surplus days are reported as `0` and marked
`not_scored_solar_surplus`, because solar refill makes end-of-day spare SOC a
poor measure of the overnight calculation. If the battery started the cheap-rate
window above the SMART target and never reached the target line, positive spare
SOC is also reported as `0` and marked `not_scored_started_above_target`; that is
carry-in energy, not an overcharge error. The same sensor also includes
`morning_need_accuracy` attributes showing how close the battery came to the
planned reserve floor before useful solar took over.

`SMART Morning Accuracy` exposes that morning bridge result as a graphable
signed percentage. A positive value means the battery stayed above the planned
reserve floor before useful solar took over; a negative value means it dipped
below the planned reserve. Carry-in nights that stayed above target and did not
need charging are reported as `0` with the same `not_scored_started_above_target`
result.

## Energy Estimate Sensors

- Battery Capacity, selected in 1.958 kWh module steps
- Estimated House Demand
- Estimated Charge Time
- Will Fill Today
- Recommended Overnight SOC

Battery Capacity is used by energy and overnight charge calculations only. It
does not control individual Battery N SOC entity creation.

### Recommended Overnight SOC

Recommended Overnight SOC is designed for homes with a cheap overnight tariff, for example Octopus Go. It suggests the battery target to charge to overnight, so the battery has enough energy for the morning without always charging to 100%.

The calculation can use:

- The configured usable battery capacity
- A weighted 14-day household energy-use profile from Home Assistant history
- Solar forecast data, ideally from Solcast
- The expected Pre-Sunrise Need before solar generation is useful
- The expected Post-Sunset Need after useful solar falls away and before off-peak starts
- Home occupancy from `zone.home`, so empty-house days can be treated separately from normal household demand
- Battery discharge and grid charge efficiency allowances
- A dynamic buffer and confidence adjustment that increase when forecast or demand history is less certain

Pre-Sunrise Need is the estimated energy needed after the cheap-rate window ends and before sustained forecast solar should cover house demand. Weak early-morning forecast solar is only given partial credit until sustained useful solar is expected. If the forecast never reaches sustained useful solar, part of the day forecast is still credited so low winter solar can reduce the target instead of forcing 100%. If Solar Availability is set to Solar Unavailable, the calculation treats forecast solar as 0 kWh and reports Batteries Only. The recommended target percentage is calculated from that kWh need, the configured battery capacity, the wider peak-rate window, recent use, expected solar, efficiency losses, a dynamic buffer, and a confidence adjustment. It is then kept within practical SOC limits.

Useful attributes include `target_breakdown_summary`, `recommendation_reason`, `pre_sunrise_need_kwh`, `post_sunset_need_kwh`, `whole_day_net_shortfall_kwh`, `pre_sunrise_net_need_kwh`, `pre_sunrise_credited_solar_kwh`, `no_useful_solar_forecast`, `solar_credit_mode`, `solar_unavailable_override`, `solar_override_status`, `solar_break_even_at`, `recorder_history_weighting`, `recorder_history_daily_averages`, `forecast_confidence`, `stale_data_guard_active`, `dynamic_buffer_soc`, `battery_loss_allowance_kwh`, `estimated_grid_charge_energy_to_target_kwh`, and `target_jump_guard`.

For best results, configure the battery capacity correctly, keep Home Assistant recorder history available, and provide Solcast forecast sensors. Without enough data the sensor may use conservative defaults or show that it cannot calculate a reliable target.

### Solcast Setup Note

When setting up Solcast, **do not set the AC inverter output to 800 W per unit**.

The batteries are capable of charging much faster than this and are rated to
**2.4 kW**.

If this is set too low, Solcast can under-estimate solar production. That can
make the overnight recommendation too high and charge the batteries more than
needed.

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
