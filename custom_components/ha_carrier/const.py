from enum import Enum

# Configuration Constants
DOMAIN: str = "ha_carrier"

# Integration Setting Constants
CONFIG_FLOW_VERSION: int = 1
PLATFORMS = ["binary_sensor", "sensor", "climate"]

# Home Assistant Data Storage Constants
DATA_SYSTEMS: str = "systems"

CONF_SCAN_INTERVAL: str = "scan_interval"
DEFAULT_SCAN_INTERVAL: int = 10
