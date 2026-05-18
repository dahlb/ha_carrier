"""Carrier-specific exception types shared across integration modules."""

from homeassistant.exceptions import HomeAssistantError


class CarrierUnauthorizedError(HomeAssistantError):
    """Raised when 401 responses persist beyond the unauthorized threshold."""
