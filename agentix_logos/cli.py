"""agentix-logos CLI entry point.

Implements:
    agentix-logos workspace-status   --path PATH [--json]
    agentix-logos modules list       --path PATH [--json]
    agentix-logos modules describe   <name> --path PATH [--json]
    agentix-logos policy-check       --path PATH [--json]
    agentix-logos verify-logoscore   --workspace PATH --modules m1,m2 \
                                     --call "m1.method()" [--call ...] \
                                     [--worktree PATH] [--timeout N] [--json]
    agentix-logos audit show         <line-number> --path PATH [--json]

All commands are read-only or sandboxed. None mutate the source workspace.
None invoke `agentix apply-verify`, `nixos-rebuild switch`, `lgs deploy`, or
`lgs wallet`.

See docs/CLAUDE-LOGOS-CONTROLLER.md for the full safety contract.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agentix_logos import __version__
from agentix_logos.policy import check_logos_policy, load_policy
from agentix_logos.verify_logoscore import verify_logoscore
from agentix_logos.workspace import LogosWorkspace


def _validate_workspace_path(path: Path) -> dict | None:
    """Validate a workspace path exists and is a git repo.

    Returns None if valid, or an error dict if invalid.
    """
    if not path.exists():
        return {
            "error": "workspace_not_found",
            "path": str(path),
            "hint": (
                f"Path {path} does not exist. "
                "See RUNBOOK.md Day 1 to clone logos-workspace: "
                "git clone --recurse-submodules git@github.com:logos-co/logos-workspace.git"
            ),
        }
    if not (path / ".git").exists():
        return {
            "error": "not_a_git_repo",
            "path": str(path),
            "hint": (
                f"Path {path} exists but is not a git repository. "
                "See RUNBOOK.md Day 1 for setup instructions."
            ),
        }
    return None


def _print(payload: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, sort_keys=True, indent=2))
    else:
        print(json.dumps(payload, sort_keys=True, indent=2))  # always JSON for now


def cmd_workspace_status(args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser().resolve()
    if err := _validate_workspace_path(path):
        _print(err, args.json)
        return 1
    ws = LogosWorkspace(path)
    _print(ws.status(), args.json)
    return 0


def cmd_modules_list(args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser().resolve()
    if err := _validate_workspace_path(path):
        _print(err, args.json)
        return 1
    ws = LogosWorkspace(path)
    modules = [m.to_dict() for m in ws.list_modules()]
    _print({"modules": modules, "count": len(modules)}, args.json)
    return 0


def cmd_modules_describe(args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser().resolve()
    if err := _validate_workspace_path(path):
        _print(err, args.json)
        return 1
    ws = LogosWorkspace(path)
    mod = ws.describe_module(args.name)
    if mod is None:
        _print({"error": "module_not_found", "name": args.name}, args.json)
        return 1
    _print(mod.to_dict(), args.json)
    return 0


def cmd_policy_check(args: argparse.Namespace) -> int:
    workspace = Path(args.path).expanduser().resolve()
    if err := _validate_workspace_path(workspace):
        _print(err, args.json)
        return 1
    policy = load_policy(workspace)
    if policy is None:
        _print({"error": "policy_not_found", "path": str(workspace / ".agentix/policy.json")}, args.json)
        return 1
    violations = check_logos_policy(workspace, proposal_diff="", modules_touched=[])
    _print(
        {
            "policy_loaded": True,
            "logos_block_present": policy.has_logos_block,
            "violations": [v.to_dict() for v in violations],
        },
        args.json,
    )
    return 0 if not violations else 2


def cmd_verify_logoscore(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).expanduser().resolve()
    if err := _validate_workspace_path(workspace):
        _print(err, args.json)
        return 1
    worktree = Path(args.worktree).expanduser().resolve() if args.worktree else None
    modules_dir = Path(args.modules_dir).expanduser().resolve() if args.modules_dir else None

    modules = [m.strip() for m in args.modules.split(",") if m.strip()]
    calls = list(args.call or [])

    passed, results = verify_logoscore(
        workspace=workspace,
        worktree=worktree,
        modules=modules,
        calls=calls,
        timeout=args.timeout,
        modules_dir=modules_dir,
        backend=args.backend,
    )

    payload = {
        "status": "ok" if passed else "failed",
        "modules": modules,
        "calls": [c.to_dict() for c in results],
        "passed": passed,
        "backend": results[0].backend if results else args.backend,
    }
    _print(payload, args.json)
    return 0 if passed else 3


def cmd_snapshot(args: argparse.Namespace) -> int:
    workspace = Path(args.path).expanduser().resolve()
    if err := _validate_workspace_path(workspace):
        _print(err, args.json)
        return 1
    ws = LogosWorkspace(workspace)
    snap = ws.extended_source_snapshot()
    payload = {
        "tracked_diff_empty": snap.tracked_diff == "",
        "untracked_files": len(snap.untracked_sha256s),
        "submodule_count": len(snap.submodule_shas),
        "submodule_shas": snap.submodule_shas,
    }
    _print(payload, args.json)
    return 0


def cmd_audit_tail(args: argparse.Namespace) -> int:
    workspace = Path(args.path).expanduser().resolve()
    audit_path = workspace / ".agentix" / "audit.jsonl"
    if not audit_path.exists():
        _print({"error": "audit_log_not_found", "path": str(audit_path)}, args.json)
        return 1

    raw = audit_path.read_text(encoding="utf-8").strip()
    lines_all = raw.splitlines() if raw else []
    n = max(0, args.lines)
    tail = lines_all[-n:] if n > 0 else []
    events: list[dict] = []
    for line in tail:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            # Partial / corrupt line — surface but don't crash.
            events.append({"_parse_error": True, "_raw": line})
    payload = {
        "lines_returned": len(events),
        "total_lines": len(lines_all),
        "events": events,
    }
    _print(payload, args.json)
    return 0


def cmd_audit_show(args: argparse.Namespace) -> int:
    workspace = Path(args.path).expanduser().resolve()
    audit_path = workspace / ".agentix" / "audit.jsonl"
    if not audit_path.exists():
        _print({"error": "audit_log_not_found", "path": str(audit_path)}, args.json)
        return 1

    lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
    line_num = args.line_number
    if line_num < 1 or line_num > len(lines):
        _print(
            {"error": "line_out_of_range", "line": line_num, "total_lines": len(lines)},
            args.json,
        )
        return 1

    event = json.loads(lines[line_num - 1])

    # Extract and format Logos-specific extensions
    logos_fields = {
        "modules_touched": event.get("modules_touched"),
        "modules_versions": event.get("modules_versions"),
        "logoscore_calls": event.get("logoscore_calls"),
        "policy_violations": event.get("policy_violations"),
        "nix_flake_lock_changed": event.get("nix_flake_lock_changed"),
        "lez_program_pin_changed": event.get("lez_program_pin_changed"),
        "submodule_drift": event.get("submodule_drift"),
        "build_targets": event.get("build_targets"),
        "build_overrides": event.get("build_overrides"),
        "sandbox_user_dir": event.get("sandbox_user_dir"),
        "sandbox_localnet_port": event.get("sandbox_localnet_port"),
        "stops_before_apply": event.get("stops_before_apply"),
        "stops_before_rebuild": event.get("stops_before_rebuild"),
        "stops_before_lgs_deploy": event.get("stops_before_lgs_deploy"),
        "stops_before_lgs_wallet": event.get("stops_before_lgs_wallet"),
    }
    # Strip None values for cleaner output
    logos_fields = {k: v for k, v in logos_fields.items() if v is not None}

    payload = {
        "line": line_num,
        "total_lines": len(lines),
        "event": event,
        "logos_extensions": logos_fields,
    }
    _print(payload, args.json)
    return 0


def cmd_audit_verify(args: argparse.Namespace) -> int:
    from agentix_logos.audit_chain import verify_chain

    workspace = Path(args.path).expanduser().resolve()
    audit_path = workspace / ".agentix" / "audit.jsonl"
    result = verify_chain(audit_path)
    _print(result.to_dict(), args.json)
    return 0 if result.valid else 1


def cmd_audit_chain(args: argparse.Namespace) -> int:
    from agentix_logos.audit_chain import build_chain, save_chain_sidecar

    workspace = Path(args.path).expanduser().resolve()
    audit_path = workspace / ".agentix" / "audit.jsonl"
    chain = build_chain(audit_path)

    sidecar_path = workspace / ".agentix" / "audit-chain.jsonl"
    save_chain_sidecar(chain, sidecar_path)

    _print({
        "entries": len(chain),
        "sidecar": str(sidecar_path),
        "genesis_cid": chain[0].cid if chain else None,
        "head_cid": chain[-1].cid if chain else None,
        "head_sequence": chain[-1].sequence_number if chain else None,
    }, args.json)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agentix-logos", description="Agent control layer for Logos")
    p.add_argument("--version", action="version", version=f"agentix-logos {__version__}")

    sub = p.add_subparsers(dest="command", required=True)

    ws = sub.add_parser("workspace-status", help="Read logos-workspace state")
    ws.add_argument("--path", required=True, help="Path to logos-workspace")
    ws.add_argument("--json", action="store_true", default=True)
    ws.set_defaults(func=cmd_workspace_status)

    mods = sub.add_parser("modules", help="Inspect Logos modules")
    mods_sub = mods.add_subparsers(dest="modules_command", required=True)

    mods_list = mods_sub.add_parser("list", help="List captured modules")
    mods_list.add_argument("--path", required=True)
    mods_list.add_argument("--json", action="store_true", default=True)
    mods_list.set_defaults(func=cmd_modules_list)

    mods_desc = mods_sub.add_parser("describe", help="Describe a single module")
    mods_desc.add_argument("name")
    mods_desc.add_argument("--path", required=True)
    mods_desc.add_argument("--json", action="store_true", default=True)
    mods_desc.set_defaults(func=cmd_modules_describe)

    policy = sub.add_parser("policy-check", help="Validate logos: policy block")
    policy.add_argument("--path", required=True)
    policy.add_argument("--json", action="store_true", default=True)
    policy.set_defaults(func=cmd_policy_check)

    verify = sub.add_parser("verify-logoscore", help="Verify modules load in a sandbox")
    verify.add_argument("--workspace", required=True, help="Path to logos-workspace")
    verify.add_argument("--worktree", help="Path to a temp worktree (default: create one)")
    verify.add_argument("--modules", required=True, help="Comma-separated module names")
    verify.add_argument("--modules-dir", help="Path to built modules directory (default: auto-detect)")
    verify.add_argument("--call", action="append", help='Call e.g. "module.method(args)"')
    verify.add_argument("--backend", choices=["auto", "logoscore", "logos_host"], default="auto",
                        help="Verify backend: logoscore CLI, logos_host daemon, or auto-detect (default)")
    verify.add_argument("--timeout", type=int, default=120)
    verify.add_argument("--json", action="store_true", default=True)
    verify.set_defaults(func=cmd_verify_logoscore)

    snap = sub.add_parser("snapshot", help="Capture source snapshot (submodules, tracked, untracked)")
    snap.add_argument("--path", required=True, help="Path to logos-workspace")
    snap.add_argument("--json", action="store_true", default=True)
    snap.set_defaults(func=cmd_snapshot)

    audit = sub.add_parser("audit", help="Inspect audit log")
    audit_sub = audit.add_subparsers(dest="audit_command", required=True)

    audit_show = audit_sub.add_parser("show", help="Pretty-print a single audit event")
    audit_show.add_argument("line_number", type=int, help="1-indexed line number")
    audit_show.add_argument("--path", required=True, help="Path to workspace")
    audit_show.add_argument("--json", action="store_true", default=True)
    audit_show.set_defaults(func=cmd_audit_show)

    audit_tail = audit_sub.add_parser("tail", help="Return the last N audit events")
    audit_tail.add_argument("--path", required=True, help="Path to workspace")
    audit_tail.add_argument("--lines", type=int, default=10, help="Number of events (default 10)")
    audit_tail.add_argument("--json", action="store_true", default=True)
    audit_tail.set_defaults(func=cmd_audit_tail)

    audit_verify = audit_sub.add_parser("verify", help="Verify audit chain integrity")
    audit_verify.add_argument("--path", required=True, help="Path to workspace")
    audit_verify.add_argument("--json", action="store_true", default=True)
    audit_verify.set_defaults(func=cmd_audit_verify)

    audit_chain = audit_sub.add_parser("chain", help="Build and save the hash chain sidecar")
    audit_chain.add_argument("--path", required=True, help="Path to workspace")
    audit_chain.add_argument("--json", action="store_true", default=True)
    audit_chain.set_defaults(func=cmd_audit_chain)

    # ── Dashboard ──
    dash = sub.add_parser("dashboard", help="Node health history and status overview")
    dash.add_argument("--path", required=True, help="Path to workspace")
    dash.add_argument("--json", action="store_true", default=False)
    dash.set_defaults(func=cmd_dashboard)

    # ── Upgrade plan ──
    uplan = sub.add_parser("upgrade-plan", help="Show dependency-ordered upgrade plan")
    uplan.add_argument("--path", required=True, help="Path to workspace")
    uplan.add_argument("--json", action="store_true", default=False)
    uplan.set_defaults(func=cmd_upgrade_plan)

    # ── Governance commands ──
    gov = sub.add_parser("governance", help="Manage proposals and governance")
    gov_sub = gov.add_subparsers(dest="governance_command", required=True)

    gov_list = gov_sub.add_parser("list", help="List proposals (all or by state)")
    gov_list.add_argument("--path", required=True, help="Path to workspace")
    gov_list.add_argument("--state", choices=["pending", "approved", "rejected", "applied", "failed", "all"],
                          default="all")
    gov_list.add_argument("--json", action="store_true", default=True)
    gov_list.set_defaults(func=cmd_governance_list)

    gov_approve = gov_sub.add_parser("approve", help="Approve a pending proposal")
    gov_approve.add_argument("proposal_id", help="Proposal ID to approve")
    gov_approve.add_argument("--path", required=True, help="Path to workspace")
    gov_approve.add_argument("--json", action="store_true", default=True)
    gov_approve.set_defaults(func=cmd_governance_approve)

    gov_reject = gov_sub.add_parser("reject", help="Reject a pending proposal")
    gov_reject.add_argument("proposal_id", help="Proposal ID to reject")
    gov_reject.add_argument("--reason", default="", help="Rejection reason")
    gov_reject.add_argument("--path", required=True, help="Path to workspace")
    gov_reject.add_argument("--json", action="store_true", default=True)
    gov_reject.set_defaults(func=cmd_governance_reject)

    gov_apply = gov_sub.add_parser("apply", help="Apply an approved proposal (shows command)")
    gov_apply.add_argument("proposal_id", help="Proposal ID to apply")
    gov_apply.add_argument("--path", required=True, help="Path to workspace")
    gov_apply.add_argument("--json", action="store_true", default=True)
    gov_apply.set_defaults(func=cmd_governance_apply)

    gov_status = gov_sub.add_parser("status", help="Show node governance status")
    gov_status.add_argument("--path", required=True, help="Path to workspace")
    gov_status.add_argument("--json", action="store_true", default=True)
    gov_status.set_defaults(func=cmd_governance_status)

    return p


def cmd_governance_list(args: argparse.Namespace) -> int:
    from agentix_logos.proposals import load_proposals

    workspace = Path(args.path).expanduser().resolve()
    proposals_dir = workspace / ".agentix" / "proposals"
    proposals = load_proposals(proposals_dir)

    if args.state != "all":
        proposals = [p for p in proposals if p.state == args.state]

    _print({
        "proposals": [p.to_dict() for p in proposals],
        "count": len(proposals),
        "filter": args.state,
    }, args.json)
    return 0


def cmd_governance_approve(args: argparse.Namespace) -> int:
    from agentix_logos.governance import HumanGovernance

    workspace = Path(args.path).expanduser().resolve()
    gov = HumanGovernance(workspace / ".agentix" / "proposals")
    result = gov.approve(args.proposal_id)

    if result is None:
        _print({"error": "proposal_not_found", "id": args.proposal_id}, args.json)
        return 1
    if result.state != "approved":
        _print({"error": "not_pending", "id": args.proposal_id, "state": result.state}, args.json)
        return 1

    _print({
        "approved": True,
        "proposal": result.to_dict(),
        "next_step": f"Apply with: cd {workspace} && git apply .agentix/proposals/{result.id}.patch",
    }, args.json)
    return 0


def cmd_governance_reject(args: argparse.Namespace) -> int:
    from agentix_logos.governance import HumanGovernance

    workspace = Path(args.path).expanduser().resolve()
    gov = HumanGovernance(workspace / ".agentix" / "proposals")
    result = gov.reject(args.proposal_id, reason=args.reason)

    if result is None:
        _print({"error": "proposal_not_found", "id": args.proposal_id}, args.json)
        return 1

    _print({"rejected": True, "proposal": result.to_dict()}, args.json)
    return 0


def cmd_governance_apply(args: argparse.Namespace) -> int:
    from agentix_logos.governance import HumanGovernance

    workspace = Path(args.path).expanduser().resolve()
    gov = HumanGovernance(workspace / ".agentix" / "proposals")
    proposal = gov.check_status(args.proposal_id)

    if proposal is None:
        _print({"error": "proposal_not_found", "id": args.proposal_id}, args.json)
        return 1
    if proposal.state != "approved":
        _print({"error": "not_approved", "id": args.proposal_id, "state": proposal.state}, args.json)
        return 1

    patch_path = workspace / ".agentix" / "proposals" / f"{proposal.id}.patch"
    _print({
        "ready_to_apply": True,
        "proposal": proposal.to_dict(),
        "commands": [
            f"cd {workspace}",
            f"git apply {patch_path}",
            f"git add -A && git commit -m 'Apply proposal: {proposal.id}'",
            "ws build logos-basecamp --auto-local",
        ],
        "warning": "These commands modify the workspace. Review the patch first.",
    }, args.json)
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    from agentix_logos.dashboard import format_dashboard, print_dashboard

    workspace = Path(args.path).expanduser().resolve()
    data = format_dashboard(workspace)
    if args.json:
        _print(data, args.json)
    else:
        print(print_dashboard(data))
    return 0


def cmd_upgrade_plan(args: argparse.Namespace) -> int:
    from agentix_logos.depgraph import (
        build_dep_graph,
        topological_sort,
    )

    workspace = Path(args.path).expanduser().resolve()
    graph = build_dep_graph(workspace)
    levels = topological_sort(graph)

    if args.json:
        _print({
            "graph": {k: v for k, v in graph.items() if v},
            "levels": levels,
            "total_modules": sum(len(lv) for lv in levels),
            "total_levels": len(levels),
        }, args.json)
    else:
        print(f"Dependency graph: {len(graph)} modules")
        for i, level in enumerate(levels):
            print(f"\n  Level {i}: {', '.join(level[:5])}" + (f" +{len(level)-5} more" if len(level) > 5 else ""))
    return 0


def cmd_governance_status(args: argparse.Namespace) -> int:
    from agentix_logos.proposals import load_proposals

    workspace = Path(args.path).expanduser().resolve()
    proposals_dir = workspace / ".agentix" / "proposals"
    proposals = load_proposals(proposals_dir)

    # Read node status
    status_file = workspace / ".agentix" / "node-status.json"
    node_status = {}
    if status_file.exists():
        try:
            node_status = json.loads(status_file.read_text())
        except json.JSONDecodeError:
            pass

    by_state: dict[str, list[str]] = {}
    for p in proposals:
        by_state.setdefault(p.state, []).append(p.module)

    _print({
        "node_healthy": node_status.get("healthy"),
        "last_check": node_status.get("timestamp"),
        "proposals": {state: len(mods) for state, mods in by_state.items()},
        "pending_approvals": [
            {"id": p.id, "module": p.module, "proposed_sha": p.proposed_sha[:12]}
            for p in proposals if p.state == "pending"
        ],
        "governance_backend": "human",
    }, args.json)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
