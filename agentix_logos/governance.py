"""Governance — the approval layer between proposals and activation.

Three governance backends, selected by policy:

  1. Human (Phase 1): proposals wait in a queue. A human runs
     `agentix-logos governance approve <id>` from the terminal.

  2. Multisig (Phase 3): proposals are submitted to a lez-multisig
     contract. N-of-M signers must approve before the proposal
     transitions to "approved." The daemon polls for signatures.

  3. LEZ Program (Phase 4): policy enforcement is a LEZ program.
     The proposal is submitted as a transaction. The program
     evaluates the proposal against on-chain policy and returns
     approve/reject. Fully autonomous, provably enforced.

All three backends share the same interface: submit a proposal,
check its status, list pending proposals. The daemon doesn't care
which backend is active — it submits and polls.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from agentix_logos.proposals import Proposal, save_proposal

GovernanceBackend = Literal["human", "multisig", "lez-program"]


def _now() -> str:
    return datetime.now(UTC).isoformat()


class GovernanceProvider(ABC):
    """Base class for governance backends."""

    @abstractmethod
    def submit(self, proposal: Proposal) -> str:
        """Submit a proposal for governance review.

        Returns a tracking ID (may be the proposal ID itself for human,
        a tx hash for multisig/LEZ).
        """

    @abstractmethod
    def check_status(self, proposal_id: str) -> Proposal | None:
        """Check the current governance status of a proposal."""

    @abstractmethod
    def list_pending(self) -> list[Proposal]:
        """List all proposals awaiting governance decision."""


class HumanGovernance(GovernanceProvider):
    """Phase 1: human approves from the terminal.

    Proposals are saved to .agentix/proposals/ and wait. The human
    runs `agentix-logos governance approve <id>` to approve, or
    `agentix-logos governance reject <id>` to reject.

    After approval, the human applies:
      cd ~/projects/logos-workspace
      git apply .agentix/proposals/<id>.patch
      ws build logos-basecamp --auto-local
    """

    def __init__(self, proposals_dir: Path):
        self.proposals_dir = proposals_dir

    def submit(self, proposal: Proposal) -> str:
        proposal.state = "pending"
        save_proposal(proposal, self.proposals_dir)
        return proposal.id

    def check_status(self, proposal_id: str) -> Proposal | None:
        manifest = self.proposals_dir / f"{proposal_id}.json"
        if not manifest.exists():
            return None
        data = json.loads(manifest.read_text())
        patch_path = manifest.with_suffix(".patch")
        return Proposal(
            id=data["id"],
            module=data["module"],
            current_sha=data["current_sha"],
            proposed_sha=data["proposed_sha"],
            patch=patch_path.read_text() if patch_path.exists() else "",
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
        )

    def list_pending(self) -> list[Proposal]:
        if not self.proposals_dir.exists():
            return []
        pending = []
        for manifest in sorted(self.proposals_dir.glob("*.json")):
            try:
                data = json.loads(manifest.read_text())
                if data.get("state") == "pending":
                    p = self.check_status(data["id"])
                    if p:
                        pending.append(p)
            except (json.JSONDecodeError, KeyError):
                continue
        return pending

    def approve(self, proposal_id: str, decided_by: str = "human") -> Proposal | None:
        """Approve a pending proposal."""
        proposal = self.check_status(proposal_id)
        if proposal is None or proposal.state != "pending":
            return proposal
        proposal.state = "approved"
        proposal.governance_decision = "approved"
        proposal.governance_decided_at = _now()
        proposal.governance_decided_by = decided_by
        save_proposal(proposal, self.proposals_dir)
        return proposal

    def reject(self, proposal_id: str, decided_by: str = "human", reason: str = "") -> Proposal | None:
        """Reject a pending proposal."""
        proposal = self.check_status(proposal_id)
        if proposal is None or proposal.state != "pending":
            return proposal
        proposal.state = "rejected"
        proposal.governance_decision = "rejected"
        proposal.governance_decided_at = _now()
        proposal.governance_decided_by = decided_by
        if reason:
            proposal.error = reason
        save_proposal(proposal, self.proposals_dir)
        return proposal

    def mark_applied(self, proposal_id: str) -> Proposal | None:
        """Mark an approved proposal as applied."""
        proposal = self.check_status(proposal_id)
        if proposal is None or proposal.state != "approved":
            return proposal
        proposal.state = "applied"
        proposal.applied_at = _now()
        save_proposal(proposal, self.proposals_dir)
        return proposal


class MultisigGovernance(GovernanceProvider):
    """Phase 3: lez-multisig approval.

    Proposals are submitted as LEZ transactions. N-of-M signers
    must approve. The daemon polls for signature count.

    Not yet implemented — this is the interface contract for when
    LEZ multisig is available.
    """

    def __init__(self, proposals_dir: Path, multisig_config: dict | None = None):
        self.proposals_dir = proposals_dir
        self.config = multisig_config or {}
        self._human_fallback = HumanGovernance(proposals_dir)

    def submit(self, proposal: Proposal) -> str:
        # Phase 3: submit to LEZ multisig contract
        # For now, fall back to human governance
        return self._human_fallback.submit(proposal)

    def check_status(self, proposal_id: str) -> Proposal | None:
        # Phase 3: query LEZ for signature count
        return self._human_fallback.check_status(proposal_id)

    def list_pending(self) -> list[Proposal]:
        # Phase 3: query LEZ for pending proposals
        return self._human_fallback.list_pending()


class LEZProgramGovernance(GovernanceProvider):
    """Phase 4: policy as a LEZ program.

    The proposal is submitted as a LEZ transaction. A deployed LEZ
    program evaluates it against on-chain policy rules and returns
    approve/reject. Fully autonomous — no human in the loop.

    The program is the governance. The code is the law.

    Not yet implemented — this is the interface contract.
    """

    def __init__(self, proposals_dir: Path, program_id: str | None = None):
        self.proposals_dir = proposals_dir
        self.program_id = program_id
        self._human_fallback = HumanGovernance(proposals_dir)

    def submit(self, proposal: Proposal) -> str:
        # Phase 4: submit to LEZ program for automated evaluation
        return self._human_fallback.submit(proposal)

    def check_status(self, proposal_id: str) -> Proposal | None:
        return self._human_fallback.check_status(proposal_id)

    def list_pending(self) -> list[Proposal]:
        return self._human_fallback.list_pending()


def create_governance(backend: GovernanceBackend, proposals_dir: Path, **kwargs) -> GovernanceProvider:
    """Factory for governance backends."""
    if backend == "human":
        return HumanGovernance(proposals_dir)
    elif backend == "multisig":
        return MultisigGovernance(proposals_dir, kwargs.get("multisig_config"))
    elif backend == "lez-program":
        return LEZProgramGovernance(proposals_dir, kwargs.get("program_id"))
    else:
        raise ValueError(f"Unknown governance backend: {backend!r}")
