"""Extends agentix.policy with the `logos:` block.

Reads <workspace>/.agentix/policy.json and validates a proposal against the
combined Agentix base + Logos rules.

See docs/POLICY-SCHEMA.md for the full schema.
"""

from __future__ import annotations

import dataclasses
import fnmatch
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from agentix_logos.modules import ModuleRef

if TYPE_CHECKING:
    from agentix_logos.workspace import SourceSnapshot


@dataclass
class LogosPolicy:
    require_metadata_json: bool = True
    require_signed_metadata: bool = False
    # Path to the IFT key registry JSON, relative to the workspace root or absolute.
    # Used when require_signed_metadata is True. If None, defaults to looking up
    # examples/key-registry-stub.json in the agentix-logos repo (Phase 2 stub).
    key_registry_path: str | None = None
    require_rln_for_messaging_modules: bool = True
    module_install_via: Literal["ws --auto-local", "manual"] = "ws --auto-local"

    forbid_flake_overrides: list[str] = field(
        default_factory=lambda: ["nixpkgs", "logos-blockchain"]
    )

    forbidden_paths_for_writes: list[str] = field(
        default_factory=lambda: [
            "~/.local/share/logos-basecamp",
            "~/Library/Application Support/LogosBasecamp",
            "logos-workspace/repos/*",
            "/etc/nixos",
        ]
    )
    sandbox_port_range: tuple[int, int] = (13000, 14000)
    redirect_logos_user_dir: bool = True

    forbid_wallet_operations: bool = True
    forbid_live_localnet: bool = True

    lez_programs_pinned: bool = True
    forbid_lez_program_deploy: bool = True

    lip_edits_via_proposal_only: bool = True

    audit_extra_fields_required: list[str] = field(
        default_factory=lambda: [
            "agentix_logos_version",
            "logos_workspace_commit",
            "modules_touched",
            "logoscore_calls",
            "nix_flake_lock_changed",
            "lez_program_pin_changed",
            "stops_before_apply",
            "stops_before_rebuild",
            "stops_before_lgs_deploy",
            "stops_before_lgs_wallet",
        ]
    )

    submodule_snapshot: bool = True
    submodule_drift_is_violation: bool = True

    ignore_paths: list[str] = field(
        default_factory=lambda: [
            "repos/*/result",
            "repos/*/result-*",
            ".direnv",
            ".scaffold/state/*.lock",
            ".scaffold/logs/*",
        ]
    )

    @classmethod
    def from_dict(cls, d: dict) -> LogosPolicy:
        """Create LogosPolicy from a parsed ``logos:`` JSON block.

        Args:
            d: Dict from the ``logos:`` block of policy.json.

        Returns:
            LogosPolicy with fields populated from d, defaults for missing keys.
        """
        # Cast sandbox_port_range from list to tuple
        if "sandbox_port_range" in d and isinstance(d["sandbox_port_range"], list):
            d = {**d, "sandbox_port_range": tuple(d["sandbox_port_range"])}
        valid = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in valid})


@dataclass
class CombinedPolicy:
    denied: list[str] = field(default_factory=list)
    review: list[str] = field(default_factory=list)
    allowed: list[str] = field(default_factory=list)
    logos: LogosPolicy = field(default_factory=LogosPolicy)
    has_logos_block: bool = False


@dataclass
class PolicyViolation:
    rule: str
    severity: Literal["deny", "review"]
    details: str
    module: str | None = None
    path: str | None = None

    def to_dict(self) -> dict:
        """Serialize to a plain dict for JSON output."""
        return dataclasses.asdict(self)


def load_policy(workspace: Path) -> CombinedPolicy | None:
    """Load ``.agentix/policy.json`` from workspace.

    Args:
        workspace: Path to the logos-workspace root.

    Returns:
        CombinedPolicy if file exists, None if missing. A missing file
        is a hard error in the controller path — callers should refuse to run.
    """
    policy_path = workspace / ".agentix" / "policy.json"
    if not policy_path.exists():
        return None

    raw = json.loads(policy_path.read_text())
    logos_block = raw.get("logos") or {}
    has_logos = bool(logos_block)

    return CombinedPolicy(
        denied=list(raw.get("denied") or []),
        review=list(raw.get("review") or []),
        allowed=list(raw.get("allowed") or []),
        logos=LogosPolicy.from_dict(logos_block) if has_logos else LogosPolicy(),
        has_logos_block=has_logos,
    )


