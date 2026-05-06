"""LEZ (Logos Execution Zone) program reference + integrity helpers.

A workspace may declare LEZ programs in ``scaffold.toml`` under
``[lez.programs.<name>]`` blocks. Each program has a ``source`` directory
on disk (where the program's Rust/SP1 sources live), an optional
``entry_point`` pointing at the compiled artifact within ``source``, and
a captured ``program_id`` pin of the form ``"sha256:<hex>"``.

This module provides:

- :class:`ProgramRef` — typed record of a captured LEZ program entry.
- :func:`compute_program_id_from_source` — deterministic hash of a
  program's compiled artifact (or, when no compiled binary is supplied,
  the recursive contents of its source directory).
- :func:`parse_lez_programs_from_scaffold` — parser for the
  ``[lez.programs.*]`` blocks in ``scaffold.toml``.

The :func:`check_logos_policy` ``lez_programs_pinned`` rule consumes
these helpers to detect drift between captured pin and recomputed hash —
flagging proposals that ship a program whose source has been mutated
without updating the pin.

See ``docs/POLICY-SCHEMA.md`` § ``lez_programs_pinned`` for the rule.
"""

from __future__ import annotations

import dataclasses
import hashlib
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


# Hash algorithm prefix used in program IDs. Matches docs/CODEX-AUDIT-ANCHOR.md
# convention so a future migration to a different hash algo is mechanical.
PROGRAM_ID_PREFIX = "sha256:"


@dataclass
class ProgramRef:
    """A captured LEZ program entry from ``scaffold.toml``.

    Attributes:
        name: The block key (``[lez.programs.<name>]``).
        source: Path to the program's source directory, relative to the
            workspace root or absolute (caller resolves).
        program_id: The captured pin of the form ``"sha256:<hex>"``, or
            ``None`` if the block omits ``program_id`` (which is a
            policy violation under ``lez_programs_pinned``).
        entry_point: Optional relative path within ``source`` pointing
            at the compiled binary. If unset, the whole source tree is
            hashed.
        raw: The raw TOML dict for the block, retained for diagnostics
            and forward-compat.
    """

    name: str
    source: str
    program_id: str | None = None
    entry_point: str | None = None
    raw: dict = dataclasses.field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to a plain dict for JSON output."""
        return {
            "name": self.name,
            "source": self.source,
            "program_id": self.program_id,
            "entry_point": self.entry_point,
        }

    def resolved_source(self, workspace: Path) -> Path:
        """Resolve ``source`` against ``workspace`` if it's relative."""
        p = Path(self.source).expanduser()
        if not p.is_absolute():
            p = (workspace / p).resolve()
        return p

    def resolved_entry_point(self, workspace: Path) -> Path | None:
        """Return the absolute entry_point path, if set.

        Args:
            workspace: Workspace root used to resolve relative ``source``.

        Returns:
            Absolute path to the compiled binary, or None if entry_point
            is unset.
        """
        if not self.entry_point:
            return None
        return (self.resolved_source(workspace) / self.entry_point).resolve()


def compute_program_id_from_source(program_path: Path) -> str:
    """Compute the deterministic program ID for a LEZ program.

    Resolution:
        - If ``program_path`` is a regular file → hash its bytes directly.
        - If it's a directory → walk the tree in deterministic (sorted)
          order and hash each file's relative path + bytes. This gives a
          stable hash even when the filesystem returns entries in a
          non-deterministic order across systems.

    Both modes return ``"sha256:<hex>"``.

    Args:
        program_path: Path to the compiled binary or to the source root.
            Symlinks are resolved before hashing.

    Returns:
        Program ID of the form ``"sha256:<lowercase-hex>"``.

    Raises:
        FileNotFoundError: If ``program_path`` doesn't exist.
        IsADirectoryError: Never — directories are handled by walking.
        OSError: Propagated if a file is unreadable.
    """
    p = Path(program_path).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"program path does not exist: {p}")
    p = p.resolve()
    h = hashlib.sha256()
    if p.is_file():
        h.update(p.read_bytes())
        return PROGRAM_ID_PREFIX + h.hexdigest()
    # Directory: walk in deterministic order. Each entry contributes its
    # repo-relative path (so renames count as drift) plus its bytes.
    for child in sorted(p.rglob("*")):
        if not child.is_file():
            continue
        rel = child.relative_to(p).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\x00")  # separator so paths don't run together
        h.update(child.read_bytes())
        h.update(b"\xff")  # record terminator
    return PROGRAM_ID_PREFIX + h.hexdigest()


def parse_lez_programs_from_scaffold(scaffold_path: Path) -> dict[str, ProgramRef]:
    """Parse ``[lez.programs.*]`` blocks from a ``scaffold.toml`` file.

    Args:
        scaffold_path: Path to scaffold.toml.

    Returns:
        Map of program name → :class:`ProgramRef`. Empty dict if the
        file doesn't exist, doesn't have an ``[lez]`` section, or has no
        ``[lez.programs]`` entries.

    Raises:
        ValueError: If the TOML parses but the ``[lez.programs]`` block
            is structurally invalid (e.g. a ``source`` field that isn't
            a string).
    """
    if not scaffold_path.exists():
        return {}
    with scaffold_path.open("rb") as f:
        data = tomllib.load(f)

    lez_block = data.get("lez") or {}
    programs_block = lez_block.get("programs") or {}
    refs: dict[str, ProgramRef] = {}
    for name, body in programs_block.items():
        if not isinstance(body, dict):
            continue
        source = body.get("source")
        if not isinstance(source, str):
            raise ValueError(
                f"[lez.programs.{name}].source must be a string; "
                f"got {type(source).__name__}"
            )
        program_id = body.get("program_id")
        if program_id is not None and not isinstance(program_id, str):
            raise ValueError(
                f"[lez.programs.{name}].program_id must be a string or absent; "
                f"got {type(program_id).__name__}"
            )
        entry_point = body.get("entry_point")
        if entry_point is not None and not isinstance(entry_point, str):
            raise ValueError(
                f"[lez.programs.{name}].entry_point must be a string or absent; "
                f"got {type(entry_point).__name__}"
            )
        refs[name] = ProgramRef(
            name=name,
            source=source,
            program_id=program_id,
            entry_point=entry_point,
            raw=body,
        )
    return refs
