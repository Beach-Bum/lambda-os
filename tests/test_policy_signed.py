"""Tests for require_signed_metadata policy enforcement (P5 / PER-21)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentix_logos.keys import SIGNATURE_FIELD, sign_metadata
from agentix_logos.modules import ModuleMetadata, ModuleRef
from agentix_logos.policy import check_logos_policy

# Same fixtures as tests/test_keys.py. Documented in that file.
SEED_1_HEX = "6167656e7469782d6c6f676f732d746573742d736565642d312d414141414141"
SEED_2_HEX = "6167656e7469782d6c6f676f732d746573742d736565642d322d424242424242"
KEY_ID_1 = "ift-stub-key-001"
KEY_ID_2 = "ift-stub-key-002"

REPO_ROOT = Path(__file__).resolve().parent.parent
STUB_REGISTRY = REPO_ROOT / "examples" / "key-registry-stub.json"


def _make_workspace(tmp_path: Path, *, registry_path: Path | None = None) -> Path:
    """Create a minimal workspace with a policy.json that turns on
    require_signed_metadata. Also makes it a git repo so the workspace
    validators are happy in cases where we wire that up later.

    Args:
        tmp_path: pytest tmp dir.
        registry_path: Override the key_registry_path setting in policy.
            If None, the policy leaves it unset and falls back to the
            bundled stub registry.

    Returns:
        Path to the workspace root.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".git").mkdir()  # cheap "is a git repo" smoke
    agentix_dir = ws / ".agentix"
    agentix_dir.mkdir()
    logos_block: dict = {
        "require_metadata_json": True,
        "require_signed_metadata": True,
        "require_rln_for_messaging_modules": False,
    }
    if registry_path is not None:
        logos_block["key_registry_path"] = str(registry_path)
    (agentix_dir / "policy.json").write_text(
        json.dumps({"denied": [], "review": [], "allowed": [], "logos": logos_block})
    )
    return ws


def _signed_module(name: str, key_id: str, seed_hex: str) -> ModuleRef:
    """Construct a ModuleRef whose metadata is signed by the given key."""
    raw_unsigned = {
        "name": name,
        "main": "lib.lgx",
        "dependencies": [],
        "capabilities": ["storage"],
        "rln": False,
    }
    sig = sign_metadata(raw_unsigned, key_id, seed_hex)
    raw_signed = {**raw_unsigned, SIGNATURE_FIELD: sig}
    md = ModuleMetadata.from_json(name, raw_signed)
    return ModuleRef(
        name=name,
        flake_ref=f"github:fake/{name}",
        role="project",
        metadata=md,
    )


def _unsigned_module(name: str) -> ModuleRef:
    """Construct a ModuleRef whose metadata has no signature field."""
    raw = {
        "name": name,
        "main": "lib.lgx",
        "dependencies": [],
        "capabilities": ["storage"],
        "rln": False,
    }
    md = ModuleMetadata.from_json(name, raw)
    return ModuleRef(
        name=name,
        flake_ref=f"github:fake/{name}",
        role="project",
        metadata=md,
    )


def _tampered_signed_module(name: str, key_id: str, seed_hex: str) -> ModuleRef:
    """Signed module whose metadata has been mutated AFTER signing."""
    raw_unsigned = {
        "name": name,
        "main": "lib.lgx",
        "dependencies": [],
        "capabilities": ["storage"],
        "rln": False,
    }
    sig = sign_metadata(raw_unsigned, key_id, seed_hex)
    # Tamper: change capabilities, but keep the original (now-stale) signature.
    raw_tampered = {
        **raw_unsigned,
        "capabilities": ["messaging"],  # changed!
        SIGNATURE_FIELD: sig,
    }
    md = ModuleMetadata.from_json(name, raw_tampered)
    return ModuleRef(
        name=name,
        flake_ref=f"github:fake/{name}",
        role="project",
        metadata=md,
    )


def _wrong_key_signed_module(name: str) -> ModuleRef:
    """Signed module whose signature claims a key_id NOT in the registry."""
    raw_unsigned = {
        "name": name,
        "main": "lib.lgx",
        "dependencies": [],
        "capabilities": ["storage"],
        "rln": False,
    }
    # Use a real seed but lie about the key_id — registry has no such id.
    sig = sign_metadata(raw_unsigned, "totally-unknown-key-id", SEED_1_HEX)
    raw_signed = {**raw_unsigned, SIGNATURE_FIELD: sig}
    md = ModuleMetadata.from_json(name, raw_signed)
    return ModuleRef(
        name=name,
        flake_ref=f"github:fake/{name}",
        role="project",
        metadata=md,
    )


# ─────────────────────────────────────────────────────────────────────────────
# The 6 acceptance-criteria scenarios
# ─────────────────────────────────────────────────────────────────────────────


def test_valid_signed_module_passes(tmp_path: Path):
    """A correctly-signed module produces no require_signed_metadata violation."""
    ws = _make_workspace(tmp_path, registry_path=STUB_REGISTRY)
    mod = _signed_module("ok_module", KEY_ID_1, SEED_1_HEX)
    violations = check_logos_policy(ws, proposal_diff="", modules_touched=[mod])
    assert not [v for v in violations if v.rule == "require_signed_metadata"], violations


