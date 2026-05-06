"""Agentix control plane daemon.

Runs as a systemd service, continuously monitoring the Logos node:
  - Periodic health checks (module load verification)
  - Source snapshot integrity monitoring
  - Policy enforcement
  - Upgrade detection (new module versions available)
  - Proposal generation for detected upgrades
  - Audit trail for every action

The daemon never activates changes. It proposes them. Governance
(human / lez-multisig / LEZ program) approves activation.
"""

from __future__ import annotations

import json
import signal
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from agentix_logos.depgraph import order_upgrades
from agentix_logos.governance import create_governance
from agentix_logos.notify import NotifyConfig, notify_degraded, notify_health_check
from agentix_logos.policy import check_logos_policy, load_policy
from agentix_logos.proposals import generate_proposal, load_proposals, save_proposal
from agentix_logos.selfheal import auto_heal, detect_regressions
from agentix_logos.verify_logoscore import verify_logoscore
from agentix_logos.workspace import LogosWorkspace, SourceSnapshot


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _log(level: str, msg: str) -> None:
    print(f"[{_now()}] [{level}] {msg}", flush=True)


class AgentixDaemon:
    """The Agentix control plane daemon.

    Watches a Logos workspace, verifies module health, detects available
    upgrades, and generates proposals. Never activates — only proposes.
    """

    def __init__(
        self,
        workspace_path: Path,
        check_interval: int = 300,
        modules_dir: Path | None = None,
    ):
        self.workspace_path = workspace_path
        self.ws = LogosWorkspace(workspace_path)
        self.check_interval = check_interval
        self.modules_dir = modules_dir or workspace_path / "result" / "modules"
        self.running = True
        self.cycle_count = 0
        self.last_snapshot: SourceSnapshot | None = None
        self.proposals_dir = workspace_path / ".agentix" / "proposals"
        self.governance = create_governance("human", self.proposals_dir)
        self.proposed_upgrades: set[str] = set()  # "module:sha" keys already proposed
        self.notify_config = NotifyConfig.from_env()
        self.was_healthy = True
        self.previous_module_results: dict[str, dict] = {}

        # Load existing proposals to avoid re-proposing
        for p in load_proposals(self.proposals_dir):
            if p.state in ("pending", "approved", "applied"):
                self.proposed_upgrades.add(f"{p.module}:{p.proposed_sha}")

        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

    def _handle_signal(self, signum: int, frame) -> None:
        _log("INFO", f"Received signal {signum}, shutting down gracefully")
        self.running = False

    def run(self) -> None:
        """Main daemon loop."""
        _log("INFO", f"Agentix daemon starting — workspace: {self.workspace_path}")
        _log("INFO", f"Check interval: {self.check_interval}s")
        _log("INFO", f"Modules dir: {self.modules_dir}")

        while self.running:
            self.cycle_count += 1
            _log("INFO", f"=== Health check cycle {self.cycle_count} ===")
            try:
                report = self._run_cycle()
                self._log_report(report)
                self._write_status(report)
                self._notify(report)
            except Exception as e:
                _log("ERROR", f"Cycle failed: {e}")

            if self.running:
                _log("INFO", f"Sleeping {self.check_interval}s until next cycle")
                # Sleep in small increments so we can respond to signals
                for _ in range(self.check_interval):
                    if not self.running:
                        break
                    time.sleep(1)

        _log("INFO", f"Agentix daemon stopped after {self.cycle_count} cycles")

    def _run_cycle(self) -> dict:
        """Run one full health check cycle."""
        report: dict = {
            "timestamp": _now(),
            "cycle": self.cycle_count,
            "checks": {},
        }

        # 1. Source snapshot
        _log("INFO", "Taking source snapshot...")
        snapshot = self.ws.extended_source_snapshot()
        report["checks"]["snapshot"] = {
            "submodules": len(snapshot.submodule_shas),
            "tracked_clean": snapshot.tracked_diff == "",
            "untracked_files": len(snapshot.untracked_sha256s),
        }

        # Check for drift since last cycle
        if self.last_snapshot is not None:
            drift = self.ws.compare_snapshots(self.last_snapshot, snapshot)
            if drift.has_drift:
                _log("WARN", "Source drift detected since last cycle!")
                report["checks"]["drift"] = drift.to_dict()
        self.last_snapshot = snapshot

        # 2. Module verification
        _log("INFO", "Verifying modules...")
        module_results: dict[str, dict] = {}
        if self.modules_dir.exists():
            for module_dir in sorted(self.modules_dir.iterdir()):
                if not module_dir.is_dir():
                    continue
                module_name = module_dir.name
                _log("INFO", f"  Verifying: {module_name}")
                try:
                    passed, calls = verify_logoscore(
                        workspace=self.workspace_path,
                        worktree=None,
                        modules=[module_name],
                        calls=[f"{module_name}.load()"],
                        timeout=10,
                        modules_dir=self.modules_dir,
                        backend="logos_host",
                    )
                    module_results[module_name] = {
                        "passed": passed,
                        "exit_code": calls[0].exit_code if calls else -1,
                        "duration": calls[0].duration_seconds if calls else 0,
                    }
                    if not passed:
                        _log("WARN", f"  FAILED: {module_name}")
                except Exception as e:
                    module_results[module_name] = {"passed": False, "error": str(e)}
                    _log("ERROR", f"  ERROR: {module_name}: {e}")

        report["checks"]["modules"] = module_results
        all_modules_ok = all(m.get("passed", False) for m in module_results.values())
        report["checks"]["modules_healthy"] = all_modules_ok

        # 2b. Self-healing — detect regressions and auto-rollback
        if self.previous_module_results:
            regressions = detect_regressions(module_results, self.previous_module_results)
            if regressions:
                _log("WARN", f"  {len(regressions)} module regression(s) detected — auto-healing")
                heal_results = auto_heal(self.workspace_path, regressions, self.notify_config)
                report["checks"]["auto_healed"] = heal_results
                healed_count = sum(1 for r in heal_results if r.get("rolled_back"))
                if healed_count:
                    _log("INFO", f"  {healed_count} module(s) auto-healed via rollback")
        self.previous_module_results = module_results

        # 3. Policy check
        _log("INFO", "Checking policy...")
        policy = load_policy(self.workspace_path)
        if policy is None:
            report["checks"]["policy"] = {"loaded": False, "violations": []}
            _log("WARN", "No policy.json found")
        else:
            violations = check_logos_policy(
                self.workspace_path,
                proposal_diff="",
                modules_touched=[],
                before_snapshot=self.last_snapshot,
                after_snapshot=snapshot,
            )
            report["checks"]["policy"] = {
                "loaded": True,
                "logos_block": policy.has_logos_block,
                "violations": [v.to_dict() for v in violations],
            }
            if violations:
                _log("WARN", f"Policy violations: {len(violations)}")
                for v in violations:
                    _log("WARN", f"  {v.rule}: {v.details}")

        # 4. Upgrade detection
        _log("INFO", "Checking for available upgrades...")
        upgrades = self._detect_upgrades()
        report["checks"]["upgrades"] = upgrades
        if upgrades:
            _log("INFO", f"  {len(upgrades)} upgrades available")
            for u in upgrades:
                _log("INFO", f"  {u['module']}: {u['current'][:8]} -> {u['available'][:8]}")

        # 5. Dependency-ordered upgrade proposals
        if upgrades:
            ordered = order_upgrades(self.workspace_path, upgrades)
            _log("INFO", f"  Upgrade plan: {sum(len(lv) for lv in ordered)} upgrades in {len(ordered)} levels")
            report["checks"]["upgrade_levels"] = len(ordered)
        else:
            ordered = []

        new_proposals = self._generate_proposals_ordered(ordered)
        report["checks"]["proposals_generated"] = [p.to_dict() for p in new_proposals]
        if new_proposals:
            _log("INFO", f"  Generated {len(new_proposals)} new proposals")
            for p in new_proposals:
                _log("INFO", f"  {p.id}: {p.state}")

        # 6. Check governance queue
        pending = self.governance.list_pending()
        report["checks"]["governance_pending"] = len(pending)
        if pending:
            _log("INFO", f"  {len(pending)} proposals awaiting governance approval")
            for p in pending:
                _log("INFO", f"  {p.id} ({p.module}): {p.current_sha[:8]} -> {p.proposed_sha[:8]}")

        # Overall health
        report["healthy"] = all_modules_ok and not report["checks"].get("drift", {}).get("has_drift", False)
        report["issues_count"] = (
            sum(1 for m in module_results.values() if not m.get("passed", False))
            + len(report["checks"].get("policy", {}).get("violations", []))
        )

        return report

    def _notify(self, report: dict) -> None:
        """Send notifications based on the cycle report."""
        healthy = report.get("healthy", False)

        # Notify on health check results
        notify_health_check(self.notify_config, report)

        # Alert on state transition to degraded
        if not healthy and self.was_healthy:
            failed = [
                name for name, info in report["checks"].get("modules", {}).items()
                if not info.get("passed", False)
            ]
            if failed:
                notify_degraded(self.notify_config, failed)

        self.was_healthy = healthy

    def _generate_proposals_ordered(self, ordered_levels: list[list[dict]]) -> list:
        """Generate proposals in dependency order — leaves first."""
        from agentix_logos.proposals import Proposal

        all_proposals: list[Proposal] = []
        for level_idx, level in enumerate(ordered_levels):
            _log("INFO", f"  Proposing level {level_idx} ({len(level)} modules)")
            level_proposals = self._generate_proposals(level)
            all_proposals.extend(level_proposals)

            # If any proposal in this level failed, log it but continue
            # (other levels may still be safe)
            failed = [p for p in level_proposals if p.state == "failed"]
            if failed:
                _log("WARN", f"  Level {level_idx}: {len(failed)} proposals failed")
        return all_proposals

    def _generate_proposals(self, upgrades: list[dict]) -> list:
        """Generate proposals for upgrades not yet proposed."""
        from agentix_logos.proposals import Proposal

        new_proposals: list[Proposal] = []

        for upgrade in upgrades:
            key = f"{upgrade['module']}:{upgrade['available']}"
            if key in self.proposed_upgrades:
                continue  # Already proposed this upgrade

            _log("INFO", f"  Generating proposal: {upgrade['module']} -> {upgrade['available'][:8]}")
            try:
                # Only pass modules_dir if this repo has a built module we can verify.
                # Many repos (SDKs, UIs, tools) aren't loadable modules.
                verifiable_modules_dir = None
                module_plugin_name = upgrade["module"].replace("-", "_")
                candidate = self.modules_dir / module_plugin_name
                if candidate.exists():
                    verifiable_modules_dir = self.modules_dir

                proposal = generate_proposal(
                    workspace=self.workspace_path,
                    module_path=upgrade["path"],
                    module_name=upgrade["module"],
                    current_sha=upgrade["current"],
                    proposed_sha=upgrade["available"],
                    modules_dir=verifiable_modules_dir,
                )

                if proposal.state in ("pending", "verified"):
                    # Submit to governance
                    self.governance.submit(proposal)
                    self.proposed_upgrades.add(key)
                    _log("INFO", f"  Proposal {proposal.id}: submitted to governance ({proposal.state})")
                elif proposal.state == "failed":
                    save_proposal(proposal, self.proposals_dir)
                    self.proposed_upgrades.add(key)  # Don't retry failed proposals
                    _log("WARN", f"  Proposal {proposal.id}: FAILED — {proposal.error}")

                new_proposals.append(proposal)

                # Audit the proposal
                self._audit_proposal(proposal)

            except Exception as e:
                _log("ERROR", f"  Failed to generate proposal for {upgrade['module']}: {e}")

        return new_proposals

    def _audit_proposal(self, proposal) -> None:
        """Write a proposal event to the audit log."""
        audit_line = {
            "timestamp": _now(),
            "action": "proposal_generated",
            "proposal_id": proposal.id,
            "module": proposal.module,
            "current_sha": proposal.current_sha,
            "proposed_sha": proposal.proposed_sha,
            "state": proposal.state,
            "verification_passed": proposal.verification_passed,
            "error": proposal.error,
        }
        audit_path = self.workspace_path / ".agentix" / "audit.jsonl"
        audit_path.parent.mkdir(exist_ok=True)
        with audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(audit_line, sort_keys=True) + "\n")

    def _detect_upgrades(self) -> list[dict]:
        """Check if any submodule has newer commits on its remote."""
        upgrades: list[dict] = []
        snapshot = self.last_snapshot
        if snapshot is None:
            return upgrades

        for path, current_sha in snapshot.submodule_shas.items():
            if not path.startswith("repos/"):
                continue
            module_path = self.workspace_path / path
            if not module_path.exists():
                continue

            # Check if remote has newer commits
            try:
                result = subprocess.run(
                    ["git", "rev-parse", "origin/master"],
                    cwd=module_path,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    remote_sha = result.stdout.strip()
                    if remote_sha != current_sha:
                        upgrades.append({
                            "module": path.removeprefix("repos/"),
                            "path": path,
                            "current": current_sha,
                            "available": remote_sha,
                        })
            except (subprocess.TimeoutExpired, Exception):
                pass

        return upgrades

    def _log_report(self, report: dict) -> None:
        """Log the cycle report summary."""
        healthy = report.get("healthy", False)
        issues = report.get("issues_count", 0)
        modules = report["checks"].get("modules", {})
        upgrades = report["checks"].get("upgrades", [])

        status = "HEALTHY" if healthy else "DEGRADED"
        _log("INFO", f"Node status: {status}")
        _log("INFO", f"  Modules: {len(modules)} checked, {sum(1 for m in modules.values() if m.get('passed'))} healthy")
        _log("INFO", f"  Issues: {issues}")
        _log("INFO", f"  Upgrades available: {len(upgrades)}")

    def _write_status(self, report: dict) -> None:
        """Write current status to a JSON file for external consumption."""
        status_dir = self.workspace_path / ".agentix"
        status_dir.mkdir(exist_ok=True)
        status_file = status_dir / "node-status.json"
        status_file.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")

        # Also append to audit log
        audit_line = {
            "timestamp": report["timestamp"],
            "action": "health_check",
            "cycle": report["cycle"],
            "healthy": report.get("healthy", False),
            "issues_count": report.get("issues_count", 0),
            "modules_checked": len(report["checks"].get("modules", {})),
            "upgrades_available": len(report["checks"].get("upgrades", [])),
        }
        audit_path = status_dir / "audit.jsonl"
        with audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(audit_line, sort_keys=True) + "\n")


def main() -> None:
    """Entry point for the daemon."""
    import os

    workspace = Path(os.environ.get("LOGOS_WORKSPACE", "")).expanduser()
    if not workspace.exists():
        workspace = Path.home() / "projects" / "logos-workspace"

    interval = int(os.environ.get("AGENTIX_CHECK_INTERVAL", "300"))
    modules_dir_str = os.environ.get("AGENTIX_MODULES_DIR", "")
    modules_dir = Path(modules_dir_str) if modules_dir_str else None

    daemon = AgentixDaemon(
        workspace_path=workspace,
        check_interval=interval,
        modules_dir=modules_dir,
    )
    daemon.run()


if __name__ == "__main__":
    main()
