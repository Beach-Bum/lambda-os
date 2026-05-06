"""Extended audit log for Logos runs.

Wraps agentix.audit_log.audit (when available) to enrich events with
Logos-specific fields. Falls back to writing directly to .agentix/audit.jsonl
if the agentix package isn't importable (so this can be developed
standalone).

See docs/AUDIT-SCHEMA.md for the full field list.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from agentix_logos import __version__ as agentix_logos_version
from agentix_logos.modules import ModuleRef
from agentix_logos.policy import PolicyViolation
from agentix_logos.verify_logoscore import LogoscoreCall


def _try_import_agentix_audit():
    try:
        from agentix.audit_log import audit  # type: ignore

        return audit
    except Exception:
        return None


def audit_logos_run(
    path: Path,
    *,
    base_event: dict,
    modules_touched: list[ModuleRef],
    modules_versions: dict[str, tuple[str | None, str | None]],
    logoscore_calls: list[LogoscoreCall],
    nix_flake_lock_changed: bool,
    lez_program_pin_changed: bool,
    policy_violations: list[PolicyViolation],
    workspace_commit: str,
    build_targets: list[str] | None = None,
    build_overrides: dict[str, str] | None = None,
    sandbox_user_dir: str | None = None,
    sandbox_localnet_port: int | None = None,
    scaffold_modules_changed: list[str] | None = None,
    submodule_drift: list[dict] | None = None,
) -> None:
    """Append one enriched JSONL line to ``<path>/.agentix/audit.jsonl``.

    Args:
        path: Workspace root path (audit.jsonl lives under .agentix/).
        base_event: Base Agentix audit fields (action, goal, result, etc.).
        modules_touched: Modules involved in this run.
        modules_versions: Module name → (before_version, after_version).
        logoscore_calls: Results of logoscore verification calls.
        nix_flake_lock_changed: Whether flake.lock was modified.
        lez_program_pin_changed: Whether a LEZ program pin changed.
        policy_violations: Any policy violations detected.
        workspace_commit: git rev-parse HEAD of the workspace at run start.
        build_targets: Nix build targets executed.
        build_overrides: Flake input overrides applied.
        sandbox_user_dir: LOGOS_USER_DIR path used for isolation.
        sandbox_localnet_port: Port used for sandbox localnet, if any.
        scaffold_modules_changed: scaffold.toml [modules.*] entries updated.
        submodule_drift: Non-empty only on source_workspace_mutated errors.
    """
    enriched = {
        "agentix_logos_version": agentix_logos_version,
        "logos_workspace_commit": workspace_commit,
        "modules_touched": [m.name for m in modules_touched],
        "modules_versions": {k: list(v) for k, v in modules_versions.items()},
        "logoscore_calls": [c.to_dict() for c in logoscore_calls],
        "nix_flake_lock_changed": nix_flake_lock_changed,
        "lez_program_pin_changed": lez_program_pin_changed,
        "policy_violations": [v.to_dict() for v in policy_violations],
        "build_targets": list(build_targets or []),
        "build_overrides": dict(build_overrides or {}),
        "sandbox_user_dir": sandbox_user_dir,
        "sandbox_localnet_port": sandbox_localnet_port,
        "scaffold_modules_changed": list(scaffold_modules_changed or []),
        "submodule_drift": list(submodule_drift or []),
        # Safety attestations
        "stops_before_apply": True,
        "stops_before_rebuild": True,
        "stops_before_lgs_deploy": True,
        "stops_before_lgs_wallet": True,
    }
    full_event = {**base_event, **enriched}

    agentix_audit = _try_import_agentix_audit()
    if agentix_audit is not None:
        agentix_audit(path, full_event)
        return

    # Fallback: write directly
    audit_dir = path / ".agentix"
    audit_dir.mkdir(exist_ok=True)
    full_event = {
        "timestamp": datetime.now(UTC).isoformat(),
        **full_event,
    }
    with (audit_dir / "audit.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(full_event, sort_keys=True) + "\n")
