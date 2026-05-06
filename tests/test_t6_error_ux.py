"""T6 (PER-7): Tests for better error UX when workspace doesn't exist.

Verifies that all CLI commands taking --path or --workspace fail with
a clear message pointing at RUNBOOK.md Day 1 when the path doesn't
exist or isn't a git repo.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentix_logos.cli import main


def test_workspace_status_missing_path(tmp_path: Path, capsys):
    rc = main(["workspace-status", "--path", str(tmp_path / "nope")])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "workspace_not_found"
    assert "RUNBOOK.md" in out["hint"]


def test_workspace_status_not_git_repo(tmp_path: Path, capsys):
    plain_dir = tmp_path / "not-git"
    plain_dir.mkdir()
    rc = main(["workspace-status", "--path", str(plain_dir)])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "not_a_git_repo"
    assert "RUNBOOK.md" in out["hint"]


def test_modules_list_missing_path(tmp_path: Path, capsys):
    rc = main(["modules", "list", "--path", str(tmp_path / "nope")])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "workspace_not_found"


def test_modules_describe_missing_path(tmp_path: Path, capsys):
    rc = main(["modules", "describe", "foo", "--path", str(tmp_path / "nope")])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "workspace_not_found"


def test_policy_check_missing_path(tmp_path: Path, capsys):
    rc = main(["policy-check", "--path", str(tmp_path / "nope")])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "workspace_not_found"


def test_verify_logoscore_missing_workspace(tmp_path: Path, capsys):
    rc = main([
        "verify-logoscore",
        "--workspace", str(tmp_path / "nope"),
        "--modules", "storage",
        "--call", "storage.healthcheck()",
    ])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "workspace_not_found"


def test_error_message_includes_clone_command(tmp_path: Path, capsys):
    """The hint includes the actual git clone command."""
    rc = main(["workspace-status", "--path", str(tmp_path / "missing")])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert "git clone" in out["hint"]
    assert "logos-workspace" in out["hint"]
