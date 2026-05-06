"""Tests for audit chain verification."""

from __future__ import annotations

import json
from pathlib import Path

from agentix_logos.audit_chain import (
    build_chain,
    compute_chain_root,
    compute_cid,
    save_chain_sidecar,
    verify_chain,
)


def _write_audit(path: Path, events: list[dict]) -> Path:
    audit_dir = path / ".agentix"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_path = audit_dir / "audit.jsonl"
    with audit_path.open("w") as f:
        for e in events:
            f.write(json.dumps(e, sort_keys=True) + "\n")
    return audit_path


class TestComputeCID:
    def test_deterministic(self):
        line = '{"action":"test","timestamp":"2026-01-01"}'
        assert compute_cid(line) == compute_cid(line)

    def test_different_lines_different_cids(self):
        assert compute_cid('{"a":1}') != compute_cid('{"a":2}')

    def test_strips_trailing_newline(self):
        assert compute_cid('{"a":1}\n') == compute_cid('{"a":1}')

    def test_prefix(self):
        assert compute_cid("test").startswith("sha256:")


class TestChainRoot:
    def test_includes_sequence(self):
        cid = "sha256:abc"
        r0 = compute_chain_root(0, cid, None)
        r1 = compute_chain_root(1, cid, None)
        assert r0 != r1  # Same CID at different positions = different root

    def test_genesis_has_no_prev(self):
        root = compute_chain_root(0, "sha256:abc", None)
        assert root.startswith("sha256:")


class TestBuildChain:
    def test_empty_file(self, tmp_path: Path):
        chain = build_chain(tmp_path / "nonexistent")
        assert chain == []

    def test_single_entry(self, tmp_path: Path):
        audit_path = _write_audit(tmp_path, [{"action": "test"}])
        chain = build_chain(audit_path)
        assert len(chain) == 1
        assert chain[0].sequence_number == 0
        assert chain[0].prev_cid is None
        assert chain[0].cid.startswith("sha256:")

    def test_chain_links(self, tmp_path: Path):
        audit_path = _write_audit(tmp_path, [
            {"action": "first"},
            {"action": "second"},
            {"action": "third"},
        ])
        chain = build_chain(audit_path)
        assert len(chain) == 3
        assert chain[0].prev_cid is None
        assert chain[1].prev_cid == chain[0].cid
        assert chain[2].prev_cid == chain[1].cid

    def test_cids_are_reproducible(self, tmp_path: Path):
        audit_path = _write_audit(tmp_path, [{"action": "test"}])
        chain1 = build_chain(audit_path)
        chain2 = build_chain(audit_path)
        assert chain1[0].cid == chain2[0].cid
        assert chain1[0].chain_root == chain2[0].chain_root


class TestVerifyChain:
    def test_valid_chain(self, tmp_path: Path):
        audit_path = _write_audit(tmp_path, [
            {"action": "a"},
            {"action": "b"},
            {"action": "c"},
        ])
        result = verify_chain(audit_path)
        assert result.valid is True
        assert result.entries == 3
        assert result.errors == []

    def test_empty_is_valid(self, tmp_path: Path):
        result = verify_chain(tmp_path / "nope")
        assert result.valid is True
        assert result.entries == 0

    def test_corrupt_line_detected(self, tmp_path: Path):
        audit_dir = tmp_path / ".agentix"
        audit_dir.mkdir(parents=True)
        audit_path = audit_dir / "audit.jsonl"
        audit_path.write_text('{"action":"ok"}\nNOT JSON\n{"action":"ok2"}\n')
        result = verify_chain(audit_path)
        assert result.valid is False
        assert len(result.errors) == 1
        assert "invalid JSON" in result.errors[0]


class TestSaveSidecar:
    def test_creates_file(self, tmp_path: Path):
        audit_path = _write_audit(tmp_path, [{"a": 1}, {"a": 2}])
        chain = build_chain(audit_path)
        sidecar = tmp_path / "chain.jsonl"
        save_chain_sidecar(chain, sidecar)

        assert sidecar.exists()
        lines = sidecar.read_text().strip().splitlines()
        assert len(lines) == 2

        entry = json.loads(lines[0])
        assert "cid" in entry
        assert "chain_root" in entry
        assert entry["sequence_number"] == 0
