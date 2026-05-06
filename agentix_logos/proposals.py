"""Proposal pipeline — auto-generation and lifecycle management.

When the daemon detects an available upgrade, it:
  1. Creates a git worktree (sandbox)
  2. Pins the module to the new commit
  3. Verifies the new version loads in sandbox
  4. Saves the diff as a .patch file
  5. Writes a proposal manifest (JSON) with full context
  6. Submits the proposal to the governance queue

The proposal never activates. It waits for governance approval.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from agentix_logos.verify_logoscore import verify_logoscore

ProposalState = Literal[
    "created",      # Proposal generated, not yet verified
    "verified",     # Module loads in sandbox
    "failed",       # Verification failed
    "pending",      # Awaiting governance approval
    "approved",     # Governance approved, ready to apply
    "applied",      # Human/system applied the patch
    "rejected",     # Governance rejected
]


@dataclass
class Proposal:
    id: str
    module: str
    current_sha: str
    proposed_sha: str
    patch: str
    state: ProposalState
    created_at: str
    verified_at: str | None = None
    verification_passed: bool = False
    verification_details: dict = field(default_factory=dict)
    governance_decision: str | None = None
    governance_decided_at: str | None = None
    governance_decided_by: str | None = None
    applied_at: str | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "module": self.module,
            "current_sha": self.current_sha,
            "proposed_sha": self.proposed_sha,
            "state": self.state,
            "created_at": self.created_at,
            "verified_at": self.verified_at,
            "verification_passed": self.verification_passed,
            "verification_details": self.verification_details,
            "governance_decision": self.governance_decision,
            "governance_decided_at": self.governance_decided_at,
            "governance_decided_by": self.governance_decided_by,
            "applied_at": self.applied_at,
            "error": self.error,
            "patch_sha256": hashlib.sha256(self.patch.encode()).hexdigest(),
        }


def _now() -> str:
    return datetime.now(UTC).isoformat()


def generate_proposal(
    workspace: Path,
    module_path: str,
    module_name: str,
    current_sha: str,
    proposed_sha: str,
    modules_dir: Path | None = None,
) -> Proposal:
    """Generate a proposal to upgrade a module.

    Creates a worktree, pins the submodule, verifies the new version,
    and saves the patch. Never touches the source workspace.
    """
    proposal_id = f"{module_name}-{proposed_sha[:8]}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"

    proposal = Proposal(
        id=proposal_id,
        module=module_name,
        current_sha=current_sha,
        proposed_sha=proposed_sha,
        patch="",
        state="created",
        created_at=_now(),
    )

    worktree_dir = Path(tempfile.mkdtemp(prefix=f"agentix-proposal-{module_name}-"))

    try:
        # Create worktree from current HEAD
        subprocess.run(
            ["git", "worktree", "add", str(worktree_dir), "HEAD"],
            cwd=workspace,
            capture_output=True,
            check=True,
        )

        # Pin the submodule to the new commit
        subprocess.run(
            ["git", "update-index", "--cacheinfo", f"160000,{proposed_sha},{module_path}"],
            cwd=worktree_dir,
            capture_output=True,
            check=True,
        )

        # Get the diff
        diff_result = subprocess.run(
            ["git", "diff", "--cached"],
            cwd=worktree_dir,
            capture_output=True,
            text=True,
        )
        proposal.patch = diff_result.stdout

        if not proposal.patch.strip():
            proposal.state = "failed"
            proposal.error = "Empty diff — proposed SHA may be identical to current"
            return proposal

        # Verify the module loads with the new version
        if modules_dir and modules_dir.exists():
            try:
                # Use underscored name for the call syntax (logoscore convention)
                call_name = module_name.replace("-", "_")
                passed, calls = verify_logoscore(
                    workspace=workspace,
                    worktree=worktree_dir,
                    modules=[call_name],
                    calls=[f"{call_name}.load()"],
                    timeout=10,
                    modules_dir=modules_dir,
                    backend="logos_host",
                )
                proposal.verified_at = _now()
                proposal.verification_passed = passed
                proposal.verification_details = {
                    "calls": [c.to_dict() for c in calls],
                    "backend": calls[0].backend if calls else "unknown",
                }
                if passed:
                    proposal.state = "verified"
                else:
                    proposal.state = "failed"
                    proposal.error = f"Module verification failed (exit {calls[0].exit_code if calls else '?'})"
            except Exception as e:
                proposal.verified_at = _now()
                proposal.verification_passed = False
                proposal.state = "failed"
                proposal.error = f"Verification error: {e}"
        else:
            # No modules_dir — skip verification, mark as pending without verify
            proposal.state = "verified"
            proposal.verification_details = {"skipped": True, "reason": "modules_dir not available"}

        # If verified, move to pending governance
        if proposal.state == "verified":
            proposal.state = "pending"

    finally:
        # Clean up worktree
        subprocess.run(
            ["git", "worktree", "remove", str(worktree_dir), "--force"],
            cwd=workspace,
            capture_output=True,
        )

    return proposal


def save_proposal(proposal: Proposal, proposals_dir: Path) -> Path:
    """Save a proposal's patch and manifest to disk."""
    proposals_dir.mkdir(parents=True, exist_ok=True)

    # Save the patch
    patch_path = proposals_dir / f"{proposal.id}.patch"
    patch_path.write_text(proposal.patch)

    # Save the manifest
    manifest_path = proposals_dir / f"{proposal.id}.json"
    manifest_path.write_text(json.dumps(proposal.to_dict(), sort_keys=True, indent=2) + "\n")

    return manifest_path


def load_proposals(proposals_dir: Path) -> list[Proposal]:
    """Load all proposals from the proposals directory."""
    if not proposals_dir.exists():
        return []

    proposals = []
    for manifest_path in sorted(proposals_dir.glob("*.json")):
        try:
            data = json.loads(manifest_path.read_text())
            patch_path = manifest_path.with_suffix(".patch")
            patch = patch_path.read_text() if patch_path.exists() else ""
            proposals.append(Proposal(
                id=data["id"],
                module=data["module"],
                current_sha=data["current_sha"],
                proposed_sha=data["proposed_sha"],
                patch=patch,
                state=data["state"],
                created_at=data["created_at"],
                verified_at=data.get("verified_at"),
                verification_passed=data.get("verification_passed", False),
                verification_details=data.get("verification_details", {}),
                governance_decision=data.get("governance_decision"),
                governance_decided_at=data.get("governance_decided_at"),
                governance_decided_by=data.get("governance_decided_by"),
                applied_at=data.get("applied_at"),
                error=data.get("error"),
            ))
        except (json.JSONDecodeError, KeyError):
            continue

    return proposals