def check_logos_policy(
    workspace: Path,
    proposal_diff: str,
    modules_touched: list[ModuleRef],
    before_snapshot: SourceSnapshot | None = None,
    after_snapshot: SourceSnapshot | None = None,
) -> list[PolicyViolation]:
    """Validate a proposal against the Logos policy rules.

    Args:
        workspace: Path to the logos-workspace root.
        proposal_diff: The proposal's git diff content.
        modules_touched: List of ModuleRef involved in the proposal.
        before_snapshot: Source snapshot taken before the worktree run.
        after_snapshot: Source snapshot taken after the worktree run.

    Returns:
        List of PolicyViolation. Empty list means the proposal is clean.
    """
    policy = load_policy(workspace)
    if policy is None:
        return [
            PolicyViolation(
                rule="policy_file_missing",
                severity="deny",
                details=f"{workspace}/.agentix/policy.json not found; controller refuses to run",
            )
        ]

    violations: list[PolicyViolation] = []
    lp = policy.logos

    # Rule: require_metadata_json
    if lp.require_metadata_json:
        for m in modules_touched:
            if m.metadata is None:
                violations.append(
                    PolicyViolation(
                        rule="require_metadata_json",
                        severity="deny",
                        module=m.name,
                        details=f"Module {m.name} has no metadata.json",
                    )
                )

    # Rule: require_signed_metadata
    if lp.require_signed_metadata:
        from agentix_logos.keys import SIGNATURE_FIELD, KeyRegistry

        registry: KeyRegistry | None = None
        registry_path = _resolve_key_registry_path(workspace, lp.key_registry_path)
        registry_load_error: str | None = None
        if registry_path is None or not registry_path.exists():
            registry_load_error = (
                f"key registry not found at {registry_path or '<unset>'}; "
                "set logos.key_registry_path in policy.json or place "
                "examples/key-registry-stub.json on the agentix-logos search path"
            )
        else:
            try:
                registry = KeyRegistry.load(registry_path)
            except (ValueError, OSError) as exc:
                registry_load_error = f"failed to load key registry {registry_path}: {exc}"

        if registry_load_error is not None:
            # Single deny: if the registry is unavailable, the rule cannot
            # be enforced safely. Fail closed with one violation rather
            # than per-module spam.
            violations.append(
                PolicyViolation(
                    rule="require_signed_metadata",
                    severity="deny",
                    details=registry_load_error,
                )
            )
        else:
            assert registry is not None  # for mypy
            for m in modules_touched:
                if m.metadata is None:
                    # require_metadata_json (above) already covers the missing-meta case.
                    continue
                # Read the signature off the *raw* JSON so we sign over the canonical
                # bytes the author committed, not a re-serialised typed view.
                source_dict = m.metadata.raw if m.metadata.raw else m.metadata.to_dict()
                signature = source_dict.get(SIGNATURE_FIELD) or m.metadata.signature
                if not isinstance(signature, str) or not signature:
                    violations.append(
                        PolicyViolation(
                            rule="require_signed_metadata",
                            severity="deny",
                            module=m.name,
                            details=f"Module {m.name} metadata.json missing signature field",
                        )
                    )
                    continue
                if not registry.verify(source_dict, signature):
                    violations.append(
                        PolicyViolation(
                            rule="require_signed_metadata",
                            severity="deny",
                            module=m.name,
                            details=(
                                f"Module {m.name} signature did not verify "
                                "against the IFT key registry"
                            ),
                        )
                    )

    # Rule: require_rln_for_messaging_modules
    if lp.require_rln_for_messaging_modules:
        for m in modules_touched:
            if m.is_messaging_module and not m.has_rln:
                violations.append(
                    PolicyViolation(
                        rule="require_rln_for_messaging_modules",
                        severity="deny",
                        module=m.name,
                        details=f"Module {m.name} declares messaging capability but rln=false",
                    )
                )

        # Cross-reference mix state: if any messaging module is loaded AND
        # mix config exists with rln_enabled=False, deny the proposal.
        messaging_modules = [m for m in modules_touched if m.is_messaging_module]
        if messaging_modules:
            from agentix_logos.mix import parse_mix_config

            try:
                mix_config = parse_mix_config(workspace)
            except ValueError:
                mix_config = None  # malformed mix block; validate_mix_config catches this

            if mix_config is not None and not mix_config.rln_enabled:
                module_names = ", ".join(m.name for m in messaging_modules)
                violations.append(
                    PolicyViolation(
                        rule="require_rln_for_messaging_modules",
                        severity="deny",
                        details=(
                            f"Mix config has rln_enabled=false but messaging modules "
                            f"are loaded ({module_names}); RLN must remain enabled "
                            f"in [mix] when messaging-class modules are present"
                        ),
                    )
                )

    # Rule: forbidden_paths_for_writes (proposal_diff scan)
    if proposal_diff and lp.forbidden_paths_for_writes:
        for forbidden in lp.forbidden_paths_for_writes:
            if forbidden in proposal_diff:  # naive but adequate for Phase 1
                violations.append(
                    PolicyViolation(
                        rule="forbidden_paths_for_writes",
                        severity="deny",
                        path=forbidden,
                        details=f"Proposal touches forbidden path: {forbidden}",
                    )
                )

    # Rule: forbid_flake_overrides (proposal_diff scan for --override-input)
    if proposal_diff and lp.forbid_flake_overrides:
        # Regex handles both `--override-input <name> <value>` and
        # `--override-input<whitespace><name>` patterns
        override_re = re.compile(r"--override-input\s+(\S+)\s*")
        found_overrides = override_re.findall(proposal_diff)
        for override_name in found_overrides:
            if override_name in lp.forbid_flake_overrides:
                violations.append(
                    PolicyViolation(
                        rule="forbid_flake_overrides",
                        severity="deny",
                        details=f"Proposal includes forbidden override: --override-input {override_name}",
                    )
                )

    # Rule: lez_programs_pinned
    if lp.lez_programs_pinned:
        from agentix_logos.lez import (
            compute_program_id_from_source,
            parse_lez_programs_from_scaffold,
        )

        scaffold_path = workspace / "scaffold.toml"
        if scaffold_path.exists():
            try:
                programs = parse_lez_programs_from_scaffold(scaffold_path)
            except ValueError as exc:
                violations.append(
                    PolicyViolation(
                        rule="lez_programs_pinned",
                        severity="deny",
                        details=f"scaffold.toml [lez.programs] is malformed: {exc}",
                    )
                )
                programs = {}

            for prog in programs.values():
                # Missing pin is a deny — operators must commit a captured
                # program_id alongside any LEZ program declaration.
                if prog.program_id is None:
                    violations.append(
                        PolicyViolation(
                            rule="lez_programs_pinned",
                            severity="deny",
                            details=(
                                f"LEZ program {prog.name!r} has no program_id pin in "
                                "scaffold.toml; capture one with "
                                "`lgs deploy --dry-run --json` or hash the compiled artifact"
                            ),
                        )
                    )
                    continue

                # Resolve the path we'll hash. If entry_point is set, use that
                # (typical for compiled LEZ binaries); otherwise hash the whole
                # source directory deterministically.
                target = prog.resolved_entry_point(workspace) or prog.resolved_source(
                    workspace
                )
                if not target.exists():
                    violations.append(
                        PolicyViolation(
                            rule="lez_programs_pinned",
                            severity="deny",
                            details=(
                                f"LEZ program {prog.name!r} source/entry_point not found: "
                                f"{target}"
                            ),
                        )
                    )
                    continue

                try:
                    actual = compute_program_id_from_source(target)
                except OSError as exc:
                    violations.append(
                        PolicyViolation(
                            rule="lez_programs_pinned",
                            severity="deny",
                            details=(
                                f"could not hash LEZ program {prog.name!r} at "
                                f"{target}: {exc}"
                            ),
                        )
                    )
                    continue

                if actual != prog.program_id:
                    violations.append(
                        PolicyViolation(
                            rule="lez_programs_pinned",
                            severity="deny",
                            details=(
                                f"LEZ program {prog.name!r} program_id drift: "
                                f"captured {prog.program_id}, actual {actual}. "
                                "Source has been mutated without updating the pin."
                            ),
                        )
                    )

    # Rule: submodule_drift_is_violation
    if lp.submodule_drift_is_violation and before_snapshot is not None and after_snapshot is not None:
        from agentix_logos.workspace import LogosWorkspace

        report = LogosWorkspace.compare_snapshots(before_snapshot, after_snapshot)
        for drift in report.submodule_drifts:
            # Only flag repos/* paths (Logos submodules)
            if drift.path.startswith("repos/"):
                violations.append(
                    PolicyViolation(
                        rule="submodule_drift_is_violation",
                        severity="deny",
                        path=drift.path,
                        details=(
                            f"Submodule {drift.path} SHA changed: "
                            f"{drift.before_sha} -> {drift.after_sha}"
                        ),
                    )
                )

    return violations


