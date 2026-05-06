"""T4 (PER-5): Tests for forbidden flake override regex scan.

Verifies that the regex-based --override-input detection correctly
identifies forbidden overrides while allowing permitted ones.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentix_logos.policy import check_logos_policy


def _write_policy(tmp_path: Path, forbid: list[str] | None = None) -> None:
    agentix_dir = tmp_path / ".agentix"
    agentix_dir.mkdir(exist_ok=True)
    logos = {}
    if forbid is not None:
        logos["forbid_flake_overrides"] = forbid
    policy = {"denied": [], "allowed": [], "logos": logos}
    (agentix_dir / "policy.json").write_text(json.dumps(policy))


def test_no_override_passes(tmp_path: Path):
    """Proposal with no --override-input produces no violation."""
    _write_policy(tmp_path, ["nixpkgs", "logos-blockchain"])
    diff = "nix build .#logos-basecamp --out-link /tmp/result"
    violations = check_logos_policy(tmp_path, proposal_diff=diff, modules_touched=[])
    override_vs = [v for v in violations if v.rule == "forbid_flake_overrides"]
    assert len(override_vs) == 0


def test_forbidden_override_fails(tmp_path: Path):
    """Proposal with --override-input nixpkgs triggers violation."""
    _write_policy(tmp_path, ["nixpkgs", "logos-blockchain"])
    diff = "nix build .#foo --override-input nixpkgs path:/tmp/nixpkgs-local"
    violations = check_logos_policy(tmp_path, proposal_diff=diff, modules_touched=[])
    override_vs = [v for v in violations if v.rule == "forbid_flake_overrides"]
    assert len(override_vs) == 1
    assert "nixpkgs" in override_vs[0].details


def test_allowed_override_passes(tmp_path: Path):
    """Proposal with --override-input for a non-forbidden input passes."""
    _write_policy(tmp_path, ["nixpkgs", "logos-blockchain"])
    diff = "nix build .#foo --override-input logos-chat-module path:/tmp/chat"
    violations = check_logos_policy(tmp_path, proposal_diff=diff, modules_touched=[])
    override_vs = [v for v in violations if v.rule == "forbid_flake_overrides"]
    assert len(override_vs) == 0


def test_multiple_overrides_one_forbidden(tmp_path: Path):
    """Multiple --override-input flags, only the forbidden one fires."""
    _write_policy(tmp_path, ["nixpkgs", "logos-blockchain"])
    diff = (
        "nix build .#foo "
        "--override-input logos-chat path:/tmp/chat "
        "--override-input logos-blockchain path:/tmp/bc "
        "--override-input logos-storage path:/tmp/storage"
    )
    violations = check_logos_policy(tmp_path, proposal_diff=diff, modules_touched=[])
    override_vs = [v for v in violations if v.rule == "forbid_flake_overrides"]
    assert len(override_vs) == 1
    assert "logos-blockchain" in override_vs[0].details


def test_override_with_tabs_and_newlines(tmp_path: Path):
    """Regex handles whitespace variants (tabs, newlines)."""
    _write_policy(tmp_path, ["nixpkgs"])
    diff = "nix build .#foo --override-input\tnixpkgs\tpath:/tmp/np"
    violations = check_logos_policy(tmp_path, proposal_diff=diff, modules_touched=[])
    override_vs = [v for v in violations if v.rule == "forbid_flake_overrides"]
    assert len(override_vs) == 1


def test_multiple_forbidden_overrides(tmp_path: Path):
    """Both forbidden inputs flagged when both present."""
    _write_policy(tmp_path, ["nixpkgs", "logos-blockchain"])
    diff = (
        "nix build .#foo "
        "--override-input nixpkgs path:/nix "
        "--override-input logos-blockchain path:/bc"
    )
    violations = check_logos_policy(tmp_path, proposal_diff=diff, modules_touched=[])
    override_vs = [v for v in violations if v.rule == "forbid_flake_overrides"]
    assert len(override_vs) == 2
    names = {v.details for v in override_vs}
    assert any("nixpkgs" in d for d in names)
    assert any("logos-blockchain" in d for d in names)
