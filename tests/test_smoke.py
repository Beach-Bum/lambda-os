"""Smoke tests for agentix-logos.

These should pass on any machine without logos-workspace installed —
they cover the parser, policy schema, and CLI surface only. Real
integration tests against a logos-workspace checkout live in
tests/integration/ (Phase 1 work).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_import_package():
    import agentix_logos

    assert agentix_logos.__version__


def test_cli_help():
    from agentix_logos.cli import build_parser

    parser = build_parser()
    # Should not raise
    parser.format_help()


def test_logos_policy_defaults():
    from agentix_logos.policy import LogosPolicy

    p = LogosPolicy()
    assert p.require_metadata_json is True
    assert p.require_rln_for_messaging_modules is True
    assert "nixpkgs" in p.forbid_flake_overrides
    assert "logos-blockchain" in p.forbid_flake_overrides
    assert p.forbid_wallet_operations is True
    assert p.forbid_live_localnet is True


def test_policy_load_missing_returns_none(tmp_path: Path):
    from agentix_logos.policy import load_policy

    assert load_policy(tmp_path) is None


def test_policy_load_with_logos_block(tmp_path: Path):
    from agentix_logos.policy import load_policy

    agentix_dir = tmp_path / ".agentix"
    agentix_dir.mkdir()
    policy = {
        "denied": ["sudo"],
        "allowed": ["ws build"],
        "logos": {
            "require_rln_for_messaging_modules": True,
            "forbid_flake_overrides": ["nixpkgs", "logos-blockchain"],
        },
    }
    (agentix_dir / "policy.json").write_text(json.dumps(policy))
    loaded = load_policy(tmp_path)
    assert loaded is not None
    assert loaded.has_logos_block is True
    assert loaded.logos.require_rln_for_messaging_modules is True


def test_logoscore_call_parser():
    from agentix_logos.verify_logoscore import _parse_call

    assert _parse_call("storage_module.healthcheck()") == ("storage_module", "healthcheck", [])
    assert _parse_call("chat.send(hello, world)") == ("chat", "send", ["hello", "world"])
    with pytest.raises(ValueError):
        _parse_call("not a valid call")


def test_module_metadata_from_json():
    from agentix_logos.modules import ModuleMetadata

    raw = {
        "name": "storage_module",
        "main": {"linux-x86_64-dev": "storage.lgx"},
        "dependencies": ["libp2p"],
        "capabilities": ["storage"],
        "rln": False,
    }
    md = ModuleMetadata.from_json("storage_module", raw)
    assert md.name == "storage_module"
    assert md.dependencies == ["libp2p"]
    assert md.capabilities == ["storage"]
    assert md.rln is False


def test_messaging_module_detection():
    from agentix_logos.modules import ModuleMetadata, ModuleRef

    md = ModuleMetadata(name="chat", capabilities=["messaging"], rln=True)
    ref = ModuleRef(
        name="chat",
        flake_ref="github:logos-co/chat",
        role="project",
        metadata=md,
        has_rln=True,
    )
    assert ref.is_messaging_module is True


def test_policy_violation_to_dict():
    from agentix_logos.policy import PolicyViolation

    v = PolicyViolation(rule="test", severity="deny", details="x", module="m")
    d = v.to_dict()
    assert d["rule"] == "test"
    assert d["severity"] == "deny"


def test_check_policy_no_modules_no_diff(tmp_path: Path):
    """Empty proposal + no modules = no violations except missing policy file."""
    from agentix_logos.policy import check_logos_policy

    violations = check_logos_policy(tmp_path, proposal_diff="", modules_touched=[])
    # We expect exactly one violation: policy_file_missing
    assert len(violations) == 1
    assert violations[0].rule == "policy_file_missing"
