# Changelog

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
