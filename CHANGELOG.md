# Changelog

## Unreleased

No unreleased changes.

## 1.7.1

- Added a user-friendly Wi-Fi Signal sensor showing percentage, quality and raw dBm.
- Removed the unused SMART Overnight Accuracy and SMART Morning Accuracy sensors.
- Corrected SMART History completeness so known away or filtered days count as observed history without affecting the demand average.
- Added SMART Solar Forecast and SMART House Demand configuration sliders so users can gently tune the overnight calculation when local behaviour differs from the forecast/history.
- Added expected end-of-peak reserve and SMART tuning visibility to the bundled Overnight Plan card.
- Added rolling SMART overnight re-checks that can raise, but not lower, the locked target during off-peak when the live recommendation materially increases.
- Replaced the simplistic expected reserve calculation with a timed battery simulation that accounts for the battery reaching 100%, clipped solar surplus, and later evening demand.
- Documented the low-solar behaviour: use cheap-rate energy when useful, while leaving room for forecast solar.

## 1.7.0

- Added experimental Feed mode to Operating Mode using the locally discovered base grid-connected feed/discharge register `3026`.
- Added a passive Base Feed Power slider from `0-800 W`; moving the slider only stores the target until Operating Mode is set to Feed.
- Made Self-Gen/Zero Export clear the base feed value so Feed mode does not linger after returning to the normal safe mode.
- Documented that Feed mode is experimental and actual output may differ from the target, especially on systems with smart meter/CT feedback.

## 1.6.4

- Persisted SMART runtime settings across Home Assistant restarts and power cuts, including Overnight Charge mode, Battery Capacity, tariff preset, custom off-peak times, Manual SOC, and Solar Availability.
- Improved local TCP recovery by forcing a reconnect after empty or invalid poll responses while preserving last known good sensor data during short outages.
- Kept SMART configuration controls visible during temporary TCP failures so local settings do not appear to reset when the battery poll is unavailable.
- Improved SMART overnight planning for close-call and low-solar days by allowing cheap-rate top-up while leaving forecast solar headroom.
- Added extra transparency on the bundled Overnight Plan card for cheap-rate top-up decisions and forecast solar headroom.
- Reduced low-value diagnostic noise and recorder-heavy attributes to keep long-term history leaner.

## 1.6.3

- Added SMART Overnight Accuracy as a diagnostic sensor to review the last completed SMART overnight cycle against the configured minimum SOC plus planned buffer.
- Improved AC Charging Power cleanup so stale AC charge readings are suppressed when PV already explains the observed battery charging.
- Changed SMART history projections to exclude the current in-progress day from the 14-day demand profile.
- Improved the bundled Overnight Plan card so Overnight Status and Tariff update from the local integration entities instead of stale dashboard helpers.
- Rewrote the SMART Overnight Charging README section in plainer language and added an important Solcast setup note.
- Documented the PV Surplus Charge Trigger for unmanaged microinverters or other PV sources sharing the same smart meter.

## 1.6.2

- Added a bundled AFERIY Overnight Plan Lovelace card with card-picker registration and README setup instructions.
- Added PV Surplus Charge Trigger as a local `0-50 W` control for systems with unmanaged microinverters or other PV sources on the same electrical system.
- Improved Recommended Overnight SOC so whole-day demand versus solar shortfall is considered before using the morning-bridge-only target path.
- Removed the extra overnight discharge-efficiency uplift so SMART targets follow measured house demand, solar shortfall, reserve, and buffer more closely.
- Added Post-Sunset Need and whole-day net shortfall attributes to explain the battery reserve needed between useful solar ending and the next off-peak window.
- Added SMART History visibility for the 14-day household usage picture used by overnight recommendations.
- Locked the automatic overnight SMART target at the start of the active off-peak window so recalculations after midnight do not move the charging target mid-window.
- Rounded Battery Capacity preset labels to two decimal places and renamed Local Operating Mode to Operating Mode.
- Clarified the Overnight Plan card battery need and timing-check wording.
- Updated the README hero graphic and documented the local PV surplus trigger behaviour.

## 1.6.1

- Returned individual Battery N SOC identification to the master-reported local `Storage_list`.
- Battery Capacity no longer limits or removes individual Battery N SOC entities; it is only an advanced energy-estimate input.
- On startup or integration reload, stale Battery N SOC entities are removed when the master reports a changed battery list.
- Automatic Overnight Charging now reports an unconfirmed charge command as retrying rather than as a final failure.

