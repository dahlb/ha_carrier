
[![Usage][usage-shield]][releases]
[![GitHub Release][releases-shield]][releases]
[![GitHub Activity][commits-shield]][commits]
[![License][license-shield]](LICENSE)
[![hacs][hacsbadge]][hacs]

![Project Maintenance][maintenance-shield]
[![BuyMeCoffee][buymecoffeebadge]][buymecoffee]

Cloud-based Home Assistant integration for **Carrier Infinity**, **Bryant Evolution**, and other thermostats that use the Carrier Infinity / Côr cloud account (the same account used by the Carrier mobile app).

This integration talks to Carrier's cloud and requires internet access plus your Carrier account credentials.
> Looking for a fully local option? Try [infinitude](https://github.com/MizterB/homeassistant-infinitude-beyond) instead. 

Initially tested on **SYSTXCCWIC01-B** (firmware 4.31). Other Infinity / Evolution thermostats that work in the Carrier mobile app are expected to work as well.

## Table of contents

- [Features](#features)
- [Installation](#installation)
- [Configuration](#configuration)
- [Troubleshooting](#troubleshooting)
- [Support](#support)

## Features

The integration adds a Home Assistant device per Carrier system, with entities for every zone

### Climate (per zone)

- Heat, Cool, Heat/Cool (Auto), Fan-only, and Off modes
- Current temperature and humidity
- Target temperature (single setpoint, or independent high/low setpoints in Heat/Cool mode)
- Target humidity (when a humidifier is installed)
- Fan speed: Auto, Low, Medium, High
- Preset activities defined on your thermostat: **Home**, **Away**, **Sleep**, **Wake**, **Manual**, plus a special **Resume** preset that returns the zone to its scheduled program
- Optional "infinite hold" behavior — when enabled *(default)*, manual changes hold indefinitely; when disabled, holds end at the next scheduled activity transition

### Sensors

#### Per zone:

- Temperature
- Humidity

#### Per system:

- Outdoor temperature
- Filter life remaining (%)
- Humidifier life remaining (%, *when installed*)
- UV lamp life remaining (%, *when installed*)
- Airflow (CFM)
- Static pressure
- Outdoor unit (ODU) status, with detailed diagnostics
- Indoor unit (IDU) status, with detailed diagnostics
- Variable-capacity ODU output (%, on variable-capacity heat pumps and AC units)
- Energy usage per source (heat pump, electric heat, gas, fan, cooling, reheat, loop pump): yesterday, last month, and year-to-date — wired up for Home Assistant's Energy dashboard
- Gas / propane usage year-to-date *(when applicable)*
- Last-update timestamps for full data refresh, websocket updates, and energy refresh

### Binary sensors

- System online / offline
- Humidifier currently running *(when installed)*
- Zone occupancy *(when occupancy sensing is enabled on the thermostat)*

### Controls

- **Heat Source** select: choose between gas heat only, heat pump only, or system-controlled (on dual-fuel and hybrid systems)

### Other

- Configuration entirely through the Home Assistant UI — no YAML required
- Near real-time updates via Carrier's websocket push channel, with periodic polling as a fallback
- Automatic re-authentication prompt if your Carrier password changes
- Supports multiple Carrier systems on the same account

## Installation

### HACS

*This integration is included in the default HACS store*

1. Make sure [HACS](https://hacs.xyz/) is installed in your Home Assistant

1. [![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=dahlb&repository=ha_carrier&category=integration) <br/>(**or** manually search for **Carrier Infinity Thermostat** in HACS)

1. Click **Download** in the bottom right

1. Restart Home Assistant

1. Continue with [Configuration](#configuration) below.

<details>
<summary><h3>Manual Installation</h3></summary>

1. Download the latest release from the [Releases page](https://github.com/dahlb/ha_carrier/releases).
2. Copy the `custom_components/ha_carrier` folder into your Home Assistant `config/custom_components/` directory. The final path should look like `config/custom_components/ha_carrier/__init__.py`.
3. Restart Home Assistant.
4. Continue with [Configuration](#configuration) below.

</details>

## Configuration

1. [![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=ha_carrier) <br/>**OR**
    * In Home Assistant, go to **Settings → Devices & Services**
    * Click **+ Add Integration** in the bottom-right
    * Search for **Carrier Infinity Thermostat** and select it

1. Enter the **username** and **password** for your Carrier cloud account _(the same login you use in the mobile app)_

1. Click **Submit**. Your thermostats and zones will appear as devices automatically

### Options

After setup, click **Configure** on the integration to change:

- **Infinite holds** (default: on) — when on, manual changes from Home Assistant hold until you choose **Resume**. When off, holds expire at the next scheduled activity transition on your thermostat.

### Re-authentication

If you change your Carrier password, Home Assistant will display a **Re-authenticate** prompt next to the integration. Click it and enter your new password — no need to remove and re-add the integration.

## Troubleshooting

If something isn't working as expected:

1. Open **Settings → Devices & Services → Carrier Infinity**.
2. Click **Enable debug logging**.
3. Restart Home Assistant so initialization is captured with debug logs.
4. Reproduce the issue.
5. Click **Disable debug logging** — Home Assistant will download a log file.
6. From the three-dot menu next to your Carrier device, click **Download diagnostics**.
7. [Open an issue](https://github.com/dahlb/ha_carrier/issues) and attach **both** the log file and the diagnostics file. Both files have personal information (serial numbers, account ID) automatically redacted.

### Common issues

- **"Invalid authentication"** — double-check your username and password in the Carrier mobile app. If the mobile app works but Home Assistant doesn't, open an issue with diagnostics.
- **Entities show as unavailable** — check the **Online** binary sensor for the system. If it reports offline, the thermostat has lost its connection to Carrier's cloud (often a router or internet issue at the thermostat's location).
- **Slow updates** — most state changes arrive within a few seconds via websocket; energy data is refreshed at most every 30 minutes.

## Support

- Bugs and feature requests: [GitHub Issues](https://github.com/dahlb/ha_carrier/issues)
- Like the integration? [Buy me a coffee][buymecoffee] ☕

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
[usage-shield]: https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fanalytics.home-assistant.io%2Fcustom_integrations.json&query=%24.ha_carrier.total&style=for-the-badge&logo=home-assistant&label=Integration%20Usage&color=41BDF5&cacheSeconds=15600
