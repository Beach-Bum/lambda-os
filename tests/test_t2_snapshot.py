"""T2 (PER-3): Tests for extended source snapshot and drift comparison.

Uses tmp git repos with synthetic submodules to validate snapshot capture
and comparison logic.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from agentix_logos.workspace import (
    DriftReport,
    LogosWorkspace,
    SourceSnapshot,
    SubmoduleDrift,
)


def _git(path: Path, *args: str) -> str:
    env = {
        **subprocess.os.environ,
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
    }
    proc = subprocess.run(
        ["git", "-c", "protocol.file.allow=always", *args],
        cwd=path,
        capture_output=True,
        text=True,
        env=env,
    )
    return proc.stdout


def _init_repo(path: Path) -> Path:
    """Create and init a git repo at path."""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init")
    _git(path, "config", "user.email", "test@test.com")
    _git(path, "config", "user.name", "Test")
    (path / "README.md").write_text("# test\n")
    _git(path, "add", ".")
    _git(path, "commit", "-m", "init")
    return path


def _add_submodule(parent: Path, sub_path: Path, name: str) -> None:
    """Add sub_path as a submodule named `name` under parent/repos/name."""
    _git(parent, "submodule", "add", str(sub_path), f"repos/{name}")
    _git(parent, "commit", "-m", f"add submodule {name}")


class TestExtendedSourceSnapshot:
    def test_snapshot_captures_tracked_diff(self, tmp_path: Path):
        repo = _init_repo(tmp_path / "ws")
        ws = LogosWorkspace(repo)
        snap = ws.extended_source_snapshot()
        assert snap.tracked_diff == ""

        # Make a tracked change
        (repo / "README.md").write_text("# modified\n")
        snap2 = ws.extended_source_snapshot()
        assert "modified" in snap2.tracked_diff

    def test_snapshot_captures_untracked_files(self, tmp_path: Path):
        repo = _init_repo(tmp_path / "ws")
        ws = LogosWorkspace(repo)
        snap = ws.extended_source_snapshot()
        assert snap.untracked_sha256s == {}

        # Add an untracked file
        (repo / "new_file.txt").write_text("hello")
        snap2 = ws.extended_source_snapshot()
        assert "new_file.txt" in snap2.untracked_sha256s

    def test_snapshot_captures_submodule_shas(self, tmp_path: Path):
        upstream = _init_repo(tmp_path / "upstream")
        repo = _init_repo(tmp_path / "ws")
        _add_submodule(repo, upstream, "test-module")

        ws = LogosWorkspace(repo)
        snap = ws.extended_source_snapshot()
        assert "repos/test-module" in snap.submodule_shas
        assert len(snap.submodule_shas["repos/test-module"]) == 40  # full SHA


class TestCompareSnapshots:
    def test_identical_snapshots_no_drift(self):
        snap = SourceSnapshot(
            tracked_diff="",
            untracked_sha256s={},
            submodule_shas={"repos/a": "abc123"},
        )
        report = LogosWorkspace.compare_snapshots(snap, snap)
        assert report.has_drift is False
        assert report.tracked_changed is False
        assert report.submodule_drifts == []

    def test_tracked_diff_change_detected(self):
        before = SourceSnapshot(tracked_diff="", untracked_sha256s={}, submodule_shas={})
        after = SourceSnapshot(tracked_diff="diff --git a/f", untracked_sha256s={}, submodule_shas={})
        report = LogosWorkspace.compare_snapshots(before, after)
        assert report.has_drift is True
        assert report.tracked_changed is True

    def test_submodule_drift_detected(self):
        before = SourceSnapshot(
            tracked_diff="",
            untracked_sha256s={},
            submodule_shas={"repos/storage": "aaa", "repos/chat": "bbb"},
        )
        after = SourceSnapshot(
            tracked_diff="",
            untracked_sha256s={},
            submodule_shas={"repos/storage": "aaa", "repos/chat": "ccc"},
        )
        report = LogosWorkspace.compare_snapshots(before, after)
        assert report.has_drift is True
        assert len(report.submodule_drifts) == 1
        assert report.submodule_drifts[0].path == "repos/chat"
        assert report.submodule_drifts[0].before_sha == "bbb"
        assert report.submodule_drifts[0].after_sha == "ccc"

    def test_untracked_file_added(self):
        before = SourceSnapshot(tracked_diff="", untracked_sha256s={}, submodule_shas={})
        after = SourceSnapshot(
            tracked_diff="",
            untracked_sha256s={"new.txt": "sha256abc"},
            submodule_shas={},
        )
        report = LogosWorkspace.compare_snapshots(before, after)
        assert report.has_drift is True
        assert report.untracked_added == ["new.txt"]

    def test_untracked_file_removed(self):
        before = SourceSnapshot(
            tracked_diff="",
            untracked_sha256s={"old.txt": "sha256abc"},
            submodule_shas={},
        )
        after = SourceSnapshot(tracked_diff="", untracked_sha256s={}, submodule_shas={})
        report = LogosWorkspace.compare_snapshots(before, after)
        assert report.has_drift is True
        assert report.untracked_removed == ["old.txt"]

    def test_untracked_file_modified(self):
        before = SourceSnapshot(
            tracked_diff="",
            untracked_sha256s={"f.txt": "sha_before"},
            submodule_shas={},
        )
        after = SourceSnapshot(
            tracked_diff="",
            untracked_sha256s={"f.txt": "sha_after"},
            submodule_shas={},
        )
        report = LogosWorkspace.compare_snapshots(before, after)
        assert report.has_drift is True
        assert report.untracked_modified == ["f.txt"]

    def test_submodule_added(self):
        before = SourceSnapshot(tracked_diff="", untracked_sha256s={}, submodule_shas={})
        after = SourceSnapshot(
            tracked_diff="",
            untracked_sha256s={},
            submodule_shas={"repos/new-mod": "abc123"},
        )
        report = LogosWorkspace.compare_snapshots(before, after)
        assert report.has_drift is True
        assert len(report.submodule_drifts) == 1
        assert report.submodule_drifts[0].before_sha is None
        assert report.submodule_drifts[0].after_sha == "abc123"

    def test_drift_report_to_dict(self):
        report = DriftReport(
            has_drift=True,
            tracked_changed=False,
            submodule_drifts=[SubmoduleDrift(path="repos/a", before_sha="x", after_sha="y")],
        )
        d = report.to_dict()
        assert d["has_drift"] is True
        assert len(d["submodule_drifts"]) == 1
        assert d["submodule_drifts"][0]["path"] == "repos/a"


class TestEndToEndSnapshot:
    def test_real_git_repo_no_drift(self, tmp_path: Path):
        """Full roundtrip: snapshot, do nothing, snapshot again, compare."""
        upstream = _init_repo(tmp_path / "upstream")
        repo = _init_repo(tmp_path / "ws")
        _add_submodule(repo, upstream, "test-mod")

        ws = LogosWorkspace(repo)
        before = ws.extended_source_snapshot()
        after = ws.extended_source_snapshot()
        report = ws.compare_snapshots(before, after)
        assert report.has_drift is False

    def test_real_git_repo_with_tracked_change(self, tmp_path: Path):
        """Modify a tracked file between snapshots."""
        repo = _init_repo(tmp_path / "ws")
        ws = LogosWorkspace(repo)

        before = ws.extended_source_snapshot()
        (repo / "README.md").write_text("# changed\n")
        after = ws.extended_source_snapshot()

        report = ws.compare_snapshots(before, after)
        assert report.has_drift is True
        assert report.tracked_changed is True