## 1.6.0

- Added integration-owned Automatic Overnight Charging with Off, On, and Manual modes.
- Added local SMART configuration controls for tariff preset, custom off-peak start/end times, and manual overnight SOC.
- Starts automatic charging one minute after off-peak begins, monitors System Average Battery SOC throughout the window, and restores Self-Gen/Zero Export five minutes before off-peak ends.
- Added Overnight Status and integration-owned House Demand Energy and House Demand Daily sensors.
- Made Recommended Overnight SOC and the Battery Capacity preset available as standard configuration features.
- Replaced the Solar Unavailable switch with a Solar Availability dropdown offering Solar Available and Solar Unavailable.
- Removed redundant Battery SOC, Local Unit Battery SOC, Runtime Left, manual Battery Capacity, and old Solar Unavailable entities.
- Made individual Battery N SOC entities follow the installed module count selected through Battery Capacity and remove stale higher-numbered slots after restart.
- Documented that changing the installed battery module count may require a full Home Assistant restart to rebuild individual battery SOC entities.

## 1.5.6

- Made AC Charging Power prefer the system-total cloud/local summary field before falling back to the local unit field.
- Stabilised individual per-battery SOC sensors by holding the last valid reading through short bad-value bursts.
- Changed Energy Charged to use Total Charge Power and prevent negative source power from reducing the total-increasing counter.
- Renamed Operating Mode to Local Operating Mode and clarified that cloud/app-originated changes may not update the selector.
- Added diagnostic labelling for control time slot 2 and documented AEC Cloud app schedule/export findings from Proxyman captures.
- Documented the recommended local-first overnight charging approach using Home Assistant as the scheduler.

## 1.5.5

- Fixed Hassfest validation for zeroconf discovery by using the required lowercase manifest matcher.
- Made zeroconf service-name handling case-insensitive.

## 1.5.4

- Added Home Assistant zeroconf/mDNS discovery for local AECC/SXD devices.
- Added a Master Device selector during setup and reconfiguration while keeping manual IP entry available.
- De-duplicated discovered devices by IP/port and prefers serial-number labels when available.
- Clearly warns multi-unit users to add only the master/coordinator, not executor units.
- Optimised setup discovery with shorter scans, parallel mDNS fallback, and a short cache so settings load faster.

## 1.5.3

- Added dynamic per-battery SOC sensors based on the units reported by local TCP, avoiding unused fixed Array sensors.
- Kept System Average Battery SOC as the main multi-unit SOC source for estimates and overnight automation logic.
- Made House Demand available without enabling advanced energy estimate sensors.
- Hid the manual Battery Capacity number by default because the capacity preset is the preferred control.
- Removed unreliable per-battery PV/output sensors after local testing showed they do not provide useful per-unit values.
- Updated the Home Assistant device manufacturer display to Richard Owen to avoid implying manufacturer support.

## 1.5.2

- Changed the PS240 Self-Gen restore path to the tested schedule-3 pattern with AI smart discharge enabled and AI smart charge disabled.
- Improved smart overnight charging so strong solar days credit more of the early pre-useful-solar ramp, reducing overcharging on clear summer mornings.
- Added regression tests to protect the PS240 Self-Gen register pattern and diagnostics redaction.
- Updated the GitHub lint workflow to run the regression tests.

## 1.5.1

- Extended the smart overnight demand-history lookback from 7 to 14 days.
- Weighted recent house-demand history more strongly while keeping older valid days as smoothing data.
- Added a small same-weekday boost so matching weekday patterns have more influence.
- Exposed demand-history weighting details on the Recommended Overnight SOC sensor attributes.
- Resolved the Solar Unavailable switch entity ID from the entity registry instead of relying on a hardcoded dashboard-style name.

## 1.5.0

- Added Recommended Overnight SOC target breakdown attributes for dashboards.
- Added Solcast/data-history freshness checks and a safer stale-data minimum target.
- Added forecast confidence adjustments for uncertain or very low solar forecasts.
- Changed Pre-Sunrise Need to run until sustained forecast solar should cover house demand when timed Solcast data is available.
- Added a conservative pre-useful-solar guard so weak early forecast solar only gets partial credit before it affects the SOC target.
- Added a low-solar-day credit so forecast solar can reduce the battery-size-aware SOC target even if it never fully covers house load.
- Added a Solar Unavailable integration switch that treats forecast solar as 0 kWh and reports Batteries Only.
- Made the options helper checklist labels render directly in the form if Home Assistant translation caching falls back to raw keys.

