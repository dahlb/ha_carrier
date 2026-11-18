
[![Usage][usage-shield]][releases]
[![GitHub Release][releases-shield]][releases]
[![GitHub Activity][commits-shield]][commits]
[![License][license-shield]](LICENSE)
[![hacs][hacsbadge]][hacs]

![Project Maintenance][maintenance-shield]
[![BuyMeCoffee][buymecoffeebadge]][buymecoffee]

Cloud-based Home Assistant integration for **Carrier Infinity**, **Bryant Evolution**, and other thermostats that use the Carrier Infinity / Côr cloud account (the same account used by the Carrier mobile app).

This integration talks to Carrier's cloud and requires internet access plus your Carrier account credentials.
> Looking for a fully local option? Try [Infinitude](https://github.com/MizterB/homeassistant-infinitude-beyond) or [InfinitESP](https://nebulous.github.io/infinitesp) instead.

Initially tested on **SYSTXCCWIC01-B** (firmware 4.31). Other Infinity / Evolution thermostats that work in the Carrier mobile app are expected to work as well.

## Table of contents

- [Features](#features)
- [Use cases](#use-cases)
- [Installation](#installation)
- [Configuration](#configuration)
- [Automation examples](#automation-examples)
- [Known limitations](#known-limitations)
- [Troubleshooting](#troubleshooting)
- [Removal](#removal)
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
- Services that work with the thermostat's own schedule instead of holds:
  - `ha_carrier.set_activity_setpoint` edits an activity's set points without a hold (the current activity, or the one named by `activity`)
  - `ha_carrier.get_schedule` returns the zone's program and each activity's set points
  - `ha_carrier.set_program_day` replaces one day of the zone's program

### Sensors

#### Per zone:

- Temperature
- Humidity
- Damper position (%, *diagnostic; disabled by default*)
- Conditioning state as Carrier reports it (*diagnostic; disabled by default*)

#### Per system:

- Outdoor temperature
- Filter life remaining (%)
- Humidifier life remaining (%, *when installed*)
- UV lamp life remaining (%, *when installed*)
- Airflow (CFM)
- Static pressure
- Outdoor unit (ODU) status
- Indoor unit (IDU) status
- Blower speed (rpm, *diagnostic*)
- Outdoor unit readings: fan speed, coil / discharge / suction temperature, suction pressure, suction superheat and line voltage (*diagnostic; disabled by default*, on systems with an outdoor unit — Carrier only reports these in diagnostic websocket messages, so they can stay unavailable on some systems)
- Variable-capacity ODU output (%, on variable-capacity heat pumps and AC units)
- Modulating furnace (IDU) output (%, on modulating furnaces)
- Energy usage per source (heat pump, electric heat, gas, fan, cooling, reheat, loop pump): yesterday, last month, and year-to-date — wired up for Home Assistant's Energy dashboard
- Gas / propane usage year-to-date *(when applicable)*
- Last-update timestamps for full data refresh, websocket updates, and energy refresh *(diagnostic; disabled by default — enable them from the entity settings when troubleshooting)*

### Binary sensors

- System online / offline
- Humidifier currently running *(when installed)*
- Zone occupancy *(when occupancy sensing is enabled on the thermostat)*
- Indoor unit (furnace / fan coil) lockout (*diagnostic; disabled by default*)
- Outdoor unit lockout (*diagnostic; disabled by default*, on systems with an outdoor unit)

> **Upgrading from 2.36.0 or earlier:** the climate entity's `conditioning`, `blower_rpm` and `damper_position` attributes, and the ODU / IDU status sensors' unit-detail attributes, are now the sensors above. Update any templates or automations that read those attributes.

### Controls

- **Heat Source** select: choose between gas heat only, heat pump only, or system-controlled (on dual-fuel and hybrid systems)

### Other

- Configuration entirely through the Home Assistant UI — no YAML required
- Near real-time updates via Carrier's websocket push channel, with periodic polling as a fallback
- Automatic re-authentication prompt if your Carrier password changes
- Supports multiple Carrier systems on the same account
- Thermostats added to your Carrier account later show up automatically (on the next full refresh) without reloading the integration; devices for thermostats removed from the account are removed from Home Assistant too

## Use cases

- **Presence-aware comfort** — switch zones to **Away** when everyone leaves and **Resume** the schedule when someone comes home, instead of heating or cooling an empty house.
- **Schedule tweaks without holds** — nudge an activity's set points (for example, a cooler **Sleep** in summer) with `ha_carrier.set_activity_setpoint`, or rewrite a day's program with `ha_carrier.set_program_day`. The thermostat keeps running its own schedule, so it still works if Home Assistant is down.
- **Maintenance reminders** — get notified when the filter, humidifier pad or UV lamp is nearly used up.
- **Energy tracking** — add the energy sensors to Home Assistant's Energy dashboard to see what heating, cooling and the blower cost you.
- **Alerts** — be told when the thermostat drops offline, or when a room gets too hot or cold while you are away.
- **Dual-fuel control** — switch the **Heat Source** between the heat pump and gas furnace based on outdoor temperature or energy prices.

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
- **Full refresh interval** (default: 120 minutes, range 5–120) — how often the integration fetches the complete system state from Carrier, on top of the live websocket updates. Most systems get live updates and should leave this at 120. Carrier has said its A-model controls (such as the `SYSTXCCITC01-A`) are obsolete and that features like live updates may no longer work on them, so their status only changes on a full refresh. If yours behaves that way, set this to a low value such as 5. Lower values mean more requests to Carrier's API.

### Re-authentication

If you change your Carrier password, Home Assistant will display a **Re-authenticate** prompt next to the integration. Click it and enter your new password — no need to remove and re-add the integration.

## Automation examples

Entity IDs below are examples — yours are based on your system and zone names.

**Set every zone to Away when everyone leaves, and resume the schedule on return:**

```yaml
automation:
  - alias: "Carrier: away when house is empty"
    triggers:
      - trigger: state
        entity_id: zone.home
        to: "0"
        for: "00:10:00"
    actions:
      - action: climate.set_preset_mode
        target:
          entity_id: climate.home_living_room
        data:
          preset_mode: away

  - alias: "Carrier: resume schedule when someone is home"
    triggers:
      - trigger: numeric_state
        entity_id: zone.home
        above: 0
    actions:
      - action: climate.set_preset_mode
        target:
          entity_id: climate.home_living_room
        data:
          preset_mode: resume
```

**Make the Sleep activity cooler in summer, without creating a hold:**

```yaml
automation:
  - alias: "Carrier: summer sleep set points"
    triggers:
      - trigger: time
        at: "06:00:00"
    conditions:
      - condition: template
        value_template: "{{ now().month in [6, 7, 8] }}"
    actions:
      - action: ha_carrier.set_activity_setpoint
        target:
          entity_id: climate.home_living_room
        data:
          activity: sleep
          target_temp_low: 66
          target_temp_high: 72
```

**Remind me to change the filter:**

```yaml
automation:
  - alias: "Carrier: filter reminder"
    triggers:
      - trigger: numeric_state
        entity_id: sensor.home_filter_remaining
        below: 10
    actions:
      - action: notify.notify
        data:
          message: "The HVAC filter has {{ states('sensor.home_filter_remaining') }}% life left."
```

**Alert when the thermostat has been offline for 30 minutes:**

```yaml
automation:
  - alias: "Carrier: thermostat offline"
    triggers:
      - trigger: state
        entity_id: binary_sensor.home_online
        to: "off"
        for: "00:30:00"
    actions:
      - action: notify.notify
        data:
          message: "The Carrier thermostat has lost its connection to Carrier's cloud."
```

**Print a zone's schedule** (Developer tools → Actions, with *Return response*):

```yaml
action: ha_carrier.get_schedule
target:
  entity_id: climate.home_living_room
```

## Known limitations

- **Cloud only.** Everything goes through Carrier's cloud: no internet or a Carrier outage means no data or control. For local control, see [Infinitude](https://github.com/MizterB/homeassistant-infinitude-beyond).
- **A-model controls may not get live updates.** Carrier says its A-model controls (such as the `SYSTXCCITC01-A`) are obsolete, and they may not receive live updates. Their state then only changes on a full refresh; lower the **Full refresh interval** option (see [Options](#options)).
- **Rate limits.** Carrier's API rate limits requests. The full refresh interval can't go below 5 minutes, commands are sent one at a time, and very frequent automations may still be refused for a while.
- **Changes can briefly bounce back.** After a change, Carrier's cloud sometimes replays the old value. For five minutes after a write the integration keeps showing what you set; a full refresh is postponed until then.
- **Humidity targets.** The target humidity is the heating humidifier target only, from 0% (off) to 45% in steps of 5%; other values are rounded to the nearest step.
- **Energy data is not real time.** Energy sensors report yesterday, last month and year to date, refreshed every 30 minutes (or at the full refresh interval if lower). There is no live power reading.
- **Smart Thermostats (entry-level) have fewer features.** They get a climate entity with Off, Cool, Heat and Heat/Cool and their set points, but no presets, fan modes, holds, schedule services or sensors.
- **New and removed thermostats** are picked up on the next full refresh, not instantly.
- **Outdoor temperature** is reported by Carrier in Fahrenheit regardless of the thermostat's unit setting; Home Assistant converts it for display.

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
- **Slow updates** — most state changes arrive within a few seconds via websocket; energy data is refreshed every 30 minutes, or more often if you lower the full refresh interval. If changes only show up after you reload the integration, your system is probably not receiving live updates; lower the **Full refresh interval** option (see [Options](#options)).

## Removal

This integration follows the standard Home Assistant removal process; no extra steps are needed.

1. Go to **Settings → Devices & Services → Carrier Infinity**.
2. Click the three-dot menu next to your Carrier account and choose **Delete**. This removes its devices and entities and stops all requests to Carrier's cloud.
3. *(Optional)* If you installed through HACS and will not use the integration again, open **HACS**, find **Carrier Infinity Thermostat**, choose **Remove**, then restart Home Assistant. For a manual install, delete `config/custom_components/ha_carrier` and restart.

Removing the integration does not change anything on your thermostat or in your Carrier account. Holds or set points made from Home Assistant stay in place until you change them in the Carrier app or on the thermostat.

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
