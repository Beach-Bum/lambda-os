"""T5 (PER-6): Tests for agentix-logos audit show <line-number>.

Tests the CLI command with synthetic audit.jsonl files.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentix_logos.cli import main


def _write_audit(tmp_path: Path, events: list[dict]) -> None:
    """Write synthetic audit.jsonl."""
    audit_dir = tmp_path / ".agentix"
    audit_dir.mkdir(exist_ok=True)
    with (audit_dir / "audit.jsonl").open("w") as f:
        for event in events:
            f.write(json.dumps(event) + "\n")


SAMPLE_EVENT = {
    "timestamp": "2026-05-06T08:14:23.451Z",
    "action": "controller_run",
    "goal": "swap chat module",
    "passed": True,
    "modules_touched": ["chat_module", "storage_module"],
    "modules_versions": {"chat_module": [None, "v0.4.0"]},
    "logoscore_calls": [
        {
            "module": "chat_module",
            "method": "healthcheck",
            "args": [],
            "exit_code": 0,
            "stdout_sha256": "abc123",
            "stderr_sha256": "def456",
            "duration_seconds": 1.5,
        }
    ],
    "policy_violations": [],
    "nix_flake_lock_changed": True,
    "lez_program_pin_changed": False,
    "stops_before_apply": True,
    "stops_before_rebuild": True,
    "stops_before_lgs_deploy": True,
    "stops_before_lgs_wallet": True,
}


def test_audit_show_valid_line(tmp_path: Path, capsys):
    _write_audit(tmp_path, [SAMPLE_EVENT])
    rc = main(["audit", "show", "1", "--path", str(tmp_path)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["line"] == 1
    assert out["event"]["modules_touched"] == ["chat_module", "storage_module"]
    assert "modules_touched" in out["logos_extensions"]
    assert out["logos_extensions"]["logoscore_calls"][0]["exit_code"] == 0


def test_audit_show_line_out_of_range(tmp_path: Path, capsys):
    _write_audit(tmp_path, [SAMPLE_EVENT])
    rc = main(["audit", "show", "5", "--path", str(tmp_path)])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "line_out_of_range"
    assert out["total_lines"] == 1


def test_audit_show_missing_file(tmp_path: Path, capsys):
    rc = main(["audit", "show", "1", "--path", str(tmp_path)])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "audit_log_not_found"


def test_audit_show_multiple_lines(tmp_path: Path, capsys):
    event2 = {**SAMPLE_EVENT, "goal": "upgrade storage", "passed": False}
    _write_audit(tmp_path, [SAMPLE_EVENT, event2])
    rc = main(["audit", "show", "2", "--path", str(tmp_path)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["line"] == 2
    assert out["total_lines"] == 2
    assert out["event"]["goal"] == "upgrade storage"


def test_audit_show_strips_none_from_extensions(tmp_path: Path, capsys):
    """Logos extensions with None values are stripped from output."""
    minimal = {"timestamp": "2026-05-06T00:00:00Z", "action": "test"}
    _write_audit(tmp_path, [minimal])
    rc = main(["audit", "show", "1", "--path", str(tmp_path)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    # All logos fields are None in this event, so logos_extensions should be empty
    assert out["logos_extensions"] == {}


def test_audit_show_line_zero_rejected(tmp_path: Path, capsys):
    """Line 0 is out of range (1-indexed)."""
    _write_audit(tmp_path, [SAMPLE_EVENT])
    rc = main(["audit", "show", "0", "--path", str(tmp_path)])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "line_out_of_range"
