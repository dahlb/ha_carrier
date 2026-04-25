"""Centralized constants used throughout the Carrier integration."""

from homeassistant.const import CONF_PASSWORD, CONF_UNIQUE_ID, CONF_USERNAME, Platform

VERSION: str = "2.19.1"

# Configuration Constants
DOMAIN: str = "ha_carrier"

# Integration Setting Constants
CONFIG_FLOW_VERSION: int = 1
PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.CLIMATE,
    Platform.SELECT,
]

CONF_INFINITE_HOLDS: str = "infinite_holds"
DEFAULT_INFINITE_HOLDS: bool = True

FAN_AUTO = "auto"

TO_REDACT: set[str] = {CONF_USERNAME, CONF_PASSWORD, CONF_UNIQUE_ID}
TO_REDACT_MAPPED: set[str] = {
    "serial",
    "indoor_serial",
    "outdoor_serial",
}
TO_REDACT_RAW: set[str] = {
    "pin",
    "serial",
    "indoorSerial",
    "outdoorSerial",
    "routerMac",
    "href",
    "weatherPostalCode",
}
TO_REDACT_DEVICE: set[str] = {"identifiers"}
TO_REDACT_ENTITIES: set[str] = set()

HEAT_SOURCE_IDU_ONLY_LABEL = "gas heat only"
HEAT_SOURCE_ODU_ONLY_LABEL = "heat pump only"
HEAT_SOURCE_SYSTEM_LABEL = "system in control"
