from homeassistant.const import Platform

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
