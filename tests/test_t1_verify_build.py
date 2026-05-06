"""T1 (PER-2): Tests for LogosWorkspace.build() wired into verify_logoscore.

Tests that verify_logoscore auto-builds modules when modules_dir doesn't
exist, using mocked subprocess.run to avoid needing a real logos-workspace.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _make_workspace(tmp_path: Path) -> Path:
    """Create a minimal fake workspace directory."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / ".git").mkdir()  # LogosWorkspace checks path.exists()
    return ws


def _make_logoscore_script(tmp_path: Path) -> Path:
    """Create a fake logoscore script on disk."""
    scripts = tmp_path / "workspace" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    logoscore = scripts / "logoscore"
    logoscore.write_text("#!/bin/sh\necho ok\n")
    logoscore.chmod(0o755)
    return logoscore


def test_auto_build_called_when_modules_dir_missing(tmp_path: Path):
    """When modules_dir doesn't exist, verify_logoscore calls ws.build()."""
    from agentix_logos.verify_logoscore import verify_logoscore

    ws = _make_workspace(tmp_path)
    _make_logoscore_script(tmp_path)
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    build_called_with: list[str] = []

    def mock_build(self, target, *, override=None, output_dir=None, timeout=1800):
        from agentix_logos.workspace import BuildResult

        build_called_with.append(target)
        # Create the output dir to simulate a successful build
        if output_dir is not None:
            output_dir.mkdir(parents=True, exist_ok=True)
        return BuildResult(
            target=target,
            output_path=str(output_dir or ""),
            duration_seconds=0.1,
            success=True,
        )

    # Mock subprocess.run for the logoscore call itself
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = b"healthcheck ok"
    mock_proc.stderr = b""

    with (
        patch("agentix_logos.workspace.LogosWorkspace.build", mock_build),
        patch("subprocess.run", return_value=mock_proc),
        patch("shutil.which", return_value=None),  # force fallback to ws scripts/
    ):
        passed, results = verify_logoscore(
            workspace=ws,
            worktree=worktree,
            modules=["storage_module"],
            calls=["storage_module.healthcheck()"],
        )

    assert build_called_with == ["storage_module"]
    assert passed is True
    assert len(results) == 1


def test_auto_build_correct_args(tmp_path: Path):
    """Build is called with output_dir inside the worktree."""
    from agentix_logos.verify_logoscore import verify_logoscore

    ws = _make_workspace(tmp_path)
    _make_logoscore_script(tmp_path)
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    captured_output_dirs: list[Path | None] = []

    def mock_build(self, target, *, override=None, output_dir=None, timeout=1800):
        from agentix_logos.workspace import BuildResult

        captured_output_dirs.append(output_dir)
        if output_dir is not None:
            output_dir.mkdir(parents=True, exist_ok=True)
        return BuildResult(
            target=target,
            output_path=str(output_dir or ""),
            duration_seconds=0.1,
            success=True,
        )

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = b"ok"
    mock_proc.stderr = b""

    with (
        patch("agentix_logos.workspace.LogosWorkspace.build", mock_build),
        patch("subprocess.run", return_value=mock_proc),
        patch("shutil.which", return_value=None),
    ):
        verify_logoscore(
            workspace=ws,
            worktree=worktree,
            modules=["storage_module"],
            calls=["storage_module.healthcheck()"],
        )

    assert len(captured_output_dirs) == 1
    # output_dir must be inside the worktree (modules-built subdir)
    assert captured_output_dirs[0] is not None
    assert str(captured_output_dirs[0]).startswith(str(worktree))


def test_auto_build_resolves_modules_dir(tmp_path: Path):
    """After auto-build, modules_dir is passed to logoscore -m flag."""
    from agentix_logos.verify_logoscore import verify_logoscore

    ws = _make_workspace(tmp_path)
    _make_logoscore_script(tmp_path)
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    captured_cmds: list[list[str]] = []

    def mock_build(self, target, *, override=None, output_dir=None, timeout=1800):
        from agentix_logos.workspace import BuildResult

        if output_dir is not None:
            output_dir.mkdir(parents=True, exist_ok=True)
        return BuildResult(
            target=target,
            output_path=str(output_dir or ""),
            duration_seconds=0.1,
            success=True,
        )

    def mock_run(cmd, **kwargs):
        captured_cmds.append(list(cmd))
        result = MagicMock()
        result.returncode = 0
        result.stdout = b"ok"
        result.stderr = b""
        return result

    with (
        patch("agentix_logos.workspace.LogosWorkspace.build", mock_build),
        patch("subprocess.run", side_effect=mock_run),
        patch("shutil.which", return_value=None),
    ):
        verify_logoscore(
            workspace=ws,
            worktree=worktree,
            modules=["storage_module"],
            calls=["storage_module.healthcheck()"],
        )

    # Find the logoscore invocation (contains -m flag)
    logoscore_cmds = [c for c in captured_cmds if "-m" in c]
    assert len(logoscore_cmds) == 1
    cmd = logoscore_cmds[0]
    m_idx = cmd.index("-m")
    modules_dir_arg = cmd[m_idx + 1]
    assert modules_dir_arg == str(worktree / "modules-built")


def test_auto_build_failure_surfaces_clearly(tmp_path: Path):
    """When build fails, verify_logoscore raises RuntimeError with details."""
    from agentix_logos.verify_logoscore import verify_logoscore

    ws = _make_workspace(tmp_path)
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    def mock_build(self, target, *, override=None, output_dir=None, timeout=1800):
        from agentix_logos.workspace import BuildResult

        return BuildResult(
            target=target,
            output_path="",
            duration_seconds=0.5,
            success=False,
            error="error: attribute 'storage_module' not found in flake",
        )

    with patch("agentix_logos.workspace.LogosWorkspace.build", mock_build):
        with pytest.raises(RuntimeError, match="Auto-build failed.*storage_module"):
            verify_logoscore(
                workspace=ws,
                worktree=worktree,
                modules=["storage_module"],
                calls=["storage_module.healthcheck()"],
            )


def test_existing_modules_dir_skips_build(tmp_path: Path):
    """When modules_dir already exists, no build is triggered."""
    from agentix_logos.verify_logoscore import verify_logoscore

    ws = _make_workspace(tmp_path)
    _make_logoscore_script(tmp_path)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    modules_dir = worktree / "modules-built"
    modules_dir.mkdir()

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = b"ok"
    mock_proc.stderr = b""

    with (
        patch("agentix_logos.workspace.LogosWorkspace.build") as mock_build,
        patch("subprocess.run", return_value=mock_proc),
        patch("shutil.which", return_value=None),
    ):
        passed, results = verify_logoscore(
            workspace=ws,
            worktree=worktree,
            modules=["storage_module"],
            calls=["storage_module.healthcheck()"],
        )

    mock_build.assert_not_called()
    assert passed is True
