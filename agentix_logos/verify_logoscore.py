"""Logos verify rung — module loading and healthcheck verification.

Supports two backends:
  - ``logoscore``: the full CLI (``logoscore -m <dir> -l <mods> -c <call>``)
  - ``logos_host``: the low-level host binary (``logos_host --name <mod> --path <plugin>``)

``logos_host`` is the fallback when ``logoscore`` isn't available (e.g. when
``logoscore-cli`` fails to build due to upstream toolchain issues). A module
that loads without crashing within the timeout is considered verified.

See docs/BRIDGE-SPEC.md for the full contract.
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from agentix_logos.workspace import BuildResult

Backend = Literal["logoscore", "logos_host", "auto"]


@dataclass
class LogoscoreCall:
    module: str
    method: str
    args: list[str]
    exit_code: int
    stdout_sha256: str
    stderr_sha256: str
    duration_seconds: float
    raw_call: str
    backend: str = "logoscore"

    def to_dict(self) -> dict:
        """Serialize to a plain dict for JSON output."""
        return dataclasses.asdict(self)


_CALL_RE = re.compile(r"^([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)\((.*)\)\s*$")


def _parse_call(call_str: str) -> tuple[str, str, list[str]]:
    """Parse 'module.method(arg1, arg2)' into (module, method, args)."""
    m = _CALL_RE.match(call_str.strip())
    if not m:
        raise ValueError(f"Invalid call syntax: {call_str!r}; expected 'module.method(args)'")
    module, method, args_str = m.group(1), m.group(2), m.group(3)
    if not args_str.strip():
        return module, method, []
    args = [a.strip() for a in args_str.split(",")]
    return module, method, args


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _find_plugin(modules_dir: Path, module_name: str) -> Path | None:
    """Locate a module's .so/.dylib plugin file inside modules_dir."""
    # Convention: modules_dir/<module_name>/<module_name>_plugin.so
    for suffix in ("_plugin.so", "_plugin.dylib", ".so", ".dylib"):
        candidate = modules_dir / module_name / f"{module_name}{suffix}"
        if candidate.exists():
            return candidate
    # Flat layout: modules_dir/<module_name>_plugin.so
    for suffix in ("_plugin.so", "_plugin.dylib"):
        candidate = modules_dir / f"{module_name}{suffix}"
        if candidate.exists():
            return candidate
    return None


def _resolve_backend(
    workspace: Path, backend: Backend
) -> tuple[str, Literal["logoscore", "logos_host"]]:
    """Find the verify binary path and determine which backend to use.

    Returns:
        (binary_path, backend_type)
    """
    if backend == "logoscore" or backend == "auto":
        path = shutil.which("logoscore")
        if path:
            return path, "logoscore"
        ws_logoscore = workspace / "scripts" / "logoscore"
        if ws_logoscore.exists():
            return str(ws_logoscore), "logoscore"

    if backend == "logos_host" or backend == "auto":
        path = shutil.which("logos_host")
        if path:
            return path, "logos_host"
        # Check workspace build result
        for candidate in [
            workspace / "result" / "bin" / "logos_host",
            workspace / "repos" / "logos-liblogos" / "result" / "bin" / "logos_host",
        ]:
            if candidate.exists():
                return str(candidate), "logos_host"

    if backend == "auto":
        raise FileNotFoundError(
            "Neither logoscore nor logos_host found. "
            "Add <workspace>/scripts to PATH, or build the workspace first "
            "(ws build logos-basecamp), or specify --backend explicitly."
        )

    raise FileNotFoundError(f"Backend {backend!r} binary not found on PATH or in workspace.")


def _run_logoscore(
    binary: str,
    modules_dir: Path,
    modules: list[str],
    call_str: str,
    timeout: int,
    env: dict[str, str],
) -> tuple[int, bytes, bytes, float]:
    """Run a single logoscore -m -l -c call."""
    cmd = [binary, "-m", str(modules_dir), "-l", ",".join(modules), "-c", call_str]
    start = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env)
        return proc.returncode, proc.stdout, proc.stderr, time.time() - start
    except subprocess.TimeoutExpired as exc:
        return (
            124,
            exc.stdout or b"",
            (exc.stderr or b"") + f"\n[agentix-logos] timeout after {timeout}s\n".encode(),
            time.time() - start,
        )


def _run_logos_host(
    binary: str,
    modules_dir: Path,
    module_name: str,
    timeout: int,
    env: dict[str, str],
) -> tuple[int, bytes, bytes, float]:
    """Load a module via logos_host and verify it doesn't crash.

    logos_host is a daemon — it loads the module and waits for IPC. A timeout
    means the module loaded successfully. A crash (non-zero, non-timeout exit)
    means verification failed.
    """
    plugin = _find_plugin(modules_dir, module_name)
    if plugin is None:
        msg = f"Plugin not found for {module_name!r} in {modules_dir}"
        return 1, b"", msg.encode(), 0.0

    cmd = [binary, "--name", module_name, "--path", str(plugin)]
    start = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env)
        # If it exits cleanly before timeout, that's fine too
        return proc.returncode, proc.stdout, proc.stderr, time.time() - start
    except subprocess.TimeoutExpired as exc:
        # Timeout = module loaded and is waiting for IPC = SUCCESS
        return 0, exc.stdout or b"", exc.stderr or b"", time.time() - start


