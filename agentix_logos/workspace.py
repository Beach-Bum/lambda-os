"""logos-workspace adapter.

Wraps the `ws` CLI and Nix flake commands. Pure read/build operations only —
no writes to the source workspace.

See docs/BRIDGE-SPEC.md § 'workspace.py — logos-workspace adapter' for the
full contract.
"""

from __future__ import annotations

import dataclasses
import hashlib
import socket
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from agentix_logos.modules import ModuleRef, parse_scaffold_toml


@dataclass
class WorkspaceStatus:
    path: str
    git_branch: str | None
    git_dirty: bool
    submodule_count: int
    dirty_submodules: list[str]
    has_scaffold_toml: bool
    has_flake_lock: bool

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class BuildResult:
    target: str
    output_path: str
    duration_seconds: float
    success: bool
    log_path: str | None = None
    error: str | None = None


@dataclass
class SubmoduleDrift:
    path: str
    before_sha: str | None
    after_sha: str | None

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class SourceSnapshot:
    tracked_diff: str
    untracked_sha256s: dict[str, str]
    submodule_shas: dict[str, str]


@dataclass
class DriftReport:
    has_drift: bool
    tracked_changed: bool
    untracked_added: list[str] = field(default_factory=list)
    untracked_removed: list[str] = field(default_factory=list)
    untracked_modified: list[str] = field(default_factory=list)
    submodule_drifts: list[SubmoduleDrift] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        return d


