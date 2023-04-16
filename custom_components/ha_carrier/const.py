"""Create constants for reference."""

from homeassistant.const import Platform
from homeassistant.const import CONF_UNIQUE_ID, CONF_USERNAME, CONF_PASSWORD

# Configuration Constants
DOMAIN: str = "ha_carrier"

# Integration Setting Constants
CONFIG_FLOW_VERSION: int = 1
PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.CLIMATE]

# Home Assistant Data Storage Constants
DATA_SYSTEMS: str = "systems"

CONF_SCAN_INTERVAL: str = "scan_interval"
CONF_INFINITE_HOLDS: str = "infinite_holds"
DEFAULT_SCAN_INTERVAL: int = 10
DEFAULT_INFINITE_HOLDS: bool = True

FAN_AUTO = "auto"

TO_REDACT = {CONF_USERNAME, CONF_PASSWORD, CONF_UNIQUE_ID}
TO_REDACT_MAPPED = {
    "serial",
    "indoor_serial",
    "outdoor_serial",
}
TO_REDACT_RAW = {
    "pin",
    "serial",
    "indoorSerial",
    "outdoorSerial",
    "routerMac",
    "href",
    "weatherPostalCode",
}
TO_REDACT_DEVICE = {"identifiers"}
TO_REDACT_ENTITIES = {}
