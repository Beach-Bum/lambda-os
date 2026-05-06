"""Tests for agentix_logos.lez and lez_programs_pinned policy (P2 / PER-18)."""

from __future__ import annotations

import hashlib
import json
import textwrap
from pathlib import Path

import pytest

from agentix_logos.lez import (
    PROGRAM_ID_PREFIX,
    ProgramRef,
    compute_program_id_from_source,
    parse_lez_programs_from_scaffold,
)
from agentix_logos.policy import check_logos_policy

# ─────────────────────────────────────────────────────────────────────
# compute_program_id_from_source
# ─────────────────────────────────────────────────────────────────────


def test_program_id_from_file_matches_sha256(tmp_path: Path):
    """Hashing a file yields sha256:<hex of file bytes>."""
    f = tmp_path / "program.bin"
    payload = b"\x7fELF" + b"junk-bytes-for-test" * 100
    f.write_bytes(payload)
    pid = compute_program_id_from_source(f)
    assert pid.startswith(PROGRAM_ID_PREFIX)
    expected = PROGRAM_ID_PREFIX + hashlib.sha256(payload).hexdigest()
    assert pid == expected


def test_program_id_from_directory_is_deterministic(tmp_path: Path):
    """Hashing the same directory twice produces the same ID."""
    d = tmp_path / "program-dir"
    (d / "src").mkdir(parents=True)
    (d / "src" / "main.rs").write_bytes(b"fn main() {}\n")
    (d / "Cargo.toml").write_bytes(b"[package]\nname = 'p'\n")
    pid_a = compute_program_id_from_source(d)
    pid_b = compute_program_id_from_source(d)
    assert pid_a == pid_b
    assert pid_a.startswith(PROGRAM_ID_PREFIX)


def test_program_id_changes_when_source_changes(tmp_path: Path):
    """Mutating any byte in a source dir changes the program ID."""
    d = tmp_path / "program-dir"
    d.mkdir()
    (d / "main.rs").write_bytes(b"fn main() { println!(\"v1\"); }\n")
    pid_v1 = compute_program_id_from_source(d)
    (d / "main.rs").write_bytes(b"fn main() { println!(\"v2\"); }\n")
    pid_v2 = compute_program_id_from_source(d)
    assert pid_v1 != pid_v2


def test_program_id_changes_when_file_added(tmp_path: Path):
    """Adding a new file to a source dir changes the program ID."""
    d = tmp_path / "program-dir"
    d.mkdir()
    (d / "main.rs").write_bytes(b"fn main() {}\n")
    pid_before = compute_program_id_from_source(d)
    (d / "extra.rs").write_bytes(b"// extra\n")
    pid_after = compute_program_id_from_source(d)
    assert pid_before != pid_after


def test_program_id_raises_on_missing_path(tmp_path: Path):
    """Non-existent path raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        compute_program_id_from_source(tmp_path / "no-such-thing")


# ─────────────────────────────────────────────────────────────────────
# parse_lez_programs_from_scaffold
# ─────────────────────────────────────────────────────────────────────


def test_parse_scaffold_empty_when_no_lez_section(tmp_path: Path):
    """scaffold.toml without [lez] returns an empty dict."""
    scaffold = tmp_path / "scaffold.toml"
    scaffold.write_text(
        textwrap.dedent(
            """
            [modules.foo]
            flake = "github:logos-co/foo"
            """
        )
    )
    assert parse_lez_programs_from_scaffold(scaffold) == {}


def test_parse_scaffold_returns_program_refs(tmp_path: Path):
    """[lez.programs.*] blocks parse into ProgramRef instances."""
    scaffold = tmp_path / "scaffold.toml"
    scaffold.write_text(
        textwrap.dedent(
            """
            [lez.programs.alpha]
            source = "programs/alpha"
            program_id = "sha256:abc123"
            entry_point = "release/alpha.bin"

            [lez.programs.beta]
            source = "programs/beta"
            program_id = "sha256:def456"
            """
        )
    )
    refs = parse_lez_programs_from_scaffold(scaffold)
    assert set(refs) == {"alpha", "beta"}
    assert refs["alpha"].source == "programs/alpha"
    assert refs["alpha"].program_id == "sha256:abc123"
    assert refs["alpha"].entry_point == "release/alpha.bin"
    assert refs["beta"].entry_point is None  # absent → None


def test_parse_scaffold_rejects_non_string_source(tmp_path: Path):
    """Non-string source raises ValueError."""
    scaffold = tmp_path / "scaffold.toml"
    scaffold.write_text(
        textwrap.dedent(
            """
            [lez.programs.alpha]
            source = 42
            program_id = "sha256:abc"
            """
        )
    )
    with pytest.raises(ValueError, match="source must be a string"):
        parse_lez_programs_from_scaffold(scaffold)


def test_program_ref_resolved_paths(tmp_path: Path):
    """ProgramRef resolves source + entry_point relative to the workspace."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    ref = ProgramRef(
        name="alpha",
        source="programs/alpha",
        program_id="sha256:x",
        entry_point="release/alpha.bin",
    )
    assert ref.resolved_source(workspace) == workspace / "programs" / "alpha"
    assert (
        ref.resolved_entry_point(workspace)
        == workspace / "programs" / "alpha" / "release" / "alpha.bin"
    )

    # Unset entry_point → None
    ref_no_ep = ProgramRef(name="beta", source="programs/beta", program_id="sha256:y")
    assert ref_no_ep.resolved_entry_point(workspace) is None


