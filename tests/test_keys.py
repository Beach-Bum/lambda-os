"""Unit tests for agentix_logos.keys (P5 / PER-21)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentix_logos.keys import (
    SIGNATURE_FIELD,
    KeyRegistry,
    canonical_signing_bytes,
    sign_metadata,
)

# Test fixture seeds. NOT secrets — documented here so future test authors
# can produce signatures that verify against the stub key registry shipped
# at examples/key-registry-stub.json. Don't sign production modules with
# these seeds.
SEED_1_HEX = "6167656e7469782d6c6f676f732d746573742d736565642d312d414141414141"  # 32 bytes
SEED_2_HEX = "6167656e7469782d6c6f676f732d746573742d736565642d322d424242424242"  # 32 bytes
KEY_ID_1 = "ift-stub-key-001"
KEY_ID_2 = "ift-stub-key-002"

REPO_ROOT = Path(__file__).resolve().parent.parent
STUB_REGISTRY = REPO_ROOT / "examples" / "key-registry-stub.json"


def test_load_stub_registry():
    """The bundled stub registry loads and contains the documented test keys."""
    reg = KeyRegistry.load(STUB_REGISTRY)
    assert reg.version == 1
    assert reg.has_key(KEY_ID_1)
    assert reg.has_key(KEY_ID_2)
    assert not reg.has_key("nonexistent-key")
    assert reg.source_path == STUB_REGISTRY


def test_load_missing_raises():
    """Loading a non-existent path raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        KeyRegistry.load(REPO_ROOT / "does-not-exist-registry.json")


def test_load_malformed_raises(tmp_path: Path):
    """Bad JSON raises ValueError."""
    bad = tmp_path / "bad.json"
    bad.write_text("not json {")
    with pytest.raises(ValueError):
        KeyRegistry.load(bad)


def test_load_bad_version_type_raises(tmp_path: Path):
    """Non-int version raises ValueError."""
    bad = tmp_path / "bad-version.json"
    bad.write_text(json.dumps({"version": "1", "keys": []}))
    with pytest.raises(ValueError):
        KeyRegistry.load(bad)


def test_load_bad_public_key_hex_raises(tmp_path: Path):
    """Malformed public_key_hex raises ValueError."""
    bad = tmp_path / "bad-pubkey.json"
    bad.write_text(
        json.dumps(
            {
                "version": 1,
                "keys": [{"key_id": "x", "public_key_hex": "ZZ-not-hex"}],
            }
        )
    )
    with pytest.raises(ValueError, match="invalid public_key_hex"):
        KeyRegistry.load(bad)


def test_canonical_signing_bytes_excludes_signature_field():
    """Signature field is excluded so signing and verification agree."""
    md_unsigned = {"name": "x", "rln": True}
    md_signed = {"name": "x", "rln": True, SIGNATURE_FIELD: "ift-stub-key-001:abc123"}
    assert canonical_signing_bytes(md_unsigned) == canonical_signing_bytes(md_signed)


def test_canonical_signing_bytes_is_sorted():
    """Key order in input dict doesn't change canonical output."""
    a = {"a": 1, "b": 2}
    b = {"b": 2, "a": 1}
    assert canonical_signing_bytes(a) == canonical_signing_bytes(b)
    # Compact separators, sorted keys, no whitespace
    assert canonical_signing_bytes(a) == b'{"a":1,"b":2}'


def test_sign_then_verify_roundtrip():
    """A signature produced by sign_metadata verifies against the stub registry."""
    reg = KeyRegistry.load(STUB_REGISTRY)
    md = {"name": "test_module", "capabilities": ["storage"], "rln": False}
    sig = sign_metadata(md, KEY_ID_1, SEED_1_HEX)
    assert sig.startswith(KEY_ID_1 + ":")
    assert reg.verify(md, sig) is True


def test_verify_invalid_signature_returns_false():
    """A signature whose bytes don't match the metadata fails verification."""
    reg = KeyRegistry.load(STUB_REGISTRY)
    md = {"name": "test_module", "rln": False}
    sig = sign_metadata(md, KEY_ID_1, SEED_1_HEX)
    # Tamper: change rln from False to True; same signature won't verify.
    tampered = {"name": "test_module", "rln": True}
    assert reg.verify(tampered, sig) is False


def test_verify_unknown_key_returns_false():
    """Signature using a key not in the registry fails verification."""
    reg = KeyRegistry.load(STUB_REGISTRY)
    md = {"name": "x"}
    # Sign with seed_1 but claim it's a different key_id not in the registry.
    sig = sign_metadata(md, "fake-unknown-key-999", SEED_1_HEX)
    assert reg.verify(md, sig) is False


def test_verify_malformed_signature_returns_false():
    """Various malformed signatures all return False (no exceptions)."""
    reg = KeyRegistry.load(STUB_REGISTRY)
    md = {"name": "x"}
    # Wrong shape (no colon)
    assert reg.verify(md, "no-colon-here") is False
    # Empty
    assert reg.verify(md, "") is False
    # Non-string
    assert reg.verify(md, None) is False  # type: ignore[arg-type]
    # Bad hex
    assert reg.verify(md, f"{KEY_ID_1}:NOT_HEX_ZZZ") is False
    # Wrong sig length but valid hex
    assert reg.verify(md, f"{KEY_ID_1}:00ff") is False


def test_verify_works_with_two_distinct_keys():
    """Signatures by different keys both verify when each is in the registry."""
    reg = KeyRegistry.load(STUB_REGISTRY)
    md1 = {"name": "module-one"}
    md2 = {"name": "module-two"}
    sig1 = sign_metadata(md1, KEY_ID_1, SEED_1_HEX)
    sig2 = sign_metadata(md2, KEY_ID_2, SEED_2_HEX)
    assert reg.verify(md1, sig1) is True
    assert reg.verify(md2, sig2) is True
    # Cross-verification fails (sig1 uses key_id_1; verifying it for md2 fails)
    assert reg.verify(md2, sig1) is False
    assert reg.verify(md1, sig2) is False


def test_sign_metadata_rejects_wrong_seed_length():
    """sign_metadata raises ValueError if seed is not 32 bytes."""
    md = {"name": "x"}
    with pytest.raises(ValueError, match="32 bytes"):
        sign_metadata(md, KEY_ID_1, "00" * 16)  # 16 bytes
    with pytest.raises(ValueError, match="32 bytes"):
        sign_metadata(md, KEY_ID_1, "00" * 64)  # 64 bytes


def test_sign_metadata_deterministic():
    """Signing the same metadata with the same seed produces the same signature."""
    md = {"name": "x", "rln": True}
    sig_a = sign_metadata(md, KEY_ID_1, SEED_1_HEX)
    sig_b = sign_metadata(md, KEY_ID_1, SEED_1_HEX)
    assert sig_a == sig_b


def test_sign_metadata_independent_of_signature_field():
    """If metadata already has a signature field, sign_metadata ignores it."""
    md_unsigned = {"name": "x", "rln": True}
    md_with_old_sig = {"name": "x", "rln": True, SIGNATURE_FIELD: "ift-stub-key-001:deadbeef"}
    sig_a = sign_metadata(md_unsigned, KEY_ID_1, SEED_1_HEX)
    sig_b = sign_metadata(md_with_old_sig, KEY_ID_1, SEED_1_HEX)
    assert sig_a == sig_b
