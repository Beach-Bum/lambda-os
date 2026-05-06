"""Tests for the governance system."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentix_logos.governance import (
    HumanGovernance,
    LEZProgramGovernance,
    MultisigGovernance,
    create_governance,
)
from agentix_logos.proposals import Proposal


def _make_proposal(module: str = "test-module", state: str = "pending") -> Proposal:
    return Proposal(
        id=f"{module}-abc123-20260506T000000",
        module=module,
        current_sha="aaa",
        proposed_sha="bbb",
        patch="diff --git a/x b/x\n",
        state=state,
        created_at="2026-05-06T00:00:00Z",
    )


class TestHumanGovernance:
    def test_submit_saves_proposal(self, tmp_path: Path):
        gov = HumanGovernance(tmp_path)
        p = _make_proposal()
        tracking_id = gov.submit(p)
        assert tracking_id == p.id
        assert (tmp_path / f"{p.id}.json").exists()
        assert (tmp_path / f"{p.id}.patch").exists()

    def test_approve_pending(self, tmp_path: Path):
        gov = HumanGovernance(tmp_path)
        p = _make_proposal()
        gov.submit(p)

        result = gov.approve(p.id, decided_by="ned")
        assert result.state == "approved"
        assert result.governance_decision == "approved"
        assert result.governance_decided_by == "ned"
        assert result.governance_decided_at is not None

    def test_reject_pending(self, tmp_path: Path):
        gov = HumanGovernance(tmp_path)
        p = _make_proposal()
        gov.submit(p)

        result = gov.reject(p.id, reason="too risky")
        assert result.state == "rejected"
        assert result.error == "too risky"

    def test_approve_nonexistent(self, tmp_path: Path):
        gov = HumanGovernance(tmp_path)
        assert gov.approve("nonexistent-id") is None

    def test_list_pending(self, tmp_path: Path):
        gov = HumanGovernance(tmp_path)
        p1 = _make_proposal("mod-a")
        p2 = _make_proposal("mod-b")
        gov.submit(p1)
        gov.submit(p2)
        gov.approve(p1.id)  # No longer pending

        pending = gov.list_pending()
        assert len(pending) == 1
        assert pending[0].module == "mod-b"

    def test_mark_applied(self, tmp_path: Path):
        gov = HumanGovernance(tmp_path)
        p = _make_proposal()
        gov.submit(p)
        gov.approve(p.id)

        result = gov.mark_applied(p.id)
        assert result.state == "applied"
        assert result.applied_at is not None

    def test_mark_applied_rejects_non_approved(self, tmp_path: Path):
        gov = HumanGovernance(tmp_path)
        p = _make_proposal()
        gov.submit(p)
        # Still pending — can't apply
        result = gov.mark_applied(p.id)
        assert result.state == "pending"

    def test_check_status(self, tmp_path: Path):
        gov = HumanGovernance(tmp_path)
        p = _make_proposal()
        gov.submit(p)

        status = gov.check_status(p.id)
        assert status is not None
        assert status.state == "pending"
        assert status.module == "test-module"

    def test_check_status_nonexistent(self, tmp_path: Path):
        gov = HumanGovernance(tmp_path)
        assert gov.check_status("nope") is None


class TestGovernanceFactory:
    def test_create_human(self, tmp_path: Path):
        gov = create_governance("human", tmp_path)
        assert isinstance(gov, HumanGovernance)

    def test_create_multisig(self, tmp_path: Path):
        gov = create_governance("multisig", tmp_path)
        assert isinstance(gov, MultisigGovernance)

    def test_create_lez_program(self, tmp_path: Path):
        gov = create_governance("lez-program", tmp_path)
        assert isinstance(gov, LEZProgramGovernance)

    def test_create_unknown_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match="Unknown governance"):
            create_governance("quantum", tmp_path)


class TestMultisigFallback:
    def test_falls_back_to_human(self, tmp_path: Path):
        """Multisig governance falls back to human until LEZ is available."""
        gov = MultisigGovernance(tmp_path)
        p = _make_proposal()
        gov.submit(p)

        pending = gov.list_pending()
        assert len(pending) == 1

        status = gov.check_status(p.id)
        assert status.state == "pending"


class TestLEZProgramFallback:
    def test_falls_back_to_human(self, tmp_path: Path):
        """LEZ program governance falls back to human until deployed."""
        gov = LEZProgramGovernance(tmp_path, program_id="0xdead")
        p = _make_proposal()
        gov.submit(p)

        pending = gov.list_pending()
        assert len(pending) == 1
