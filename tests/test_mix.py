"""Tests for agentix_logos.mix and mix/RLN policy cross-reference (P3 / PER-19)."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from agentix_logos.mix import (
    MAX_MIX_NODE_COUNT,
    MIN_MIX_NODE_COUNT,
    MixConfig,
    parse_mix_config,
    validate_mix_config,
)
from agentix_logos.modules import ModuleMetadata, ModuleRef
from agentix_logos.policy import check_logos_policy

# ─────────────────────────────────────────────────────────────────────
# parse_mix_config
# ─────────────────────────────────────────────────────────────────────


def test_parse_mix_config_valid(tmp_path: Path):
    """A well-formed [mix] block parses into MixConfig."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "scaffold.toml").write_text(
        textwrap.dedent(
            """
            [mix]
            rln_enabled = true
            mix_node_count = 7
            capability_filters = ["messaging", "relay"]
            """
        )
    )
    config = parse_mix_config(ws)
    assert config is not None
    assert config.rln_enabled is True
    assert config.mix_node_count == 7
    assert config.capability_filters == ["messaging", "relay"]


def test_parse_mix_config_defaults(tmp_path: Path):
    """A minimal [mix] block uses defaults for missing fields."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "scaffold.toml").write_text("[mix]\n")
    config = parse_mix_config(ws)
    assert config is not None
    assert config.rln_enabled is True
    assert config.mix_node_count == 5
    assert config.capability_filters == []


def test_parse_mix_config_no_scaffold(tmp_path: Path):
    """Missing scaffold.toml returns None."""
    ws = tmp_path / "ws"
    ws.mkdir()
    assert parse_mix_config(ws) is None


def test_parse_mix_config_no_mix_section(tmp_path: Path):
    """scaffold.toml without [mix] returns None."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "scaffold.toml").write_text("[modules.foo]\nflake = 'x'\n")
    assert parse_mix_config(ws) is None


def test_parse_mix_config_rejects_bad_rln_type(tmp_path: Path):
    """Non-bool rln_enabled raises ValueError."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "scaffold.toml").write_text(
        textwrap.dedent(
            """
            [mix]
            rln_enabled = "yes"
            """
        )
    )
    with pytest.raises(ValueError, match="rln_enabled must be a bool"):
        parse_mix_config(ws)


def test_parse_mix_config_rejects_bad_node_count_type(tmp_path: Path):
    """Non-int mix_node_count raises ValueError."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "scaffold.toml").write_text(
        textwrap.dedent(
            """
            [mix]
            mix_node_count = "five"
            """
        )
    )
    with pytest.raises(ValueError, match="mix_node_count must be an int"):
        parse_mix_config(ws)