class LogosWorkspace:
    """Adapter for a logos-workspace checkout.

    All methods are read-only or write strictly to caller-supplied output_dir.
    None of these methods mutate `self.path`.
    """

    def __init__(self, path: Path):
        if not path.exists():
            raise FileNotFoundError(f"logos-workspace path does not exist: {path}")
        self.path = path

    # ---- Read-only inspection ----

    def status(self) -> dict:
        """Return workspace state as a dict.

        Returns:
            Dict with keys: path, git_branch, git_dirty, submodule_count,
            dirty_submodules, has_scaffold_toml, has_flake_lock.
        """
        branch = self._git("rev-parse", "--abbrev-ref", "HEAD") or None
        dirty = bool(self._git("status", "--porcelain"))
        submodules = self.submodule_snapshot()
        dirty_subs = self._dirty_submodules()
        return WorkspaceStatus(
            path=str(self.path),
            git_branch=branch,
            git_dirty=dirty,
            submodule_count=len(submodules),
            dirty_submodules=dirty_subs,
            has_scaffold_toml=(self.path / "scaffold.toml").exists(),
            has_flake_lock=(self.path / "flake.lock").exists(),
        ).to_dict()

    def list_modules(self) -> list[ModuleRef]:
        """Parse scaffold.toml [modules.*] entries into ModuleRef list.

        TODO Phase 1: also walk repos/logos-modules and merge.
        """
        scaffold_path = self.path / "scaffold.toml"
        if not scaffold_path.exists():
            return []
        return parse_scaffold_toml(scaffold_path)

    def describe_module(self, name: str) -> ModuleRef | None:
        """Find a single module by name in the captured set.

        Args:
            name: Module name to look up.

        Returns:
            ModuleRef if found, None otherwise.
        """
        for mod in self.list_modules():
            if mod.name == name:
                return mod
        return None

    def submodule_snapshot(self) -> dict[str, str]:
        """Return submodule path → commit SHA.

        Used by the extended source snapshot to detect submodule pointer drift.

        Returns:
            Dict mapping submodule path to its current commit SHA.
            Empty dict if no submodules.
        """
        out = self._git("submodule", "status", "--recursive")
        if not out:
            return {}
        snapshot: dict[str, str] = {}
        for line in out.splitlines():
            # Format: " <sha> <path> (<branch>)" or "+<sha> ..." for dirty
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            sha = parts[0].lstrip("+-U")
            path = parts[1]
            snapshot[path] = sha
        return snapshot

    def extended_source_snapshot(self) -> SourceSnapshot:
        """Capture a full source snapshot for the source-untouched invariant.

        Returns:
            SourceSnapshot with tracked_diff, untracked_sha256s, and submodule_shas.
        """
        tracked_diff = self._git("diff", "HEAD", "--")
        untracked_sha256s = self._untracked_sha256s()
        submodule_shas = self.submodule_snapshot()
        return SourceSnapshot(
            tracked_diff=tracked_diff,
            untracked_sha256s=untracked_sha256s,
            submodule_shas=submodule_shas,
        )

    @staticmethod
    def compare_snapshots(before: SourceSnapshot, after: SourceSnapshot) -> DriftReport:
        """Compare two snapshots and return a typed drift report.

        Args:
            before: Snapshot taken before the worktree run.
            after: Snapshot taken after the worktree run.

        Returns:
            DriftReport categorizing all changes: tracked, untracked, submodule.
        """
        tracked_changed = before.tracked_diff != after.tracked_diff

        # Untracked file changes
        untracked_added = [
            p for p in after.untracked_sha256s if p not in before.untracked_sha256s
        ]
        untracked_removed = [
            p for p in before.untracked_sha256s if p not in after.untracked_sha256s
        ]
        untracked_modified = [
            p
            for p in before.untracked_sha256s
            if p in after.untracked_sha256s
            and before.untracked_sha256s[p] != after.untracked_sha256s[p]
        ]

        # Submodule drift
        submodule_drifts: list[SubmoduleDrift] = []
        all_paths = set(before.submodule_shas) | set(after.submodule_shas)
        for path in sorted(all_paths):
            before_sha = before.submodule_shas.get(path)
            after_sha = after.submodule_shas.get(path)
            if before_sha != after_sha:
                submodule_drifts.append(
                    SubmoduleDrift(
                        path=path,
                        before_sha=before_sha,
                        after_sha=after_sha,
                    )
                )

        has_drift = (
            tracked_changed
            or bool(untracked_added)
            or bool(untracked_removed)
            or bool(untracked_modified)
            or bool(submodule_drifts)
        )

        return DriftReport(
            has_drift=has_drift,
            tracked_changed=tracked_changed,
            untracked_added=untracked_added,
            untracked_removed=untracked_removed,
            untracked_modified=untracked_modified,
            submodule_drifts=submodule_drifts,
        )

    def _untracked_sha256s(self) -> dict[str, str]:
        """SHA-256 hash of each untracked file."""
        out = self._git("ls-files", "--others", "--exclude-standard")
        if not out:
            return {}
        result: dict[str, str] = {}
        for rel_path in out.splitlines():
            rel_path = rel_path.strip()
            if not rel_path:
                continue
            full = self.path / rel_path
            if full.is_file():
                result[rel_path] = hashlib.sha256(full.read_bytes()).hexdigest()
        return result

    def _dirty_submodules(self) -> list[str]:
        out = self._git("submodule", "foreach", "--recursive", "git status --porcelain")
        if not out:
            return []
        dirty: list[str] = []
        current_path: str | None = None
        for line in out.splitlines():
            if line.startswith("Entering '"):
                current_path = line.removeprefix("Entering '").rstrip("'")
            elif line.strip() and current_path:
                if current_path not in dirty:
                    dirty.append(current_path)
        return dirty

    # ---- Sandboxed builds ----

    def build(
        self,
        target: str,
        *,
        override: dict[str, Path] | None = None,
        output_dir: Path | None = None,
        timeout: int = 1800,
    ) -> BuildResult:
        """Run ``nix build .#<target>`` with optional ``--override-input``.

        Args:
            target: Nix flake target (e.g. "logos-basecamp", "storage_module").
            override: Map of flake input name to local path override.
            output_dir: Directory for build output symlinks. If None, uses
                the workspace's default ``result/`` symlink.
            timeout: Build timeout in seconds (default 1800 = 30 min).

        Returns:
            BuildResult with success status, output path, and timing.

        Raises:
            Nothing — failures are returned in BuildResult.error.
        """
        import time

        cmd = ["nix", "build", f".#{target}"]
        if override:
            for input_name, override_path in override.items():
                cmd.extend(["--override-input", input_name, f"path:{override_path}"])
        if output_dir is not None:
            output_dir.mkdir(parents=True, exist_ok=True)
            cmd.extend(["--out-link", str(output_dir / f"result-{target}")])

        start = time.time()
        try:
            proc = subprocess.run(
                cmd,
                cwd=self.path,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            return BuildResult(
                target=target,
                output_path="",
                duration_seconds=time.time() - start,
                success=False,
                error=f"timeout: {exc}",
            )
        duration = time.time() - start
        if proc.returncode != 0:
            return BuildResult(
                target=target,
                output_path="",
                duration_seconds=duration,
                success=False,
                error=proc.stderr.strip()[-2000:],
            )
        out = output_dir / f"result-{target}" if output_dir else self.path / "result"
        return BuildResult(
            target=target,
            output_path=str(out.resolve()),
            duration_seconds=duration,
            success=True,
        )

    def graph(self, target: str) -> dict:
        """Return dependency graph for a target repo via ``ws graph``.

        Args:
            target: Repository name to graph.

        Returns:
            Dict with 'target' and 'raw' (stdout from ws graph).
        """
        out = self._ws("graph", target)
        return {"target": target, "raw": out}

    # ---- Sandbox port allocation ----

    def allocate_sandbox_port(self, port_range: tuple[int, int] = (13000, 14000)) -> int:
        """Find an unused port in the given range for sandbox localnet.

        Args:
            port_range: (min, max) port range to search.

        Returns:
            An available port number.

        Raises:
            RuntimeError: If no free port is found in the range.
        """
        for port in range(port_range[0], port_range[1]):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                try:
                    sock.bind(("127.0.0.1", port))
                except OSError:
                    continue
                return port
        raise RuntimeError(f"No free port in range {port_range}")

    # ---- Internals ----

    def _git(self, *args: str) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=self.path,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            return ""
        return proc.stdout

    def _ws(self, *args: str) -> str:
        ws_script = self.path / "scripts" / "ws"
        if not ws_script.exists():
            raise FileNotFoundError(f"ws script not found at {ws_script}")
        proc = subprocess.run(
            [str(ws_script), *args],
            cwd=self.path,
            capture_output=True,
            text=True,
        )
        return proc.stdout