def _resolve_key_registry_path(workspace: Path, configured: str | None) -> Path | None:
    """Resolve the path to the IFT key registry JSON.

    Resolution order:
        1. ``configured`` from policy.json — absolute path used as-is;
           relative path resolved against ``workspace``.
        2. ``examples/key-registry-stub.json`` next to the agentix_logos
           package (the Phase 2 bundled stub).

    Args:
        workspace: Workspace root (target repo, e.g. logos-workspace).
        configured: ``logos.key_registry_path`` from policy.json, if set.

    Returns:
        Path to the registry, or None if neither resolution succeeded.
        The returned path may not exist — caller checks.
    """
    if configured:
        p = Path(configured).expanduser()
        if not p.is_absolute():
            p = (workspace / p).resolve()
        return p

    # Default: bundled stub. agentix_logos package lives next to examples/.
    package_dir = Path(__file__).resolve().parent
    repo_root = package_dir.parent
    candidate = repo_root / "examples" / "key-registry-stub.json"
    if candidate.exists():
        return candidate
    return None


def is_path_ignored(path: str, ignore_globs: list[str]) -> bool:
    """Check if a path should be excluded from snapshot per policy.ignore_paths.

    Args:
        path: Relative path to check.
        ignore_globs: List of glob patterns from policy.ignore_paths.

    Returns:
        True if the path matches any ignore glob.
    """
    return any(fnmatch.fnmatch(path, glob) for glob in ignore_globs)
