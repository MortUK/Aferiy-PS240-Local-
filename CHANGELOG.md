# Changelog

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
