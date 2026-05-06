"""T3 (PER-4): Tests for policy enforcement of submodule drift.

Verifies that check_logos_policy emits PolicyViolation with
rule="submodule_drift_is_violation" when repos/* SHA changes are
detected between before and after snapshots.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentix_logos.policy import check_logos_policy
from agentix_logos.workspace import SourceSnapshot


def _write_policy(tmp_path: Path, logos_block: dict | None = None) -> None:
    """Write a minimal policy.json with optional logos block."""
    agentix_dir = tmp_path / ".agentix"
    agentix_dir.mkdir(exist_ok=True)
    policy = {"denied": ["sudo"], "allowed": ["ws build"]}
    if logos_block is not None:
        policy["logos"] = logos_block
    (agentix_dir / "policy.json").write_text(json.dumps(policy))


def test_no_drift_no_violation(tmp_path: Path):
    """Identical snapshots produce no submodule drift violation."""
    _write_policy(tmp_path, {"submodule_drift_is_violation": True})
    snap = SourceSnapshot(
        tracked_diff="",
        untracked_sha256s={},
        submodule_shas={"repos/storage": "aaa", "repos/chat": "bbb"},
    )
    violations = check_logos_policy(
        tmp_path,
        proposal_diff="",
        modules_touched=[],
        before_snapshot=snap,
        after_snapshot=snap,
    )
    assert len(violations) == 0


def test_repos_drift_emits_violation(tmp_path: Path):
    """SHA change in repos/* triggers submodule_drift_is_violation."""
    _write_policy(tmp_path, {"submodule_drift_is_violation": True})
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
    violations = check_logos_policy(
        tmp_path,
        proposal_diff="",
        modules_touched=[],
        before_snapshot=before,
        after_snapshot=after,
    )
    drift_violations = [v for v in violations if v.rule == "submodule_drift_is_violation"]
    assert len(drift_violations) == 1
    assert drift_violations[0].path == "repos/chat"
    assert drift_violations[0].severity == "deny"
    assert "bbb" in drift_violations[0].details
    assert "ccc" in drift_violations[0].details


def test_non_repos_drift_not_flagged(tmp_path: Path):
    """SHA change outside repos/* is not flagged by submodule_drift_is_violation."""
    _write_policy(tmp_path, {"submodule_drift_is_violation": True})
    before = SourceSnapshot(
        tracked_diff="",
        untracked_sha256s={},
        submodule_shas={"vendor/lib": "aaa"},
    )
    after = SourceSnapshot(
        tracked_diff="",
        untracked_sha256s={},
        submodule_shas={"vendor/lib": "bbb"},
    )
    violations = check_logos_policy(
        tmp_path,
        proposal_diff="",
        modules_touched=[],
        before_snapshot=before,
        after_snapshot=after,
    )
    drift_violations = [v for v in violations if v.rule == "submodule_drift_is_violation"]
    assert len(drift_violations) == 0


def test_drift_check_disabled_by_policy(tmp_path: Path):
    """When submodule_drift_is_violation is false, no drift violations."""
    _write_policy(tmp_path, {"submodule_drift_is_violation": False})
    before = SourceSnapshot(
        tracked_diff="",
        untracked_sha256s={},
        submodule_shas={"repos/storage": "aaa"},
    )
    after = SourceSnapshot(
        tracked_diff="",
        untracked_sha256s={},
        submodule_shas={"repos/storage": "bbb"},
    )
    violations = check_logos_policy(
        tmp_path,
        proposal_diff="",
        modules_touched=[],
        before_snapshot=before,
        after_snapshot=after,
    )
    drift_violations = [v for v in violations if v.rule == "submodule_drift_is_violation"]
    assert len(drift_violations) == 0


def test_no_snapshots_skips_drift_check(tmp_path: Path):
    """When snapshots are not provided, drift check is skipped."""
    _write_policy(tmp_path, {"submodule_drift_is_violation": True})
    violations = check_logos_policy(
        tmp_path,
        proposal_diff="",
        modules_touched=[],
        before_snapshot=None,
        after_snapshot=None,
    )
    assert len(violations) == 0


def test_multiple_repos_drift(tmp_path: Path):
    """Multiple repos/* drifts produce multiple violations."""
    _write_policy(tmp_path, {"submodule_drift_is_violation": True})
    before = SourceSnapshot(
        tracked_diff="",
        untracked_sha256s={},
        submodule_shas={"repos/a": "111", "repos/b": "222", "repos/c": "333"},
    )
    after = SourceSnapshot(
        tracked_diff="",
        untracked_sha256s={},
        submodule_shas={"repos/a": "999", "repos/b": "222", "repos/c": "888"},
    )
    violations = check_logos_policy(
        tmp_path,
        proposal_diff="",
        modules_touched=[],
        before_snapshot=before,
        after_snapshot=after,
    )
    drift_violations = [v for v in violations if v.rule == "submodule_drift_is_violation"]
    assert len(drift_violations) == 2
    paths = {v.path for v in drift_violations}
    assert paths == {"repos/a", "repos/c"}
