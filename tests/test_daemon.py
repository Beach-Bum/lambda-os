"""Tests for the Agentix daemon."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from agentix_logos.daemon import AgentixDaemon


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    env = {**subprocess.os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
    subprocess.run(["git", "init"], cwd=path, capture_output=True, env=env)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, capture_output=True, env=env)
    subprocess.run(["git", "config", "user.name", "T"], cwd=path, capture_output=True, env=env)
    (path / "README.md").write_text("# test\n")
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True, env=env)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, capture_output=True, env=env)
    return path


class TestDaemonInit:
    def test_creates_with_defaults(self, tmp_path: Path):
        ws = _init_repo(tmp_path / "ws")
        daemon = AgentixDaemon(ws, check_interval=10)
        assert daemon.workspace_path == ws
        assert daemon.check_interval == 10
        assert daemon.cycle_count == 0
        assert daemon.running is True

    def test_loads_existing_proposals(self, tmp_path: Path):
        ws = _init_repo(tmp_path / "ws")
        proposals_dir = ws / ".agentix" / "proposals"
        proposals_dir.mkdir(parents=True)

        # Write a fake proposal manifest
        manifest = {
            "id": "test-abc-123",
            "module": "test",
            "current_sha": "aaa",
            "proposed_sha": "bbb",
            "state": "pending",
            "created_at": "t",
        }
        (proposals_dir / "test-abc-123.json").write_text(json.dumps(manifest))
        (proposals_dir / "test-abc-123.patch").write_text("diff")

        daemon = AgentixDaemon(ws)
        assert "test:bbb" in daemon.proposed_upgrades


class TestRunCycle:
    def test_cycle_produces_report(self, tmp_path: Path):
        ws = _init_repo(tmp_path / "ws")
        # Create a policy
        agentix_dir = ws / ".agentix"
        agentix_dir.mkdir()
        (agentix_dir / "policy.json").write_text(json.dumps({
            "denied": [], "allowed": [], "logos": {}
        }))

        daemon = AgentixDaemon(ws, check_interval=10)
        daemon.modules_dir = tmp_path / "empty_modules"
        daemon.modules_dir.mkdir()

        report = daemon._run_cycle()
        assert "timestamp" in report
        assert "checks" in report
        assert "snapshot" in report["checks"]
        assert report["checks"]["snapshot"]["submodules"] >= 0
        assert "modules" in report["checks"]
        assert "policy" in report["checks"]
        assert report["checks"]["policy"]["loaded"] is True

    def test_cycle_detects_no_upgrades_in_empty_repo(self, tmp_path: Path):
        ws = _init_repo(tmp_path / "ws")
        daemon = AgentixDaemon(ws)
        daemon.last_snapshot = daemon.ws.extended_source_snapshot()

        upgrades = daemon._detect_upgrades()
        assert upgrades == []  # No submodules = no upgrades

    def test_writes_status_file(self, tmp_path: Path):
        ws = _init_repo(tmp_path / "ws")
        daemon = AgentixDaemon(ws)
        daemon.modules_dir = tmp_path / "empty"
        daemon.modules_dir.mkdir()

        report = daemon._run_cycle()
        daemon._write_status(report)

        status_file = ws / ".agentix" / "node-status.json"
        assert status_file.exists()
        data = json.loads(status_file.read_text())
        assert "checks" in data

    def test_writes_audit_line(self, tmp_path: Path):
        ws = _init_repo(tmp_path / "ws")
        daemon = AgentixDaemon(ws)
        daemon.modules_dir = tmp_path / "empty"
        daemon.modules_dir.mkdir()

        report = daemon._run_cycle()
        daemon._write_status(report)

        audit_file = ws / ".agentix" / "audit.jsonl"
        assert audit_file.exists()
        lines = audit_file.read_text().strip().splitlines()
        assert len(lines) >= 1
        event = json.loads(lines[-1])
        assert event["action"] == "health_check"


class TestUpgradeDetection:
    def test_detects_submodule_upgrade(self, tmp_path: Path):
        upstream = _init_repo(tmp_path / "upstream")
        ws = _init_repo(tmp_path / "ws")
        env = {**subprocess.os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}

        # Add submodule
        subprocess.run(
            ["git", "-c", "protocol.file.allow=always", "submodule", "add", str(upstream), "repos/test-mod"],
            cwd=ws, capture_output=True, env=env,
        )
        subprocess.run(["git", "commit", "-m", "sub"], cwd=ws, capture_output=True, env=env)

        # Make upstream commit
        (upstream / "new.txt").write_text("x")
        subprocess.run(["git", "add", "."], cwd=upstream, capture_output=True, env=env)
        subprocess.run(["git", "commit", "-m", "new"], cwd=upstream, capture_output=True, env=env)

        # Fetch in submodule so origin/master is updated
        subprocess.run(["git", "fetch", "origin"], cwd=ws / "repos/test-mod", capture_output=True, env=env)

        daemon = AgentixDaemon(ws)
        daemon.last_snapshot = daemon.ws.extended_source_snapshot()
        upgrades = daemon._detect_upgrades()

        assert len(upgrades) == 1
        assert upgrades[0]["module"] == "test-mod"
        assert upgrades[0]["path"] == "repos/test-mod"


class TestSignalHandling:
    def test_sigterm_sets_running_false(self, tmp_path: Path):
        ws = _init_repo(tmp_path / "ws")
        daemon = AgentixDaemon(ws)
        assert daemon.running is True
        daemon._handle_signal(15, None)
        assert daemon.running is False