def test_invalid_signature_violation(tmp_path: Path):
    """Tampered metadata (sig doesn't match content) yields a deny violation."""
    ws = _make_workspace(tmp_path, registry_path=STUB_REGISTRY)
    mod = _tampered_signed_module("tampered", KEY_ID_1, SEED_1_HEX)
    violations = check_logos_policy(ws, proposal_diff="", modules_touched=[mod])
    sig_violations = [v for v in violations if v.rule == "require_signed_metadata"]
    assert len(sig_violations) == 1
    assert sig_violations[0].severity == "deny"
    assert sig_violations[0].module == "tampered"
    assert "did not verify" in sig_violations[0].details


def test_unknown_key_violation(tmp_path: Path):
    """Signature claiming an unknown key_id yields a deny violation."""
    ws = _make_workspace(tmp_path, registry_path=STUB_REGISTRY)
    mod = _wrong_key_signed_module("wrong_key")
    violations = check_logos_policy(ws, proposal_diff="", modules_touched=[mod])
    sig_violations = [v for v in violations if v.rule == "require_signed_metadata"]
    assert len(sig_violations) == 1
    assert sig_violations[0].severity == "deny"
    assert sig_violations[0].module == "wrong_key"


def test_missing_signature_violation(tmp_path: Path):
    """A module with no signature field at all yields a deny violation."""
    ws = _make_workspace(tmp_path, registry_path=STUB_REGISTRY)
    mod = _unsigned_module("unsigned")
    violations = check_logos_policy(ws, proposal_diff="", modules_touched=[mod])
    sig_violations = [v for v in violations if v.rule == "require_signed_metadata"]
    assert len(sig_violations) == 1
    assert sig_violations[0].severity == "deny"
    assert sig_violations[0].module == "unsigned"
    assert "missing signature" in sig_violations[0].details


def test_mixed_batch_only_invalid_violate(tmp_path: Path):
    """Batch with valid + invalid modules: only invalid get violations."""
    ws = _make_workspace(tmp_path, registry_path=STUB_REGISTRY)
    good_a = _signed_module("good_a", KEY_ID_1, SEED_1_HEX)
    good_b = _signed_module("good_b", KEY_ID_2, SEED_2_HEX)
    bad_unsigned = _unsigned_module("bad_unsigned")
    bad_tampered = _tampered_signed_module("bad_tampered", KEY_ID_1, SEED_1_HEX)
    violations = check_logos_policy(
        ws,
        proposal_diff="",
        modules_touched=[good_a, good_b, bad_unsigned, bad_tampered],
    )
    sig_violations = [v for v in violations if v.rule == "require_signed_metadata"]
    flagged = {v.module for v in sig_violations}
    assert flagged == {"bad_unsigned", "bad_tampered"}, sig_violations


def test_registry_not_found_violation(tmp_path: Path):
    """Pointing at a non-existent registry yields a single deny violation."""
    ws = _make_workspace(tmp_path, registry_path=tmp_path / "no-such-registry.json")
    mod = _signed_module("module_x", KEY_ID_1, SEED_1_HEX)
    violations = check_logos_policy(ws, proposal_diff="", modules_touched=[mod])
    sig_violations = [v for v in violations if v.rule == "require_signed_metadata"]
    # Single registry-level deny, NOT per-module spam
    assert len(sig_violations) == 1
    assert sig_violations[0].severity == "deny"
    assert sig_violations[0].module is None
    assert "key registry not found" in sig_violations[0].details


# ─────────────────────────────────────────────────────────────────────────────
# Bonus: rule disabled by default
# ─────────────────────────────────────────────────────────────────────────────


def test_rule_disabled_no_violations(tmp_path: Path):
    """When require_signed_metadata is False, unsigned modules pass."""
    ws = tmp_path / "ws-off"
    ws.mkdir()
    (ws / ".git").mkdir()
    (ws / ".agentix").mkdir()
    (ws / ".agentix" / "policy.json").write_text(
        json.dumps(
            {
                "logos": {
                    "require_metadata_json": True,
                    "require_signed_metadata": False,  # off!
                }
            }
        )
    )
    mod = _unsigned_module("any_module")
    violations = check_logos_policy(ws, proposal_diff="", modules_touched=[mod])
    sig_violations = [v for v in violations if v.rule == "require_signed_metadata"]
    assert sig_violations == []


@pytest.mark.parametrize(
    "key_id,seed_hex",
    [(KEY_ID_1, SEED_1_HEX), (KEY_ID_2, SEED_2_HEX)],
)
def test_both_stub_keys_work(tmp_path: Path, key_id: str, seed_hex: str):
    """Both stub registry keys verify their own signatures."""
    ws = _make_workspace(tmp_path, registry_path=STUB_REGISTRY)
    mod = _signed_module(f"signed_by_{key_id}", key_id, seed_hex)
    violations = check_logos_policy(ws, proposal_diff="", modules_touched=[mod])
    sig_violations = [v for v in violations if v.rule == "require_signed_metadata"]
    assert sig_violations == []
