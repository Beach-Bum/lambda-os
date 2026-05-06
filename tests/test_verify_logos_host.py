"""Tests for logos_host backend in verify_logoscore.

Validates that the logos_host fallback works correctly: module load
verification via timeout-as-success, plugin discovery, and the auto
backend selection logic.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agentix_logos.verify_logoscore import (
    _find_plugin,
    _resolve_backend,
    verify_logoscore,
)


def _make_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / ".git").mkdir()
    return ws


def _make_logos_host(tmp_path: Path) -> Path:
    """Create a fake logos_host binary in workspace result."""
    bin_dir = tmp_path / "workspace" / "result" / "bin"
    bin_dir.mkdir(parents=True)
    logos_host = bin_dir / "logos_host"
    logos_host.write_text("#!/bin/sh\nsleep 10\n")
    logos_host.chmod(0o755)
    return logos_host


def _make_modules_dir(tmp_path: Path, module_name: str = "capability_module") -> Path:
    """Create a fake modules directory with a plugin file."""
    modules_dir = tmp_path / "workspace" / "result" / "modules"
    mod_dir = modules_dir / module_name
    mod_dir.mkdir(parents=True)
    plugin = mod_dir / f"{module_name}_plugin.so"
    plugin.write_bytes(b"\x7fELF")  # fake ELF header
    return modules_dir


class TestFindPlugin:
    def test_finds_nested_plugin(self, tmp_path: Path):
        modules_dir = _make_modules_dir(tmp_path)
        result = _find_plugin(modules_dir, "capability_module")
        assert result is not None
        assert result.name == "capability_module_plugin.so"

    def test_returns_none_for_missing(self, tmp_path: Path):
        modules_dir = tmp_path / "empty"
        modules_dir.mkdir()
        assert _find_plugin(modules_dir, "nonexistent") is None

    def test_finds_flat_layout(self, tmp_path: Path):
        modules_dir = tmp_path / "modules"
        modules_dir.mkdir()
        (modules_dir / "my_mod_plugin.so").write_bytes(b"\x7fELF")
        result = _find_plugin(modules_dir, "my_mod")
        assert result is not None


class TestResolveBackend:
    def test_finds_logos_host_in_workspace(self, tmp_path: Path):
        ws = _make_workspace(tmp_path)
        _make_logos_host(tmp_path)
        with patch("shutil.which", return_value=None):
            path, backend_type = _resolve_backend(ws, "auto")
        assert backend_type == "logos_host"
        assert "logos_host" in path

    def test_prefers_logoscore_when_available(self, tmp_path: Path):
        ws = _make_workspace(tmp_path)
        with patch("shutil.which", return_value="/usr/bin/logoscore"):
            path, backend_type = _resolve_backend(ws, "auto")
        assert backend_type == "logoscore"

    def test_raises_when_nothing_found(self, tmp_path: Path):
        ws = _make_workspace(tmp_path)
        with patch("shutil.which", return_value=None):
            with pytest.raises(FileNotFoundError, match="Neither logoscore nor logos_host"):
                _resolve_backend(ws, "auto")

    def test_explicit_logos_host_backend(self, tmp_path: Path):
        ws = _make_workspace(tmp_path)
        _make_logos_host(tmp_path)
        with patch("shutil.which", return_value=None):
            path, backend_type = _resolve_backend(ws, "logos_host")
        assert backend_type == "logos_host"


class TestVerifyWithLogosHost:
    def test_module_load_timeout_is_success(self, tmp_path: Path):
        """logos_host timeout means the module loaded — treat as pass."""
        ws = _make_workspace(tmp_path)
        _make_logos_host(tmp_path)
        modules_dir = _make_modules_dir(tmp_path)
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        import subprocess

        def mock_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 5))

        with (
            patch("shutil.which", return_value=None),
            patch("agentix_logos.verify_logoscore.subprocess.run", side_effect=mock_run),
        ):
            passed, results = verify_logoscore(
                workspace=ws,
                worktree=worktree,
                modules=["capability_module"],
                calls=["capability_module.load()"],
                modules_dir=modules_dir,
                backend="logos_host",
            )

        assert passed is True
        assert len(results) == 1
        assert results[0].exit_code == 0
        assert results[0].backend == "logos_host"

    def test_module_crash_is_failure(self, tmp_path: Path):
        """logos_host non-zero exit means the module failed to load."""
        ws = _make_workspace(tmp_path)
        _make_logos_host(tmp_path)
        modules_dir = _make_modules_dir(tmp_path)
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = b"error loading module"
        mock_proc.stderr = b"segfault"

        with (
            patch("shutil.which", return_value=None),
            patch("agentix_logos.verify_logoscore.subprocess.run", return_value=mock_proc),
        ):
            passed, results = verify_logoscore(
                workspace=ws,
                worktree=worktree,
                modules=["capability_module"],
                calls=["capability_module.load()"],
                modules_dir=modules_dir,
                backend="logos_host",
            )

        assert passed is False
        assert results[0].exit_code == 1

    def test_missing_plugin_is_failure(self, tmp_path: Path):
        """If the plugin .so doesn't exist, fail without crashing."""
        ws = _make_workspace(tmp_path)
        _make_logos_host(tmp_path)
        modules_dir = tmp_path / "empty_modules"
        modules_dir.mkdir()
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        with patch("shutil.which", return_value=None):
            passed, results = verify_logoscore(
                workspace=ws,
                worktree=worktree,
                modules=["nonexistent_module"],
                calls=["nonexistent_module.load()"],
                modules_dir=modules_dir,
                backend="logos_host",
            )

        assert passed is False
        assert results[0].exit_code == 1

    def test_deduplicates_module_loads(self, tmp_path: Path):
        """Multiple calls for the same module only load it once."""
        ws = _make_workspace(tmp_path)
        _make_logos_host(tmp_path)
        modules_dir = _make_modules_dir(tmp_path)
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        import subprocess
        call_count = 0

        def mock_run(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            raise subprocess.TimeoutExpired(cmd, 5)

        with (
            patch("shutil.which", return_value=None),
            patch("agentix_logos.verify_logoscore.subprocess.run", side_effect=mock_run),
        ):
            passed, results = verify_logoscore(
                workspace=ws,
                worktree=worktree,
                modules=["capability_module"],
                calls=[
                    "capability_module.load()",
                    "capability_module.healthcheck()",
                ],
                modules_dir=modules_dir,
                backend="logos_host",
            )

        assert passed is True
        assert len(results) == 2
        assert call_count == 1  # Only one actual subprocess call
        assert results[1].duration_seconds == 0.0  # Cached

    def test_workspace_result_modules_fallback(self, tmp_path: Path):
        """When modules_dir doesn't exist, falls back to workspace/result/modules."""
        ws = _make_workspace(tmp_path)
        _make_logos_host(tmp_path)
        _make_modules_dir(tmp_path)
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        import subprocess

        def mock_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 5)

        with (
            patch("shutil.which", return_value=None),
            patch("agentix_logos.verify_logoscore.subprocess.run", side_effect=mock_run),
        ):
            passed, results = verify_logoscore(
                workspace=ws,
                worktree=worktree,
                modules=["capability_module"],
                calls=["capability_module.load()"],
                backend="logos_host",
                # modules_dir not specified — should auto-detect
            )

        assert passed is True
