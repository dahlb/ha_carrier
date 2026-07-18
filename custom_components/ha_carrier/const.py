"""Centralized constants used throughout the Carrier integration."""

from homeassistant.const import CONF_PASSWORD, CONF_UNIQUE_ID, CONF_USERNAME, Platform

VERSION: str = "2.24.2"

# Configuration Constants
DOMAIN: str = "ha_carrier"

# Integration Setting Constants
CONFIG_FLOW_VERSION: int = 3
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

DEFAULT_UPDATE_INTERVAL_MINUTES: int = 30
# Force a full refresh at least this often even while the websocket stays
# connected, so websocket-maintained status that silently goes stale (a dropped
# or rebroadcast partial delta) is reconciled against an authoritative full pull.
# Kept a multiple of the poll interval so intermediate polls stay lightweight
# (energy-only) and only every Nth poll pays for a full pull.
FULL_RECONCILE_INTERVAL_MINUTES: int = DEFAULT_UPDATE_INTERVAL_MINUTES * 4
# After an HA write, Carrier's cloud can replay the pre-write snapshot over the
# websocket (a fast bounce within seconds, or a slow revert ~2 min later). For
# this window after a write the coordinator re-asserts any control field (mode /
# set point) the cloud reverts back to the intended value, so HA never shows the
# stale value. The stale replay is forward-timestamped, so it cannot be told
# apart by content — the window is the only reliable discriminator.
POST_WRITE_INTERCEPT_WINDOW_MINUTES: int = 5
UNAUTHORIZED_RETRY_THRESHOLD: int = 3
MAX_WRITE_ATTEMPTS: int = 2

WEBSOCKET_RETRY_INITIAL_DELAY_SECONDS: int = 1
WEBSOCKET_RETRY_MAX_DELAY_SECONDS: int = 30

# Resiliency
TRANSIENT_FAILURE_THRESHOLD: int = 5
RETRY_JITTER_FRACTION: float = 0.25
WRITE_RETRY_BASE_DELAY_SECONDS: float = 1.0
WRITE_RETRY_MAX_DELAY_SECONDS: float = 4.0
REFRESH_RETRY_BASE_DELAY_SECONDS: float = 1.0
REFRESH_RETRY_MAX_DELAY_SECONDS: float = 4.0
MAX_REFRESH_ATTEMPTS: int = 2

# Config flow error keys
ERROR_AUTH: str = "invalid_auth"
ERROR_CANNOT_CONNECT: str = "cannot_connect"
ERROR_UNKNOWN: str = "unknown"
