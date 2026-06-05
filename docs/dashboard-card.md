# Dashboard Card

The integration includes a reusable Lovelace card for the smart overnight plan.
It shows target SOC, battery capacity, day balance, Pre-Sunrise Need,
Post-Sunset Need, useful solar, confidence, and SMART History completeness.

## Add The Card Resource

After installing or updating the integration and restarting Home Assistant, add this dashboard resource:

```text
/aecc_battery_static/aferiy-overnight-plan-card.js
```

Set the resource type to:

```text
JavaScript module
```

Once the resource is loaded, open a dashboard, choose **Add card**, switch to **By card**, and search for:

```text
AFERIY Overnight Plan
```

## Manual YAML

You can also add it manually:

```yaml
type: custom:aferiy-overnight-plan-card
```

Optional entity overrides are available if your entities have unusual names:

```yaml
type: custom:aferiy-overnight-plan-card
recommended_entity: sensor.aferiy_ps240_local_recommended_overnight_soc
overnight_status_entity: sensor.aferiy_ps240_local_automatic_overnight_charging_status
solar_availability_entity: select.aferiy_ps240_local_solar_availability
smart_history_entity: sensor.aferiy_ps240_local_smart_history
```

The card reads the calculation from the Recommended Overnight SOC sensor. It does not make charging decisions itself.
