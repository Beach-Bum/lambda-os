"""Mix network configuration parser and validator.

A workspace may declare mix routing parameters in ``scaffold.toml`` under
the ``[mix]`` block. This module provides:

- :class:`MixConfig` — typed record of the mix routing configuration.
- :func:`parse_mix_config` — parser for the ``[mix]`` block in
  ``scaffold.toml``.
- :func:`validate_mix_config` — sanity checks on mix configuration
  (RLN enabled, sane node count, valid capability filters).

The :func:`check_logos_policy` ``require_rln_for_messaging_modules`` rule
cross-references mix state: if any messaging-class module is loaded AND
mix config exists with ``rln_enabled=False``, the proposal is denied.

See ``docs/POLICY-SCHEMA.md`` § ``require_rln_for_messaging_modules`` for
the rule.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


# Sane bounds for mix_node_count. Less than 3 isn't a meaningful mix network;
# more than 50 is almost certainly operator misconfiguration.
MIN_MIX_NODE_COUNT = 3
MAX_MIX_NODE_COUNT = 50

# Recognised capability filter values for mix routing.
VALID_CAPABILITY_FILTERS = frozenset(
    {"messaging", "chat", "delivery", "store", "relay", "filter", "lightpush"}
)


@dataclass
class MixConfig:
    """Parsed ``[mix]`` block from ``scaffold.toml``.

    Attributes:
        rln_enabled: Whether RLN rate-limiting is active for the mix layer.
        mix_node_count: Number of mix nodes in the routing path.
        capability_filters: List of capability strings that mix routes
            are filtered on.
        raw_block: The raw TOML dict, retained for diagnostics.
    """

    rln_enabled: bool = True
    mix_node_count: int = 5
    capability_filters: list[str] = field(default_factory=list)
    raw_block: dict = dataclasses.field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to a plain dict for JSON output."""
        return {
            "rln_enabled": self.rln_enabled,
            "mix_node_count": self.mix_node_count,
            "capability_filters": self.capability_filters,
        }


@dataclass
class ConfigViolation:
    """A mix configuration sanity-check violation.

    Attributes:
        field: The config field that triggered the violation.
        details: Human-readable explanation.
    """

    field: str
    details: str

    def to_dict(self) -> dict:
        """Serialize to a plain dict for JSON output."""
        return dataclasses.asdict(self)


def parse_mix_config(workspace: Path) -> MixConfig | None:
    """Parse the ``[mix]`` block from ``scaffold.toml``.

    Args:
        workspace: Path to the logos-workspace root.

    Returns:
        MixConfig if a ``[mix]`` block exists, None if scaffold.toml
        is missing or has no ``[mix]`` section.

    Raises:
        ValueError: If the ``[mix]`` block is structurally invalid
            (e.g. ``rln_enabled`` is not a bool).
    """
    scaffold_path = workspace / "scaffold.toml"
    if not scaffold_path.exists():
        return None

    with scaffold_path.open("rb") as f:
        data = tomllib.load(f)

    mix_block = data.get("mix")
    if mix_block is None:
        return None
    if not isinstance(mix_block, dict):
        raise ValueError(f"[mix] must be a table; got {type(mix_block).__name__}")

    rln_enabled = mix_block.get("rln_enabled", True)
    if not isinstance(rln_enabled, bool):
        raise ValueError(
            f"[mix].rln_enabled must be a bool; got {type(rln_enabled).__name__}"
        )

    mix_node_count = mix_block.get("mix_node_count", 5)
    if not isinstance(mix_node_count, int):
        raise ValueError(
            f"[mix].mix_node_count must be an int; got {type(mix_node_count).__name__}"
        )

    capability_filters = mix_block.get("capability_filters", [])
    if not isinstance(capability_filters, list):
        raise ValueError(
            f"[mix].capability_filters must be a list; "
            f"got {type(capability_filters).__name__}"
        )
    for i, cap in enumerate(capability_filters):
        if not isinstance(cap, str):
            raise ValueError(
                f"[mix].capability_filters[{i}] must be a string; "
                f"got {type(cap).__name__}"
            )

    return MixConfig(
        rln_enabled=rln_enabled,
        mix_node_count=mix_node_count,
        capability_filters=capability_filters,
        raw_block=mix_block,
    )


def validate_mix_config(config: MixConfig) -> list[ConfigViolation]:
    """Validate a parsed MixConfig for sanity.

    Checks:
        - mix_node_count within [MIN_MIX_NODE_COUNT, MAX_MIX_NODE_COUNT]
        - All capability_filters are recognised values
        - RLN should be enabled (warning-level, but returned as violation)

    Args:
        config: Parsed MixConfig to validate.

    Returns:
        List of ConfigViolation. Empty means the config is sane.
    """
    violations: list[ConfigViolation] = []

    if config.mix_node_count < MIN_MIX_NODE_COUNT:
        violations.append(
            ConfigViolation(
                field="mix_node_count",
                details=(
                    f"mix_node_count={config.mix_node_count} is below minimum "
                    f"({MIN_MIX_NODE_COUNT}); not a meaningful mix network"
                ),
            )
        )
    elif config.mix_node_count > MAX_MIX_NODE_COUNT:
        violations.append(
            ConfigViolation(
                field="mix_node_count",
                details=(
                    f"mix_node_count={config.mix_node_count} exceeds maximum "
                    f"({MAX_MIX_NODE_COUNT}); likely operator misconfiguration"
                ),
            )
        )

    for cap in config.capability_filters:
        if cap not in VALID_CAPABILITY_FILTERS:
            violations.append(
                ConfigViolation(
                    field="capability_filters",
                    details=(
                        f"unrecognised capability filter {cap!r}; "
                        f"valid values: {sorted(VALID_CAPABILITY_FILTERS)}"
                    ),
                )
            )

    if not config.rln_enabled:
        violations.append(
            ConfigViolation(
                field="rln_enabled",
                details="RLN is disabled in mix config; messaging modules require RLN",
            )
        )

    return violations
