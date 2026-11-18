"""Validate the integration's quality_scale.yaml self-assessment."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest
import yaml

QUALITY_SCALE_FILE = (
    Path(__file__).parent.parent / "custom_components" / "ha_carrier" / "quality_scale.yaml"
)

# Rule names from Home Assistant's script/hassfest/quality_scale.py. hassfest skips
# this file for custom integrations, so keep this list in sync when HA adds rules.
QUALITY_SCALE_RULES: frozenset[str] = frozenset(
    {
        # Bronze
        "action-setup",
        "appropriate-polling",
        "brands",
        "common-modules",
        "config-flow",
        "config-flow-test-coverage",
        "dependency-transparency",
        "docs-actions",
        "docs-conditions",
        "docs-high-level-description",
        "docs-installation-instructions",
        "docs-removal-instructions",
        "docs-triggers",
        "entity-event-setup",
        "entity-unique-id",
        "has-entity-name",
        "runtime-data",
        "test-before-configure",
        "test-before-setup",
        "unique-config-entry",
        # Silver
        "action-exceptions",
        "config-entry-unloading",
        "docs-configuration-parameters",
        "docs-installation-parameters",
        "entity-unavailable",
        "integration-owner",
        "log-when-unavailable",
        "parallel-updates",
        "reauthentication-flow",
        "test-coverage",
        # Gold
        "devices",
        "diagnostics",
        "discovery",
        "discovery-update-info",
        "docs-data-update",
        "docs-examples",
        "docs-known-limitations",
        "docs-supported-devices",
        "docs-supported-functions",
        "docs-troubleshooting",
        "docs-use-cases",
        "dynamic-devices",
        "entity-category",
        "entity-device-class",
        "entity-disabled-by-default",
        "entity-translations",
        "exception-translations",
        "icon-translations",
        "reconfiguration-flow",
        "repair-issues",
        "stale-devices",
        # Platinum
        "async-dependency",
        "inject-websession",
        "strict-typing",
    }
)


def _load_rules() -> dict[str, Any]:
    """Return the ``rules`` mapping from quality_scale.yaml.

    Returns:
        dict[str, Any]: Rule name to status string or status mapping.
    """
    data = yaml.safe_load(QUALITY_SCALE_FILE.read_text(encoding="utf-8"))
    assert set(data) == {"rules"}
    return data["rules"]


def test_quality_scale_lists_every_rule_exactly() -> None:
    """List every Home Assistant quality scale rule and nothing else."""
    assert set(_load_rules()) == QUALITY_SCALE_RULES


@pytest.mark.parametrize("rule", sorted(QUALITY_SCALE_RULES))
def test_quality_scale_rule_matches_hassfest_schema(rule: str) -> None:
    """Match hassfest's schema: a status, and a comment whenever a rule is exempt."""
    value = _load_rules()[rule]

    if isinstance(value, str):
        assert value in {"todo", "done"}
        return
    assert set(value) == {"status", "comment"}
    assert value["status"] in {"todo", "done", "exempt"}
    assert isinstance(value["comment"], str)
    assert value["comment"].strip()


@pytest.mark.parametrize(
    ("platform", "expected"),
    [("binary_sensor", 0), ("sensor", 0), ("climate", 1), ("select", 1)],
)
def test_every_platform_sets_parallel_updates(platform: str, expected: int) -> None:
    """Set PARALLEL_UPDATES on every platform, as the parallel-updates rule requires.

    Read-only platforms use 0 because the coordinator fetches data centrally;
    platforms that send commands to Carrier's rate-limited cloud API use 1.
    """
    module = importlib.import_module(f"custom_components.ha_carrier.{platform}")

    assert expected == module.PARALLEL_UPDATES
