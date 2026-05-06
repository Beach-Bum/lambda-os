"""Reference Python implementation of the agentix-logos Logos module.

Exposes four methods to be invoked from logoscore (or any Logos
module). Each method shells out to the ``agentix-logos`` CLI and
returns the parsed JSON output as a typed :class:`ModuleResult`.

The eventual production shim is a C++/Qt plugin built against
``logos-cpp-sdk`` — its method bodies are byte-identical to this
Python class semantically. See ``docs/MODULE.md`` for the full
contract, including the C++ binding sketch.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


class ModuleError(RuntimeError):
    """Raised when the agentix-logos CLI is missing, returns non-zero,
    or returns invalid JSON. Callers handle as a module-level failure
    (logoscore surfaces this as a method failure to the caller)."""


@dataclass
class ModuleResult:
    """Typed return value for every module method.

    Attributes:
        ok: True if the underlying CLI exited 0 AND returned valid JSON.
        exit_code: CLI process exit code.
        data: Parsed JSON payload from the CLI. Empty dict on failure.
        method: The module method name that produced this result
            (controller_plan, controller_run, audit_tail, policy_check).
        cli_args: The argv that was actually invoked, for audit/debug.
        stderr_excerpt: Last ~2KB of stderr if the call failed; "" otherwise.
    """

    ok: bool
    exit_code: int
    data: dict
    method: str
    cli_args: list[str] = field(default_factory=list)
    stderr_excerpt: str = ""

    def to_dict(self) -> dict:
        """Serialize to a plain dict for JSON output back through logoscore.

        Returns:
            JSON-safe dict suitable for :func:`json.dumps`.
        """
        return dataclasses.asdict(self)


@dataclass
class AgentixLogosModule:
    """Logos module instance.

    Constructed once per module load (logoscore calls the module's
    ``__init__`` equivalent). Method calls are stateless.

    Attributes:
        agentix_logos_bin: Name or path of the agentix-logos executable.
            Defaults to "agentix-logos" (resolved via PATH).
        timeout_seconds: Per-call subprocess timeout. Default 60s; raised
            from the CLI's 30s-ish defaults so module callers don't get
            surprise timeouts on cold-start.
    """

    agentix_logos_bin: str = "agentix-logos"
    timeout_seconds: int = 60

    # ─── Public methods callable from logoscore ──────────────────────

    def controller_plan(self, workspace_path: str) -> ModuleResult:
        """Return the controller's read-only plan for a workspace.

        Wraps ``agentix-logos workspace-status`` — read-only,
        no mutation. The "plan" returned is the workspace state
        that a controller-run would consult: branch, dirty
        submodules, scaffold.toml presence, etc.

        Args:
            workspace_path: Filesystem path to the logos-workspace
                checkout. Must be a string (not Path) so it survives
                the C++ → Python boundary cleanly.

        Returns:
            :class:`ModuleResult` with ``method="controller_plan"``.
            On success, ``data`` is the workspace status dict from
            the CLI. On failure, ``data`` is empty and ``stderr_excerpt``
            contains the CLI's stderr.
        """
        return self._invoke(
            method="controller_plan",
            args=["workspace-status", "--path", workspace_path, "--json"],
        )

    def controller_run(
        self,
        goal: str,
        workspace_path: str,
    ) -> ModuleResult:
        """Plan a controller-run for a goal — DRY-RUN only from the module.

        Module callers MUST NOT actually mutate the workspace from a
        Logos module context. The acceptance contract here is that
        ``controller_run`` from the module surface is dry-run only —
        it returns the structured plan describing what an actual
        ``agentix controller-run --execute`` would do, but never
        invokes ``--execute`` itself.

        Real execution remains a human-only step via the upstream
        ``agentix`` CLI. See ``docs/MODULE.md`` § "Why no execute".

        Args:
            goal: Free-text goal description.
            workspace_path: Filesystem path to the logos-workspace.

        Returns:
            :class:`ModuleResult` with ``method="controller_run"``.
            ``data`` includes ``mode="dry-run"``, ``goal``, the
            workspace plan, and a hint on how a human would execute.
        """
        plan = self._invoke(
            method="controller_run",
            args=["workspace-status", "--path", workspace_path, "--json"],
        )
        if not plan.ok:
            return plan
        # Wrap the raw workspace status in a controller-shaped envelope
        # so callers can distinguish controller_plan from controller_run output.
        plan.data = {
            "mode": "dry-run",
            "from_module": True,
            "goal": goal,
            "workspace": workspace_path,
            "workspace_state": plan.data,
            "execute_hint": (
                "To actually execute this goal, run "
                f"`agentix controller-run \"{goal}\" --path {workspace_path} --execute` "
                "from a human-driven terminal. The module surface is dry-run only "
                "by design."
            ),
        }
        return plan

    def audit_tail(self, workspace_path: str, lines: int = 10) -> ModuleResult:
        """Return the last ``lines`` audit events for the workspace.

        Wraps ``agentix-logos audit tail --path WORKSPACE --lines N``.
        Read-only.

        Args:
            workspace_path: Filesystem path to the logos-workspace.
            lines: Number of trailing events. Default 10. Caller-clamped
                to ``[0, 1000]`` to bound module response size.

        Returns:
            :class:`ModuleResult` with ``method="audit_tail"``.
            On success, ``data`` has ``events: [...]``,
            ``lines_returned``, ``total_lines``.
        """
        n = max(0, min(int(lines), 1000))
        return self._invoke(
            method="audit_tail",
            args=[
                "audit",
                "tail",
                "--path",
                workspace_path,
                "--lines",
                str(n),
                "--json",
            ],
        )

    def policy_check(self, workspace_path: str) -> ModuleResult:
        """Run policy validation against the workspace's policy.json.

        Wraps ``agentix-logos policy-check --path WORKSPACE``. Read-only.

        Args:
            workspace_path: Filesystem path to the logos-workspace.

        Returns:
            :class:`ModuleResult` with ``method="policy_check"``.
            On success, ``data`` includes ``policy_loaded``,
            ``logos_block_present``, ``violations: [...]``.
        """
        return self._invoke(
            method="policy_check",
            args=["policy-check", "--path", workspace_path, "--json"],
        )

    # ─── Internals ───────────────────────────────────────────────────

    def _invoke(self, *, method: str, args: list[str]) -> ModuleResult:
        """Run the agentix-logos CLI with ``args``, parse JSON, return result.

        Args:
            method: Module method name for the returned :class:`ModuleResult`.
            args: argv tail passed after ``agentix-logos``. Should include
                ``--json`` if the subcommand supports it.

        Returns:
            :class:`ModuleResult`. Never raises — failures are surfaced
            via ``ok=False``, ``exit_code``, ``stderr_excerpt``.
        """
        bin_path = self._resolve_bin()
        if bin_path is None:
            return ModuleResult(
                ok=False,
                exit_code=127,
                data={},
                method=method,
                cli_args=[self.agentix_logos_bin, *args],
                stderr_excerpt=(
                    f"agentix-logos binary not found on PATH (looked for "
                    f"{self.agentix_logos_bin!r}). Install with "
                    f"`uv tool install agentix-logos` or set the bin path "
                    "via the module's agentix_logos_bin attribute."
                ),
            )

        argv = [bin_path, *args]
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return ModuleResult(
                ok=False,
                exit_code=124,
                data={},
                method=method,
                cli_args=argv,
                stderr_excerpt=f"timeout after {self.timeout_seconds}s",
            )

        if proc.returncode != 0:
            return ModuleResult(
                ok=False,
                exit_code=proc.returncode,
                data={},
                method=method,
                cli_args=argv,
                stderr_excerpt=proc.stderr.strip()[-2000:],
            )

        stdout = proc.stdout.strip()
        try:
            data = json.loads(stdout) if stdout else {}
            if not isinstance(data, dict):
                # The CLI consistently returns dicts; non-dict (list) is
                # unexpected. Wrap it so the caller still gets structured data.
                data = {"_raw_value": data}
        except json.JSONDecodeError as exc:
            return ModuleResult(
                ok=False,
                exit_code=proc.returncode,
                data={},
                method=method,
                cli_args=argv,
                stderr_excerpt=(
                    f"agentix-logos returned non-JSON output: {exc}. "
                    f"First 500 chars: {stdout[:500]!r}"
                ),
            )

        return ModuleResult(
            ok=True,
            exit_code=proc.returncode,
            data=data,
            method=method,
            cli_args=argv,
            stderr_excerpt="",
        )

    def _resolve_bin(self) -> str | None:
        """Resolve the agentix-logos binary path.

        Returns:
            Absolute path to the binary, or None if not found.
        """
        # If the configured value is already a path that exists, use it.
        candidate = Path(self.agentix_logos_bin).expanduser()
        if candidate.is_file() and candidate.exists():
            return str(candidate.resolve())
        # Otherwise resolve via PATH.
        return shutil.which(self.agentix_logos_bin)