def test_parse_mix_config_rejects_bad_capability_type(tmp_path: Path):
    """Non-list capability_filters raises ValueError."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "scaffold.toml").write_text(
        textwrap.dedent(
            """
            [mix]
            capability_filters = "messaging"
            """
        )
    )
    with pytest.raises(ValueError, match="capability_filters must be a list"):
        parse_mix_config(ws)


# ─────────────────────────────────────────────────────────────────────
# validate_mix_config
# ─────────────────────────────────────────────────────────────────────


def test_validate_valid_config():
    """A sane config produces no violations."""
    config = MixConfig(
        rln_enabled=True, mix_node_count=5, capability_filters=["messaging", "relay"]
    )
    assert validate_mix_config(config) == []


def test_validate_node_count_too_low():
    """mix_node_count below MIN produces a violation."""
    config = MixConfig(rln_enabled=True, mix_node_count=2)
    violations = validate_mix_config(config)
    assert len(violations) == 1
    assert violations[0].field == "mix_node_count"
    assert "below minimum" in violations[0].details


def test_validate_node_count_too_high():
    """mix_node_count above MAX produces a violation."""
    config = MixConfig(rln_enabled=True, mix_node_count=100)
    violations = validate_mix_config(config)
    assert len(violations) == 1
    assert violations[0].field == "mix_node_count"
    assert "exceeds maximum" in violations[0].details


def test_validate_node_count_at_boundaries():
    """Boundary values (MIN and MAX) are valid."""
    config_min = MixConfig(rln_enabled=True, mix_node_count=MIN_MIX_NODE_COUNT)
    config_max = MixConfig(rln_enabled=True, mix_node_count=MAX_MIX_NODE_COUNT)
    assert validate_mix_config(config_min) == []
    assert validate_mix_config(config_max) == []


def test_validate_invalid_capability_filter():
    """Unrecognised capability filter produces a violation."""
    config = MixConfig(
        rln_enabled=True, mix_node_count=5, capability_filters=["messaging", "bogus"]
    )
    violations = validate_mix_config(config)
    cap_violations = [v for v in violations if v.field == "capability_filters"]
    assert len(cap_violations) == 1
    assert "bogus" in cap_violations[0].details


def test_validate_rln_disabled():
    """rln_enabled=False produces a violation."""
    config = MixConfig(rln_enabled=False, mix_node_count=5)
    violations = validate_mix_config(config)
    rln_violations = [v for v in violations if v.field == "rln_enabled"]
    assert len(rln_violations) == 1
    assert "RLN is disabled" in rln_violations[0].details


# ─────────────────────────────────────────────────────────────────────
# Policy cross-reference: require_rln_for_messaging_modules + mix state
# ─────────────────────────────────────────────────────────────────────


def _make_messaging_module(name: str = "waku-chat") -> ModuleRef:
    """Build a ModuleRef that declares messaging capability with RLN on."""
    meta = ModuleMetadata(
        name=name,
        capabilities=["messaging"],
        rln=True,
    )
    return ModuleRef(
        name=name,
        flake_ref=f"github:logos-co/{name}",
        role="project",
        metadata=meta,
        has_rln=True,
    )


def _make_workspace_with_mix(
    tmp_path: Path,
    *,
    rln_enabled: bool = True,
    mix_node_count: int = 5,
    require_rln: bool = True,
) -> Path:
    """Build a minimal workspace with a [mix] block for policy tests."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".git").mkdir()
    (ws / ".agentix").mkdir()

    scaffold = textwrap.dedent(
        f"""
        [mix]
        rln_enabled = {'true' if rln_enabled else 'false'}
        mix_node_count = {mix_node_count}
        capability_filters = ["messaging"]
        """
    )
    (ws / "scaffold.toml").write_text(scaffold)

    policy = {
        "logos": {
            "require_metadata_json": False,
            "require_signed_metadata": False,
            "require_rln_for_messaging_modules": require_rln,
            "lez_programs_pinned": False,
        }
    }
    (ws / ".agentix" / "policy.json").write_text(json.dumps(policy))
    return ws


def test_policy_mix_rln_disabled_with_messaging_module(tmp_path: Path):
    """Mix rln_enabled=false + messaging module → deny violation."""
    ws = _make_workspace_with_mix(tmp_path, rln_enabled=False)
    module = _make_messaging_module()
    violations = check_logos_policy(ws, proposal_diff="", modules_touched=[module])
    mix_violations = [
        v
        for v in violations
        if v.rule == "require_rln_for_messaging_modules"
        and "Mix config" in v.details
    ]
    assert len(mix_violations) == 1
    assert "rln_enabled=false" in mix_violations[0].details
    assert "waku-chat" in mix_violations[0].details


def test_policy_mix_rln_enabled_with_messaging_module(tmp_path: Path):
    """Mix rln_enabled=true + messaging module → no mix-related violation."""
    ws = _make_workspace_with_mix(tmp_path, rln_enabled=True)
    module = _make_messaging_module()
    violations = check_logos_policy(ws, proposal_diff="", modules_touched=[module])
    mix_violations = [
        v
        for v in violations
        if v.rule == "require_rln_for_messaging_modules"
        and "Mix config" in v.details
    ]
    assert mix_violations == []


def test_policy_mix_rln_disabled_no_messaging_module(tmp_path: Path):
    """Mix rln_enabled=false but no messaging modules → no violation."""
    ws = _make_workspace_with_mix(tmp_path, rln_enabled=False)
    violations = check_logos_policy(ws, proposal_diff="", modules_touched=[])
    mix_violations = [
        v
        for v in violations
        if v.rule == "require_rln_for_messaging_modules"
        and "Mix config" in v.details
    ]
    assert mix_violations == []


def test_policy_mix_rule_disabled(tmp_path: Path):
    """When require_rln_for_messaging_modules is disabled, no mix check fires."""
    ws = _make_workspace_with_mix(tmp_path, rln_enabled=False, require_rln=False)
    module = _make_messaging_module()
    violations = check_logos_policy(ws, proposal_diff="", modules_touched=[module])
    rln_violations = [
        v for v in violations if v.rule == "require_rln_for_messaging_modules"
    ]
    assert rln_violations == []
