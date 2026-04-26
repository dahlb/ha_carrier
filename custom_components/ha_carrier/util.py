from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, cast

from homeassistant.core import callback

REDACTED = "**REDACTED**"


@callback
def async_redact_data[T](data: T, to_redact: Iterable[Any]) -> T:
    """Redact sensitive data in a dict."""
    if not isinstance(data, Mapping | list):
        return data

    if isinstance(data, list):
        return cast(T, [async_redact_data(val, to_redact) for val in data])

    redacted = {**data}

    for key, value in redacted.items():
        if value is None:
            continue
        if isinstance(value, str) and not value:
            continue
        if key in to_redact:
            redacted[key] = REDACTED
        elif isinstance(value, Mapping):
            redacted[key] = async_redact_data(value, to_redact)
        elif isinstance(value, list):
            redacted[key] = [async_redact_data(item, to_redact) for item in value]

    return cast(T, redacted)