# ─────────────────────────────────────────────────────────────────────
# Policy enforcement: lez_programs_pinned
# ─────────────────────────────────────────────────────────────────────


def _make_workspace_with_lez(
    tmp_path: Path,
    *,
    program_files: dict[str, bytes] | None = None,
    captured_pin: str | None = None,
    entry_point: str | None = None,
    enable_rule: bool = True,
) -> Path:
    """Build a minimal workspace with one LEZ program for policy tests.

    Args:
        tmp_path: pytest tmp dir.
        program_files: Map of relative path inside programs/alpha → bytes.
        captured_pin: program_id pin written into scaffold.toml. None
            omits the field (triggers missing-pin violation).
        entry_point: scaffold.toml entry_point value, if any.
        enable_rule: Whether lez_programs_pinned is on in policy.json.

    Returns:
        Path to the workspace root.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".git").mkdir()
    (ws / ".agentix").mkdir()

    # Build the program source tree
    src = ws / "programs" / "alpha"
    src.mkdir(parents=True)
    if program_files is not None:
        for rel, body in program_files.items():
            target = src / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)

    # scaffold.toml
    lines = ["[lez.programs.alpha]", 'source = "programs/alpha"']
    if captured_pin is not None:
        lines.append(f'program_id = "{captured_pin}"')
    if entry_point is not None:
        lines.append(f'entry_point = "{entry_point}"')
    (ws / "scaffold.toml").write_text("\n".join(lines) + "\n")

    # policy.json
    policy = {
        "logos": {
            "require_metadata_json": False,
            "require_signed_metadata": False,
            "require_rln_for_messaging_modules": False,
            "lez_programs_pinned": enable_rule,
        }
    }
    (ws / ".agentix" / "policy.json").write_text(json.dumps(policy))
    return ws


def test_clean_proposal_no_drift(tmp_path: Path):
    """Captured pin matches actual hash → no violation."""
    files = {"main.rs": b"fn main() {}\n"}
    # Pre-compute the expected pin so it matches what the rule will recompute.
    src_dir = tmp_path / "compute-helper" / "programs" / "alpha"
    src_dir.mkdir(parents=True)
    for rel, body in files.items():
        (src_dir / rel).write_bytes(body)
    expected_pin = compute_program_id_from_source(src_dir)

    ws = _make_workspace_with_lez(tmp_path, program_files=files, captured_pin=expected_pin)
    violations = check_logos_policy(ws, proposal_diff="", modules_touched=[])
    lez_violations = [v for v in violations if v.rule == "lez_programs_pinned"]
    assert lez_violations == [], lez_violations


def test_drifted_pin_violation(tmp_path: Path):
    """Source differs from captured pin → deny violation."""
    files = {"main.rs": b"fn main() { println!(\"v2\"); }\n"}
    # Captured pin from an OLD version of the source.
    captured_pin = "sha256:" + "0" * 64
    ws = _make_workspace_with_lez(tmp_path, program_files=files, captured_pin=captured_pin)
    violations = check_logos_policy(ws, proposal_diff="", modules_touched=[])
    lez_violations = [v for v in violations if v.rule == "lez_programs_pinned"]
    assert len(lez_violations) == 1
    assert lez_violations[0].severity == "deny"
    assert "drift" in lez_violations[0].details
    assert captured_pin in lez_violations[0].details


def test_missing_pin_violation(tmp_path: Path):
    """A LEZ program without a program_id pin → deny violation."""
    ws = _make_workspace_with_lez(
        tmp_path,
        program_files={"main.rs": b"fn main() {}\n"},
        captured_pin=None,  # omitted!
    )
    violations = check_logos_policy(ws, proposal_diff="", modules_touched=[])
    lez_violations = [v for v in violations if v.rule == "lez_programs_pinned"]
    assert len(lez_violations) == 1
    assert "no program_id pin" in lez_violations[0].details


def test_missing_source_path_violation(tmp_path: Path):
    """If scaffold.toml's source path doesn't exist → deny violation."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".git").mkdir()
    (ws / ".agentix").mkdir()
    (ws / "scaffold.toml").write_text(
        textwrap.dedent(
            """
            [lez.programs.alpha]
            source = "programs/does-not-exist"
            program_id = "sha256:abc"
            """
        )
    )
    (ws / ".agentix" / "policy.json").write_text(
        json.dumps({"logos": {"require_metadata_json": False, "lez_programs_pinned": True}})
    )
    violations = check_logos_policy(ws, proposal_diff="", modules_touched=[])
    lez_violations = [v for v in violations if v.rule == "lez_programs_pinned"]
    assert len(lez_violations) == 1
    assert "not found" in lez_violations[0].details


