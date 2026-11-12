"""Audit chain — tamper-evident hash chain over audit.jsonl.

Phase 3 preview: builds and verifies a hash chain over the local
audit log. Each entry gets a CID (sha256 of the line bytes), a
prev_cid linking to the previous entry, and a chain_root binding
the CID to its sequence position.

This is the local-only version. Phase 3 publishes to Codex and
anchors to LEZ. Phase 4 makes the chain verifiable by anyone.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


def compute_cid(audit_line: str) -> str:
    """Compute content ID for an audit line (without trailing newline)."""
    line = audit_line.rstrip("\n").encode("utf-8")
    return "sha256:" + hashlib.sha256(line).hexdigest()


def compute_chain_root(sequence: int, cid: str, prev_cid: str | None) -> str:
    """Compute chain root binding CID to its position."""
    material = f"{sequence}|{cid}|{prev_cid or 'genesis'}"
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass
class ChainEntry:
    sequence_number: int
    cid: str
    prev_cid: str | None
    chain_root: str
    line: str  # The original audit line

    def to_dict(self) -> dict:
        return {
            "sequence_number": self.sequence_number,
            "cid": self.cid,
            "prev_cid": self.prev_cid,
            "chain_root": self.chain_root,
        }


@dataclass
class ChainVerification:
    valid: bool
    entries: int
    errors: list[str]
    sealed: int = 0    # lines covered by the sidecar and checked against it
    unsealed: int = 0  # lines written since the chain was last sealed

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "entries": self.entries,
            "errors": self.errors,
            "sealed": self.sealed,
            "unsealed": self.unsealed,
        }


SIDECAR_NAME = "audit-chain.jsonl"
MAX_REPORTED_ERRORS = 10


def build_chain(audit_path: Path) -> list[ChainEntry]:
    """Build a hash chain from an audit.jsonl file."""
    if not audit_path.exists():
        return []

    lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
    chain: list[ChainEntry] = []
    prev_cid: str | None = None

    for i, line in enumerate(lines):
        cid = compute_cid(line)
        chain_root = compute_chain_root(i, cid, prev_cid)
        chain.append(ChainEntry(
            sequence_number=i,
            cid=cid,
            prev_cid=prev_cid,
            chain_root=chain_root,
            line=line,
        ))
        prev_cid = cid

    return chain


def load_chain_sidecar(sidecar_path: Path) -> list[dict]:
    """Read the sealed chain. A line that is not JSON is kept as an empty
    record so that it fails verification instead of being skipped."""
    records: list[dict] = []
    for raw in sidecar_path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            record = {}
        records.append(record if isinstance(record, dict) else {})
    return records


def _check_sidecar(records: list[dict]) -> list[str]:
    """The sealed chain must be consistent with itself before it can vouch
    for anything: positions in order, each entry naming the one before it,
    and each chain_root recomputable from its own fields."""
    problems: list[str] = []
    prev_cid: str | None = None
    for i, rec in enumerate(records):
        cid = rec.get("cid")
        if rec.get("sequence_number") != i or not isinstance(cid, str):
            problems.append(f"Chain entry {i}: malformed or out of sequence")
            prev_cid = cid if isinstance(cid, str) else None
            continue
        if rec.get("prev_cid") != prev_cid:
            problems.append(f"Chain entry {i}: does not link to the entry before it")
        if rec.get("chain_root") != compute_chain_root(i, cid, rec.get("prev_cid")):
            problems.append(f"Chain entry {i}: chain_root does not match its own fields")
        prev_cid = cid
    return problems


def verify_chain(audit_path: Path, sidecar_path: Path | None = None) -> ChainVerification:
    """Verify the audit log against its sealed chain.

    `agentix-logos audit chain` seals the log: it writes one record per line
    (the line's CID, the previous CID, a root binding both to a position) to
    the sidecar. This checks the log as it is now against that record.

    Detected, and reported by position:
      - a sealed line whose content changed
      - a sealed line removed, inserted or moved (every line from that point
        stops matching the CID recorded for its position)
      - the log cut short
      - a sidecar edited to agree with a changed log, unless every linked
        field was recomputed too

    Not detected: lines appended after the last seal. They are counted as
    `unsealed`, not treated as errors, because appending is what a log does.

    What this cannot do on its own: someone who can rewrite the log can
    rewrite the sidecar beside it. The check becomes binding once the sidecar
    (or its last chain_root) is held somewhere the writer cannot reach, which
    is what publishing it is for.

    With no sidecar there is nothing to compare against, so the result only
    says whether each line is well-formed, and `sealed` is 0.
    """
    if not audit_path.exists():
        return ChainVerification(valid=True, entries=0, errors=[])

    lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
    errors: list[str] = []

    for i, line in enumerate(lines):
        try:
            json.loads(line)
        except json.JSONDecodeError:
            errors.append(f"Line {i}: invalid JSON")

    if sidecar_path is None:
        sidecar_path = audit_path.parent / SIDECAR_NAME
    if not sidecar_path.exists():
        return ChainVerification(
            valid=not errors, entries=len(lines), errors=errors,
            sealed=0, unsealed=len(lines),
        )

    records = load_chain_sidecar(sidecar_path)
    errors.extend(_check_sidecar(records))

    mismatched = [
        i for i, rec in enumerate(records[: len(lines)])
        if compute_cid(lines[i]) != rec.get("cid")
    ]
    for i in mismatched[:MAX_REPORTED_ERRORS]:
        errors.append(f"Line {i}: content does not match the CID sealed for this position")
    if len(mismatched) > MAX_REPORTED_ERRORS:
        errors.append(
            f"{len(mismatched) - MAX_REPORTED_ERRORS} more lines do not match "
            f"(first divergence at line {mismatched[0]})"
        )

    if len(lines) < len(records):
        errors.append(
            f"Log is shorter than its sealed chain: {len(records) - len(lines)} sealed "
            f"line(s) missing from the end"
        )

    return ChainVerification(
        valid=not errors,
        entries=len(lines),
        errors=errors,
        sealed=min(len(lines), len(records)),
        unsealed=max(0, len(lines) - len(records)),
    )


def save_chain_sidecar(chain: list[ChainEntry], sidecar_path: Path) -> None:
    """Write the chain sidecar file (audit-chain.jsonl).

    This is the Phase 3 artifact that would be published to Codex.
    """
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    with sidecar_path.open("w", encoding="utf-8") as f:
        for entry in chain:
            f.write(json.dumps(entry.to_dict(), sort_keys=True) + "\n")
