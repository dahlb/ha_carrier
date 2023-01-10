[![hacs_badge](https://img.shields.io/badge/HACS-Default-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)

A custom integration for Carrier Infinity Thermostats. Only supports one zone setups, but can handle multiple thermostats.

Creates two thermostat entities, one reflects the api and the configurations, the read only reflects the current status of the thermostat which is sometimes outdated due to HVAC cool off periods but will update to match the config in due time.

## Feature Highlights ##
- thermostat
  - adjust fan speed, turn off/on
  - humidity
  - temperature
  - mode
  - presets
  - manual high/low temperatures
- sensors
  - outdoor temperature
  - connectivity

## Installation ##
You can install this either manually copying files or using HACS. Configuration can be done on UI, you need to enter your username and password.

## Troubleshooting ##
If you receive an error while trying to login, please go through these steps;
1. You can enable logging for this integration specifically and share your logs, so I can have a deep dive investigation. To enable logging, update your `configuration.yaml` like this, we can get more information in Configuration -> Logs page
```
logger:
  default: warning
  logs:
    custom_components.ha_carrier: debug
    carrier_api: debug
```

