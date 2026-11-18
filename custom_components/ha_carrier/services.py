"""Register the Carrier integration's entity service actions."""

from __future__ import annotations

from carrier_api import ActivityTypes
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.components.climate.const import ATTR_TARGET_TEMP_HIGH, ATTR_TARGET_TEMP_LOW
from homeassistant.const import ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant, SupportsResponse, callback
from homeassistant.helpers import config_validation as cv, service
from homeassistant.helpers.typing import VolDictType
import probatio

from .const import DOMAIN

SERVICE_SET_ACTIVITY_SETPOINT = "set_activity_setpoint"
SERVICE_GET_SCHEDULE = "get_schedule"
SERVICE_SET_PROGRAM_DAY = "set_program_day"

SETPOINT_ACTIVITIES = [
    ActivityTypes.HOME.value,
    ActivityTypes.AWAY.value,
    ActivityTypes.SLEEP.value,
    ActivityTypes.WAKE.value,
    ActivityTypes.MANUAL.value,
]
PROGRAM_ACTIVITIES = [
    ActivityTypes.HOME.value,
    ActivityTypes.AWAY.value,
    ActivityTypes.SLEEP.value,
    ActivityTypes.WAKE.value,
]

SET_ACTIVITY_SETPOINT_SCHEMA: VolDictType = {
    probatio.Optional(ATTR_TEMPERATURE): probatio.Coerce(float),
    probatio.Optional(ATTR_TARGET_TEMP_LOW): probatio.Coerce(float),
    probatio.Optional(ATTR_TARGET_TEMP_HIGH): probatio.Coerce(float),
    probatio.Optional("activity"): probatio.In(SETPOINT_ACTIVITIES),
}
SET_PROGRAM_DAY_SCHEMA: VolDictType = {
    probatio.Required("day_id"): cv.string,
    probatio.Required("periods"): probatio.All(
        cv.ensure_list,
        [
            probatio.Schema(
                {
                    probatio.Required("id"): cv.string,
                    probatio.Required("activity"): probatio.In(PROGRAM_ACTIVITIES),
                    probatio.Optional("time"): cv.string,
                    probatio.Optional("enabled"): probatio.In(["on", "off"]),
                }
            )
        ],
    ),
    probatio.Optional("etag"): cv.string,
}


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's climate entity service actions.

    The actions are registered once when the integration is set up, rather
    than per config entry, so they exist (and validate their targets) even
    while no Carrier entry is loaded.

    Schedule-friendly services: Home Assistant's set_temperature always places
    a MANUAL hold; set_activity_setpoint instead edits an activity's set points
    in place, so an independent hold from the thermostat or app still wins and
    an HA outage leaves the thermostat on its own schedule at the last value
    written.

    Args:
        hass: Home Assistant instance.
    """
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_SET_ACTIVITY_SETPOINT,
        entity_domain=CLIMATE_DOMAIN,
        schema=SET_ACTIVITY_SETPOINT_SCHEMA,
        func="async_set_activity_setpoint",
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_GET_SCHEDULE,
        entity_domain=CLIMATE_DOMAIN,
        schema=None,
        func="async_get_schedule",
        supports_response=SupportsResponse.ONLY,
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_SET_PROGRAM_DAY,
        entity_domain=CLIMATE_DOMAIN,
        schema=SET_PROGRAM_DAY_SCHEMA,
        func="async_set_program_day",
    )
