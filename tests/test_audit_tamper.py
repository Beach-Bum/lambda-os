"""The audit log is called tamper-evident. These tests tamper with it.

Each one seals a log, changes it the way someone covering their tracks
would, and requires verification to fail and to say where.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentix_logos.audit_chain import (
    SIDECAR_NAME,
    build_chain,
    save_chain_sidecar,
    verify_chain,
)

EVENTS = [
    {"action": "snapshot", "modules": 55},
    {"action": "verify", "module": "codex", "passed": True},
    {"action": "verify", "module": "waku", "passed": False},
    {"action": "auto_rollback", "module": "waku", "to": "a1b2c3"},
    {"action": "propose", "module": "waku", "approved_by": "operator"},
    {"action": "apply", "module": "waku"},
]


@pytest.fixture
def sealed(tmp_path: Path) -> Path:
    """A six-line audit log with its chain sealed beside it."""
    audit_dir = tmp_path / ".agentix"
    audit_dir.mkdir()
    audit_path = audit_dir / "audit.jsonl"
    audit_path.write_text(
        "".join(json.dumps(e, sort_keys=True) + "\n" for e in EVENTS), encoding="utf-8"
    )
    save_chain_sidecar(build_chain(audit_path), audit_dir / SIDECAR_NAME)
    return audit_path


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _rewrite(path: Path, lines: list[str]) -> None:
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")


def test_an_untouched_log_verifies(sealed: Path) -> None:
    result = verify_chain(sealed)
    assert result.valid is True
    assert result.errors == []
    assert (result.entries, result.sealed, result.unsealed) == (6, 6, 0)


def test_rewriting_a_past_event_is_detected(sealed: Path) -> None:
    # the failed check becomes a pass; still valid JSON, same length
    lines = _lines(sealed)
    lines[2] = lines[2].replace("false", "true ")
    _rewrite(sealed, lines)

    result = verify_chain(sealed)
    assert result.valid is False
    assert any(e.startswith("Line 2:") for e in result.errors)
    assert not any(e.startswith(("Line 0:", "Line 1:", "Line 3:")) for e in result.errors)


@pytest.mark.parametrize("index", range(len(EVENTS)))
def test_a_single_changed_character_on_any_line_is_detected(sealed: Path, index: int) -> None:
    lines = _lines(sealed)
    lines[index] = lines[index].replace("a", "b", 1)
    _rewrite(sealed, lines)

    result = verify_chain(sealed)
    assert result.valid is False
    assert any(e.startswith(f"Line {index}:") for e in result.errors)


def test_deleting_an_event_is_detected(sealed: Path) -> None:
    lines = _lines(sealed)
    del lines[3]  # the rollback never happened
    _rewrite(sealed, lines)

    result = verify_chain(sealed)
    assert result.valid is False
    assert any(e.startswith("Line 3:") for e in result.errors)       # first divergence
    assert any("shorter than its sealed chain" in e for e in result.errors)


def test_inserting_an_event_is_detected(sealed: Path) -> None:
    lines = _lines(sealed)
    lines.insert(4, json.dumps({"action": "propose", "approved_by": "nobody"}, sort_keys=True))
    _rewrite(sealed, lines)

    result = verify_chain(sealed)
    assert result.valid is False
    assert any(e.startswith("Line 4:") for e in result.errors)


def test_reordering_events_is_detected(sealed: Path) -> None:
    lines = _lines(sealed)
    lines[4], lines[5] = lines[5], lines[4]  # applied before it was approved
    _rewrite(sealed, lines)

    result = verify_chain(sealed)
    assert result.valid is False
    assert any(e.startswith("Line 4:") for e in result.errors)
    assert any(e.startswith("Line 5:") for e in result.errors)


def test_cutting_the_log_short_is_detected(sealed: Path) -> None:
    _rewrite(sealed, _lines(sealed)[:4])

    result = verify_chain(sealed)
    assert result.valid is False
    assert any("2 sealed line(s) missing" in e for e in result.errors)


def test_appending_is_what_a_log_does_and_is_reported_not_refused(sealed: Path) -> None:
    with sealed.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"action": "verify", "module": "codex"}, sort_keys=True) + "\n")

    result = verify_chain(sealed)
    assert result.valid is True
    assert (result.sealed, result.unsealed) == (6, 1)


def test_editing_the_sidecar_to_match_a_changed_line_is_detected(sealed: Path) -> None:
    # the careful attacker: change the event, then fix up its recorded CID
    from agentix_logos.audit_chain import compute_cid

    lines = _lines(sealed)
    lines[2] = lines[2].replace("false", "true ")
    _rewrite(sealed, lines)

    sidecar = sealed.parent / SIDECAR_NAME
    records = [json.loads(r) for r in sidecar.read_text(encoding="utf-8").splitlines()]
    records[2]["cid"] = compute_cid(lines[2])
    sidecar.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in records), encoding="utf-8")

    result = verify_chain(sealed)
    assert result.valid is False
    # entry 2's root no longer matches its fields, and entry 3 no longer links to it
    assert any(e.startswith("Chain entry 2:") for e in result.errors)
    assert any(e.startswith("Chain entry 3:") for e in result.errors)


def test_a_garbled_sidecar_line_fails_instead_of_being_skipped(sealed: Path) -> None:
    sidecar = sealed.parent / SIDECAR_NAME
    records = sidecar.read_text(encoding="utf-8").splitlines()
    records[1] = "not json"
    sidecar.write_text("".join(r + "\n" for r in records), encoding="utf-8")

    result = verify_chain(sealed)
    assert result.valid is False
    assert any(e.startswith("Chain entry 1:") for e in result.errors)


def test_without_a_sidecar_nothing_is_claimed(tmp_path: Path) -> None:
    audit_dir = tmp_path / ".agentix"
    audit_dir.mkdir()
    audit_path = audit_dir / "audit.jsonl"
    audit_path.write_text(json.dumps({"action": "a"}) + "\n", encoding="utf-8")

    result = verify_chain(audit_path)
    assert result.valid is True
    assert (result.sealed, result.unsealed) == (0, 1)