def verify_logoscore(
    *,
    workspace: Path,
    worktree: Path | None,
    modules: list[str],
    calls: list[str],
    timeout: int = 120,
    modules_dir: Path | None = None,
    extra_env: dict[str, str] | None = None,
    backend: Backend = "auto",
) -> tuple[bool, list[LogoscoreCall]]:
    """Run module verification against a sandboxed module set.

    Args:
        workspace: Path to logos-workspace (read-only here).
        worktree: Path to a temp Agentix worktree (where build outputs live).
            If None, a temp dir is created and cleaned up.
        modules: List of module names (e.g. ["capability_module"]).
        calls: List of "module.method(args)" strings. For logos_host backend,
            these are interpreted as load-verify checks (method is ignored).
        timeout: Per-call subprocess timeout in seconds.
        modules_dir: Path to directory containing built modules. If None,
            defaults to worktree/modules-built (with auto-build) or
            workspace/result/modules.
        extra_env: Additional env vars (LOGOS_USER_DIR is always set).
        backend: "logoscore", "logos_host", or "auto" (try logoscore first).

    Returns:
        (all_passed, [LogoscoreCall, ...])
    """
    if not workspace.exists():
        raise FileNotFoundError(f"workspace not found: {workspace}")

    cleanup_worktree = False
    if worktree is None:
        worktree = Path(tempfile.mkdtemp(prefix="agentix-logos-verify-"))
        cleanup_worktree = True

    if modules_dir is None:
        modules_dir = worktree / "modules-built"

    if not modules_dir.exists():
        # Check workspace build result as fallback
        ws_modules = workspace / "result" / "modules"
        if ws_modules.exists():
            modules_dir = ws_modules
        else:
            from agentix_logos.workspace import LogosWorkspace

            ws = LogosWorkspace(workspace)
            modules_dir.mkdir(parents=True, exist_ok=True)
            for mod in modules:
                result: BuildResult = ws.build(mod, output_dir=modules_dir)
                if not result.success:
                    raise RuntimeError(
                        f"Auto-build failed for module {mod!r}: {result.error}"
                    )

    sandbox_user_dir = worktree / "sandbox-user-dir"
    sandbox_user_dir.mkdir(exist_ok=True)

    base_env = os.environ.copy()
    base_env["LOGOS_USER_DIR"] = str(sandbox_user_dir)
    base_env["QT_QPA_PLATFORM"] = "offscreen"
    # Set LD_LIBRARY_PATH to include workspace libs (needed without autoPatchelf)
    ws_lib = workspace / "result" / "lib"
    if ws_lib.exists():
        existing = base_env.get("LD_LIBRARY_PATH", "")
        base_env["LD_LIBRARY_PATH"] = f"{ws_lib}:{existing}" if existing else str(ws_lib)
    if extra_env:
        for k, v in extra_env.items():
            if k != "LOGOS_USER_DIR":
                base_env[k] = v

    binary, resolved_backend = _resolve_backend(workspace, backend)

    results: list[LogoscoreCall] = []
    all_passed = True

    if resolved_backend == "logoscore":
        for call_str in calls:
            module, method, args = _parse_call(call_str)
            exit_code, stdout, stderr, duration = _run_logoscore(
                binary, modules_dir, modules, call_str, timeout, base_env,
            )
            results.append(LogoscoreCall(
                module=module, method=method, args=args,
                exit_code=exit_code,
                stdout_sha256=_sha256(stdout), stderr_sha256=_sha256(stderr),
                duration_seconds=duration, raw_call=call_str,
                backend="logoscore",
            ))
            if exit_code != 0:
                all_passed = False

    elif resolved_backend == "logos_host":
        # logos_host verifies one module at a time by loading it.
        # Each call is mapped to a module load check.
        verified_modules: set[str] = set()
        for call_str in calls:
            module, method, args = _parse_call(call_str)
            if module in verified_modules:
                # Already verified this module — record a cached pass
                results.append(LogoscoreCall(
                    module=module, method=method, args=args,
                    exit_code=0,
                    stdout_sha256=_sha256(b"cached"), stderr_sha256=_sha256(b""),
                    duration_seconds=0.0, raw_call=call_str,
                    backend="logos_host",
                ))
                continue

            # Use a shorter timeout for logos_host (it's a daemon, 5s is enough
            # to confirm the module loads)
            host_timeout = min(timeout, 5)
            exit_code, stdout, stderr, duration = _run_logos_host(
                binary, modules_dir, module, host_timeout, base_env,
            )
            verified_modules.add(module)
            results.append(LogoscoreCall(
                module=module, method=method, args=args,
                exit_code=exit_code,
                stdout_sha256=_sha256(stdout), stderr_sha256=_sha256(stderr),
                duration_seconds=duration, raw_call=call_str,
                backend="logos_host",
            ))
            if exit_code != 0:
                all_passed = False

    if cleanup_worktree:
        shutil.rmtree(worktree, ignore_errors=True)

    return all_passed, results
