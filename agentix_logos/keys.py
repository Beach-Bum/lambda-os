"""IFT key registry + ed25519 signature verification for module metadata.

A module's ``metadata.json`` may include a ``signature`` field of the form
``"<key_id>:<sig_hex>"``. The signature is computed over a canonical JSON
serialisation of the metadata dict **excluding** the signature field
itself (sort_keys=True, no whitespace, UTF-8 encoded).

The registry is a JSON file mapping ``key_id`` to public ed25519 keys.
Phase 2 ships a stub registry; Phase 3+ replaces with the real IFT key
registry once Logos publishes one.

See ``docs/POLICY-SCHEMA.md`` § ``require_signed_metadata`` for the rule
that consumes this module.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

# Field name on metadata dicts holding the signature string.
SIGNATURE_FIELD = "signature"


@dataclass
class RegistryKey:
    """A single ed25519 public key entry in the registry.

    Attributes:
        key_id: Stable identifier referenced from metadata signatures.
        public_key_hex: 32-byte ed25519 public key as lowercase hex.
        label: Human-readable label.
        created: ISO-8601 date string of when the key was added.
    """

    key_id: str
    public_key_hex: str
    label: str = ""
    created: str = ""

    def verify_key(self) -> VerifyKey:
        """Return the libsodium VerifyKey for this entry.

        Returns:
            VerifyKey constructed from the hex-decoded public key.
        """
        return VerifyKey(bytes.fromhex(self.public_key_hex))


@dataclass
class KeyRegistry:
    """Registry of ed25519 public keys for verifying metadata signatures.

    Attributes:
        version: Schema version of the registry file (currently 1).
        keys: Map of key_id → RegistryKey.
        source_path: Path the registry was loaded from, if any.
    """

    version: int = 1
    keys: dict[str, RegistryKey] = field(default_factory=dict)
    source_path: Path | None = None

    @classmethod
    def load(cls, path: Path) -> KeyRegistry:
        """Load a key registry from a JSON file.

        Args:
            path: Path to the registry JSON file. Expected shape:
                ``{"version": 1, "keys": [{"key_id": "...",
                "public_key_hex": "...", "label": "...",
                "created": "..."}, ...]}``.

        Returns:
            Populated KeyRegistry with ``source_path`` set.

        Raises:
            FileNotFoundError: If ``path`` does not exist.
            ValueError: If the file is malformed (bad JSON, wrong version
                type, malformed public_key_hex).
        """
        if not path.exists():
            raise FileNotFoundError(f"key registry not found: {path}")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"key registry is not valid JSON: {exc}") from exc

        version = raw.get("version", 1)
        if not isinstance(version, int):
            raise ValueError(
                f"registry version must be int, got {type(version).__name__}"
            )

        keys: dict[str, RegistryKey] = {}
        for entry in raw.get("keys", []):
            if not isinstance(entry, dict):
                continue
            key_id = entry.get("key_id")
            pub = entry.get("public_key_hex")
            if not (isinstance(key_id, str) and isinstance(pub, str)):
                continue
            try:
                bytes.fromhex(pub)
            except ValueError as exc:
                raise ValueError(
                    f"invalid public_key_hex for key_id {key_id!r}: {exc}"
                ) from exc
            keys[key_id] = RegistryKey(
                key_id=key_id,
                public_key_hex=pub,
                label=entry.get("label", ""),
                created=entry.get("created", ""),
            )

        return cls(version=version, keys=keys, source_path=path)

    def has_key(self, key_id: str) -> bool:
        """Check whether ``key_id`` is present in the registry.

        Args:
            key_id: Identifier to look up.

        Returns:
            True if present.
        """
        return key_id in self.keys

    def verify(self, metadata: dict, signature: str) -> bool:
        """Verify a metadata signature against the registry.

        The signature is parsed as ``"<key_id>:<sig_hex>"``. The signed
        bytes are the canonical JSON of ``metadata`` *excluding* the
        signature field — see :func:`canonical_signing_bytes`.

        Args:
            metadata: Parsed metadata.json dict. The signature field is
                stripped before computing canonical bytes (caller may pass
                the full dict including the signature).
            signature: Signature string of the form
                ``"<key_id>:<sig_hex>"`` — typically
                ``metadata[SIGNATURE_FIELD]``.

        Returns:
            True iff the signature parses, ``key_id`` is in the registry,
            and the ed25519 signature verifies. False on any failure.
            Never raises on bad input — callers treat False as
            "not validly signed".
        """
        if not isinstance(signature, str) or ":" not in signature:
            return False
        key_id, sig_hex = signature.split(":", 1)
        if key_id not in self.keys:
            return False
        try:
            sig_bytes = bytes.fromhex(sig_hex)
        except ValueError:
            return False
        canonical = canonical_signing_bytes(metadata)
        try:
            self.keys[key_id].verify_key().verify(canonical, sig_bytes)
        except BadSignatureError:
            return False
        except Exception:
            # Defensive: any libsodium-side failure is treated as
            # "not verified". Never let an unexpected exception leak.
            return False
        return True


def canonical_signing_bytes(metadata: dict) -> bytes:
    """Compute the canonical bytes that get signed for a metadata dict.

    The signature is over a ``sort_keys=True``, separators-tight JSON
    serialisation of ``metadata`` *with the signature field excluded*,
    UTF-8 encoded.

    Args:
        metadata: The metadata dict. The signature field is removed even
            if present, so signing and verification produce identical
            bytes regardless of whether the dict was supplied with or
            without it.

    Returns:
        UTF-8 encoded canonical JSON bytes ready to feed into ed25519.
    """
    payload = {k: v for k, v in metadata.items() if k != SIGNATURE_FIELD}
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_metadata(metadata: dict, key_id: str, secret_seed_hex: str) -> str:
    """Produce a signature string for a metadata dict.

    Used by tests and by tooling that produces signed metadata. **Not**
    used in the verification path — production verifiers only call
    :meth:`KeyRegistry.verify`.

    Args:
        metadata: Metadata dict to sign. The signature field is excluded
            from the canonical bytes (so it doesn't matter whether the
            dict already has a signature field; this function ignores it).
        key_id: Key identifier to embed in the returned signature string.
            The corresponding public key must be registered in the
            verifier's :class:`KeyRegistry` for verification to succeed.
        secret_seed_hex: 32-byte ed25519 seed as hex (NOT the 64-byte
            expanded form). The signing key is derived deterministically
            from the seed.

    Returns:
        ``"<key_id>:<sig_hex>"`` — assignable to ``metadata["signature"]``.

    Raises:
        ValueError: If ``secret_seed_hex`` is not 32 bytes when decoded.
    """
    seed = bytes.fromhex(secret_seed_hex)
    if len(seed) != 32:
        raise ValueError(f"ed25519 seed must be 32 bytes, got {len(seed)}")
    signing_key = SigningKey(seed)
    canonical = canonical_signing_bytes(metadata)
    sig = signing_key.sign(canonical).signature
    return f"{key_id}:{sig.hex()}"