## 1.4.22

- Simplified the installer helper checklist again to Solcast installed and Home Occupancy enabled.
- Removed Shelly Smart Meter from the checklist because AECC grid readings are used for control and estimates; Shelly comparison remains diagnostic only.

## 1.4.21

- Simplified the installer helper checklist to Solcast installed, Shelly Smart Meter installed, and Home Occupancy enabled.
- Removed Octopus Energy and Recorder from the options checklist to avoid implying they are integration requirements.
- Clarified that helper checkboxes are reminders, while the integration currently looks for known Solcast, Shelly, and `zone.home` entities rather than auto-discovering every installation.

## 1.4.20

- Clarified that Octopus Energy sensors are optional helpers for automations and diagnostics, not required by the core local battery integration.
- Renamed installer-facing dependency wording to external helper wording.

## 1.4.19

- Added off-peak tariff presets for Snug Octopus, Octopus Go, Octopus Intelligent Go, E.ON Next Drive, British Gas Electric Driver, and British Gas Economy 7.
- Changed the default off-peak preset to Octopus Intelligent Go, 23:30-05:30.
- Made named tariff presets store their listed hours automatically; custom mode keeps manual start and end times.
- Added installer-facing external dependency confirmation checkboxes for Solcast, whole-home grid metering, Recorder history, Octopus Energy sensors, and home occupancy.

## 1.4.18

- Improved the Recommended Overnight SOC calculation with a dynamic buffer that grows when demand history, timed solar forecast data, or pre-sunrise coverage is weaker.
- Split occupied-house and empty-house demand floors, using `zone.home` occupancy while keeping the previous away-mode attributes as compatibility aliases.
- Added specific Pre-Sunrise Need attributes for the period after cheap-rate charging ends and before useful solar starts.
- Added battery discharge and grid charge efficiency allowances so the target better reflects real usable energy.
- Added a plain-English recommendation reason attribute for dashboard cards.
- Added a warning-only target jump guard for unusually large target changes.

## 1.4.17

- Replaced the personal away-mode check with household occupancy from `zone.home`, so demand estimates work for any Home Assistant household rather than one named person.
- Skipped historic demand profile days where the home was empty for most of the forecast window.
- Removed the personal default brand profile alias and kept the public default on the generic AFERIY profile.
- Added Grid Meter Agreement and Charging Reason diagnostic sensors.
- Included the new diagnostic fields in power-flow snapshots.
- Always create the Firmware Version diagnostic sensor, even when the battery has not exposed a value yet.

## 1.4.16

- Added configurable off-peak tariff start and end times, defaulting to Octopus Go 23:30-05:30.
- Updated the recommended overnight SOC and morning shortfall calculations to use the configured off-peak window.
- Added a diagnostic schedule-3 self-consumption restore service for export/clipping testing without changing the stable Self mode.
- Cleaned up duplicate or unused dashboard entities while keeping internal raw values available for calculations.
- Renamed the main PS240 controls and estimates for a cleaner Home Assistant UI.

## 1.4.15

- Synced local PS240 control fixes from the live Home Assistant install.
- Updated estimated house demand to use AECC grid meter flow, including signed grid history.
- Restored extended-power compatibility for existing local entries.

## 1.4.14

- Added backwards-compatible power option constants for mixed local upgrades.
- Kept the PS240 self-consumption restore path aligned with the live local fix.

## 1.4.13

- Simplified the setup and reconfigure forms for the AFERIY PS240.
- Removed the extended 2400 W power option from the UI and normal control writes.
- Read register 3039 for diagnostics so PS240 output-limit behaviour can be investigated safely.

## 1.4.12

- Removed unsupported `domains` key from `hacs.json` for HACS validation.
- Re-ran validation after adding repository topics required by HACS.

## 1.4.11

- Sorted manifest keys for Hassfest validation.

## 1.4.10

- Declared the optional recorder integration relationship for Hassfest validation.

## 1.4.9

- Added connection health, last successful update, consecutive poll failure, and last command result sensors.
- Added options for polling interval and advanced energy estimate sensors.
- Disabled advanced estimate sensors by default for a cleaner public install.
- Added HACS, hassfest, and Python syntax GitHub Actions.
- Added issue templates, entity documentation, and troubleshooting notes.
- Added HACS/Home Assistant badges to the README.

## 1.4.8

- Initial AFERIY PS240 (Local) public package.
