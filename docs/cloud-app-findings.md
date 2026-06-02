# AEC Cloud App Findings

These notes document Proxyman captures from the AEC Cloud app. They are kept
for troubleshooting and future research. The Home Assistant integration remains
local-first and does not use these cloud endpoints for normal control.

## Scheduling

The AEC Cloud app writes schedules through:

```text
POST https://api.ai-ec.cloud:8443/aiSystem/setAISystemTimeWithEnergyMode
```

Observed schedule mode fields:

- `energyMode: 1` means app/cloud scheduled or intelligent linkage mode.
- `powerMode: 1` and `antiRefluxSet: 1` keep zero-feed enabled.
- `powerTimeSetVos` and `powerTimeSetVos2` both contain the schedule blocks.
- `mode: 1` inside a schedule block is the trough/off-peak charge schedule type.
- `mode: 3` was observed for a smart-linkage/peak reference schedule type.
- `temporaryPower` is the charge power in watts.
- `forcedPower` stayed `0` for trough charging.
- `chargingSOC` is the charge target.
- `dischargingSOC` is the discharge reserve.
- `timeSwitch: 1` enables a schedule block.
- Deleting schedules sends empty `powerTimeSetVos` and `powerTimeSetVos2` arrays.

The app does not allow a schedule block to cross midnight. A six-hour window
such as 23:30-05:30 is represented as two same-day blocks:

```json
[
  {
    "startTime": "23:30",
    "endTime": "23:59",
    "mode": 1,
    "temporaryPower": 800,
    "forcedPower": 0,
    "chargingSOC": 75,
    "dischargingSOC": 10,
    "timeSwitch": 1,
    "weather": "0",
    "energyConsume": "0",
    "electricPrice": "0"
  },
  {
    "startTime": "00:00",
    "endTime": "05:30",
    "mode": 1,
    "temporaryPower": 800,
    "forcedPower": 0,
    "chargingSOC": 75,
    "dischargingSOC": 10,
    "timeSwitch": 1,
    "weather": "0",
    "energyConsume": "0",
    "electricPrice": "0"
  }
]
```

## Local Schedule Register Test

The app/cloud state mirrored the schedule into local-looking slots such as:

```text
3003 = 1,23:30,23:59,0,800,1,0,0,0,75,10
3004 = 1,00:00,05:30,0,800,1,0,0,0,75,10
```

Local testing showed that writing this pattern directly to the unit starts
charging immediately rather than behaving as a true timer. For this reason the
integration should not expose local schedule writes as a normal feature.

Use Home Assistant as the scheduler instead:

- Trigger local Charge at the configured off-peak start time.
- Monitor System Average Battery SOC against the target.
- Idle or restore Self-Gen/Zero Export when the target is reached.
- Always restore Self-Gen/Zero Export at the configured off-peak end time.

This keeps normal operation local and avoids relying on the AEC Cloud app or an
internet connection.

## Self-Gen / Zero Feed

Cloud/app Self-Gen/Zero Feed was observed as:

```json
{
  "energyMode": 4,
  "powerMode": 1,
  "antiRefluxSet": 1
}
```

This is useful context for comparing cloud/app state with local register
snapshots, but the integration's normal local Self-Gen/Zero Export restore path
continues to use the locally tested safe register pattern.

## Plant Timezone

The app stores plant timezone through:

```text
POST https://api.ai-ec.cloud:8443/plant/updatePlant
```

The timezone field is a GMT offset in hours and supports half-hour increments:

- `timeZone=0` for GMT
- `timeZone=1` for GMT+1
- `timeZone=1.5` for GMT+1:30

The timezone can be read without updating plant settings from:

```text
POST https://api.ai-ec.cloud:8443/plant/getPlantPage
```

where the plant entry includes `timeZone`.

Do not update plant timezone casually from Home Assistant. The app request also
rewrites plant metadata and tariff periods, so a cloud helper should read this
value for diagnostics before attempting any write support.

## Per-Unit Cloud Data

The cloud endpoint:

```text
GET https://api.ai-ec.cloud:8443/energy/getHomeControlSn/{plantId}
```

returned one object per linked unit, including:

- `deviceSn`
- `deviceName`
- `masterSlaveType`
- `soc`
- `pvTotalPower`
- `batTotalPower`
- `gridPower`

This may be useful for a separate cloud diagnostic helper. It should not be
mixed into the local TCP integration's core entities unless the user explicitly
chooses a cloud-assisted setup.