def test_entry_point_used_when_set(tmp_path: Path):
    """When entry_point is set, the rule hashes that file (not the whole dir)."""
    # Build src dir with a binary + extra files. The rule should hash JUST
    # the entry_point binary. So changing extra files shouldn't drift the pin.
    src_dir = tmp_path / "compute-helper" / "programs" / "alpha"
    (src_dir / "release").mkdir(parents=True)
    (src_dir / "release" / "alpha.bin").write_bytes(b"\x7fELF compiled binary")
    (src_dir / "src").mkdir()
    (src_dir / "src" / "main.rs").write_bytes(b"fn main() {}\n")  # extra
    expected_pin = compute_program_id_from_source(src_dir / "release" / "alpha.bin")

    ws = _make_workspace_with_lez(
        tmp_path,
        program_files={
            "release/alpha.bin": b"\x7fELF compiled binary",
            "src/main.rs": b"fn main() {}\n",
        },
        captured_pin=expected_pin,
        entry_point="release/alpha.bin",
    )
    violations = check_logos_policy(ws, proposal_diff="", modules_touched=[])
    lez_violations = [v for v in violations if v.rule == "lez_programs_pinned"]
    assert lez_violations == [], lez_violations

    # Now mutate the source main.rs (NOT the entry_point binary). Rule should
    # still pass since entry_point bytes haven't changed.
    (ws / "programs" / "alpha" / "src" / "main.rs").write_bytes(b"fn main() { /* edit */ }\n")
    violations = check_logos_policy(ws, proposal_diff="", modules_touched=[])
    lez_violations = [v for v in violations if v.rule == "lez_programs_pinned"]
    assert lez_violations == [], "entry_point hash unchanged → no drift"

    # But mutating the entry_point itself should drift.
    (ws / "programs" / "alpha" / "release" / "alpha.bin").write_bytes(b"different bytes")
    violations = check_logos_policy(ws, proposal_diff="", modules_touched=[])
    lez_violations = [v for v in violations if v.rule == "lez_programs_pinned"]
    assert len(lez_violations) == 1
    assert "drift" in lez_violations[0].details


def test_rule_disabled_no_violations(tmp_path: Path):
    """When lez_programs_pinned is False, drifted pin produces no violation."""
    ws = _make_workspace_with_lez(
        tmp_path,
        program_files={"main.rs": b"fn main() {}\n"},
        captured_pin="sha256:" + "0" * 64,  # wrong pin
        enable_rule=False,
    )
    violations = check_logos_policy(ws, proposal_diff="", modules_touched=[])
    lez_violations = [v for v in violations if v.rule == "lez_programs_pinned"]
    assert lez_violations == []


def test_no_lez_block_no_violations(tmp_path: Path):
    """A workspace without [lez.programs] is silently skipped."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".git").mkdir()
    (ws / ".agentix").mkdir()
    (ws / "scaffold.toml").write_text("[modules.foo]\nflake = 'x'\n")
    (ws / ".agentix" / "policy.json").write_text(
        json.dumps({"logos": {"require_metadata_json": False, "lez_programs_pinned": True}})
    )
    violations = check_logos_policy(ws, proposal_diff="", modules_touched=[])
    lez_violations = [v for v in violations if v.rule == "lez_programs_pinned"]
    assert lez_violations == []


def test_malformed_scaffold_lez_violation(tmp_path: Path):
    """A malformed [lez.programs] block produces one deny violation."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".git").mkdir()
    (ws / ".agentix").mkdir()
    (ws / "scaffold.toml").write_text(
        textwrap.dedent(
            """
            [lez.programs.alpha]
            source = 42
            program_id = "sha256:abc"
            """
        )
    )
    (ws / ".agentix" / "policy.json").write_text(
        json.dumps({"logos": {"require_metadata_json": False, "lez_programs_pinned": True}})
    )
    violations = check_logos_policy(ws, proposal_diff="", modules_touched=[])
    lez_violations = [v for v in violations if v.rule == "lez_programs_pinned"]
    assert len(lez_violations) == 1
    assert "malformed" in lez_violations[0].details
