<img src="https://img.shields.io/badge/dynamic/json?color=41BDF5&logo=home-assistant&label=integration%20usage&suffix=%20installs&cacheSeconds=15600&url=https://analytics.home-assistant.io/custom_integrations.json&query=$.ha_carrier.total">

[![GitHub Release][releases-shield]][releases]
[![GitHub Activity][commits-shield]][commits]
[![License][license-shield]](LICENSE)
[![hacs][hacsbadge]][hacs]

![Project Maintenance][maintenance-shield]
[![BuyMeCoffee][buymecoffeebadge]][buymecoffee]

A custom integration for Carrier Infinity Thermostats.

This integration utilizes the Carrier Api, if you would prefer a fully local integration for carrier thermostats use [infinitude](https://github.com/MizterB/homeassistant-infinitude-beyond)

tested initially on SYSTXCCWIC01-B fw 4.31

## Feature Highlights ##
- thermostat
  - adjust fan speed, turn off/on
  - humidity
  - temperature
  - mode
  - presets
  - manual high/low temperatures
  - preset resume will resume the scheduled programming
- sensors
  - outdoor temperature
  - connectivity

## Installation ##
You can install this either manually copying files or using HACS. Configuration can be done on UI, you need to enter your username and password.

## Troubleshooting ##
If you receive an error, please go through these steps;
1. Enabled Debug Logging, at /config/integrations/integration/ha_carrier
2. Restart you home assistant to capture initialization with debug logging, then try to do what your having trouble with
3. Disable Debug Logging, at /config/integrations/integration/ha_carrier (which will download the logs)
4. Click the three dots menu for your device, at /config/integrations/integration/ha_carrier
5. Click Download Diagnostics
6. Attach both logs and diagnostics to your issue ticket.

***

[ha_carrier]: https://github.com/dahlb/ha_carrier
[commits-shield]: https://img.shields.io/github/commit-activity/y/dahlb/ha_carrier.svg?style=for-the-badge
[commits]: https://github.com/dahlb/ha_carrier/commits/main
[hacs]: https://github.com/hacs/integration
[hacsbadge]: https://img.shields.io/badge/HACS-Default-41BDF5.svg?style=for-the-badge
[forum]: https://community.home-assistant.io/
[license-shield]: https://img.shields.io/github/license/dahlb/ha_carrier.svg?style=for-the-badge
[maintenance-shield]: https://img.shields.io/badge/maintainer-Bren%20Dahl%20%40dahlb-blue.svg?style=for-the-badge
[releases-shield]: https://img.shields.io/github/release/dahlb/ha_carrier.svg?style=for-the-badge
[releases]: https://github.com/dahlb/ha_carrier/releases
[buymecoffee]: https://www.buymeacoffee.com/dahlb
[buymecoffeebadge]: https://img.shields.io/badge/buy%20me%20a%20coffee-donate-yellow.svg?style=for-the-badge
