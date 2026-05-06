"""Logos module registry parser.

Parses scaffold.toml `[modules.*]` blocks and module metadata.json into
typed records. Read-only.

See docs/BRIDGE-SPEC.md § 'modules.py — Logos module registry parser' for
the full contract.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


@dataclass
class ModuleMetadata:
    name: str
    main: str | dict[str, str] | None = None
    dependencies: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    rln: bool = False  # explicit field; some upstream repos may not declare it yet
    signature: str | None = None  # "<key_id>:<sig_hex>" form; verified via agentix_logos.keys
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to a plain dict for JSON output."""
        return {
            "name": self.name,
            "main": self.main,
            "dependencies": self.dependencies,
            "capabilities": self.capabilities,
            "rln": self.rln,
            "signature": self.signature,
        }

    @classmethod
    def from_json(cls, name: str, raw: dict) -> ModuleMetadata:
        """Create ModuleMetadata from a parsed metadata.json dict.

        Args:
            name: Fallback module name if not present in raw.
            raw: Parsed JSON dict from metadata.json.

        Returns:
            Populated ModuleMetadata instance.
        """
        sig = raw.get("signature")
        if sig is not None and not isinstance(sig, str):
            sig = None
        return cls(
            name=raw.get("name", name),
            main=raw.get("main"),
            dependencies=list(raw.get("dependencies") or []),
            capabilities=list(raw.get("capabilities") or []),
            rln=bool(raw.get("rln", False)),
            signature=sig,
            raw=raw,
        )


@dataclass
class ModuleRef:
    name: str
    flake_ref: str
    role: Literal["project", "dependency"]
    metadata: ModuleMetadata | None = None
    has_rln: bool = False
    main_artifact: str | None = None

    def to_dict(self) -> dict:
        """Serialize to a plain dict for JSON output."""
        d = dataclasses.asdict(self)
        if self.metadata is not None:
            d["metadata"] = self.metadata.to_dict()
        return d

    @property
    def is_messaging_module(self) -> bool:
        """True if metadata declares messaging/chat/delivery capability."""
        if self.metadata is None:
            return False
        return any(
            cap in self.metadata.capabilities
            for cap in ("messaging", "chat", "delivery")
        )


def parse_scaffold_toml(path: Path) -> list[ModuleRef]:
    """Parse ``[modules.*]`` blocks from scaffold.toml.

    Args:
        path: Path to the scaffold.toml file.

    Returns:
        List of ModuleRef. Metadata is not loaded here — call
        ``enrich_with_metadata`` separately.
    """
    if not path.exists():
        return []
    with path.open("rb") as f:
        data = tomllib.load(f)

    modules_block = data.get("modules") or {}
    refs: list[ModuleRef] = []
    for name, body in modules_block.items():
        if not isinstance(body, dict):
            continue
        flake_ref = body.get("flake")
        role = body.get("role", "project")
        if role not in ("project", "dependency"):
            role = "project"
        if not flake_ref:
            continue
        refs.append(
            ModuleRef(
                name=name,
                flake_ref=flake_ref,
                role=role,  # type: ignore[arg-type]
            )
        )
    return refs


def enrich_with_metadata(ref: ModuleRef, source_root: Path) -> ModuleRef:
    """Read metadata.json from a module's source and attach to ref.

    Args:
        ref: ModuleRef to enrich.
        source_root: Directory containing metadata.json (e.g.
            ``<workspace>/repos/<module>/``).

    Returns:
        New ModuleRef with metadata and has_rln populated. Returns
        the original ref unchanged if metadata.json doesn't exist.
    """
    import json

    metadata_path = source_root / "metadata.json"
    if not metadata_path.exists():
        return ref

    raw = json.loads(metadata_path.read_text())
    md = ModuleMetadata.from_json(ref.name, raw)
    return dataclasses.replace(ref, metadata=md, has_rln=md.rln)
