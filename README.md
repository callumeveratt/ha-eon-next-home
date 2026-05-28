# E.ON Next Home — Home Assistant Integration

A custom Home Assistant integration for E.ON Next customers. Currently covers EV smart charging, with room to expand to solar and energy management in the future (powered by the Kraken platform).

## What it provides

### Sensors
| Entity | Description |
|--------|-------------|
| Smart Charge Status | Device registration status (Live / Suspended) |
| Vehicle | Your vehicle name (e.g. Audi Q4 e-tron) |
| Smart Charge Window Start | Start of the next planned charging window |
| Smart Charge Window End | End of the last planned charging window tonight |
| Smart Charge Energy | Total kWh planned for the next session |
| Weekday / Weekend Target Charge | Target state of charge (%) |
| Weekday / Weekend Ready By | Target ready-by time |
| Battery Capacity | Vehicle battery size in kWh |
| Charger Max Power | Charger power in kW |

### Binary Sensors
| Entity | Description |
|--------|-------------|
| Charger Connected | OCPP connection status of the charger |
| Smart Charging Scheduled | Whether dispatch windows are planned |
| Smart Charge Device Active | Whether the Kraken device is Live |

### Switches
| Entity | Description |
|--------|-------------|
| Smart Charging | Toggle Kraken smart scheduling on/off |
| Boost Charge | Start / cancel an immediate full-rate boost charge |

## Installation via HACS

1. In HACS → go to **Integrations**
2. Click the three-dot menu → **Custom repositories**
3. Add this repository URL and select category **Integration**
4. Click **Download**
5. Restart Home Assistant
6. Go to **Settings → Devices & Services → Add Integration**
7. Search for **E.ON Next EV Smart Charging**
8. Enter your E.ON Next email and password

## Requirements

- An E.ON Next account with an EV registered for smart charging
- A compatible smart charger (e.g. Ohme) linked to the E.ON Next app
- Home Assistant 2023.1 or newer

## Polling interval

Data is refreshed every **5 minutes**. Tokens are refreshed automatically.

## Notes

- This integration uses the private E.ON Next / Kraken API. It may break if E.ON Next changes their app.
- Smart charging is provided by Octopus Energy's Kraken platform — the same backend used by Intelligent Octopus Go.
