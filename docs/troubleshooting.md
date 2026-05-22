# Troubleshooting

## Device Cannot Connect

- Confirm the battery has a stable local IP address.
- Confirm TCP port `8080` is reachable from Home Assistant.
- Reserve the battery IP in your router so it does not change.
- Restart Home Assistant after installing or updating the integration.

## Entities Are Unavailable

Check the `Connection Status`, `Last Successful Update`, and `Consecutive Poll Failures` entities first. These show whether the local TCP poll is succeeding or whether Home Assistant is using the last good data.

## Commands Do Not Appear To Apply

Check `Last Command Result`. The integration records whether the battery acknowledged the command and whether the follow-up register read matched the requested value.

If a command is acknowledged but not verified, the battery may have ignored or normalised part of the register write. Download diagnostics and include them in a GitHub issue.

## Advanced Estimate Sensors Are Missing

The advanced estimate sensors are optional. Open the integration options and enable `Advanced energy estimate sensors`.
