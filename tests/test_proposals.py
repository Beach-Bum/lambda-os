"""Tests for the proposal pipeline."""

from __future__ import annotations

import subprocess
from pathlib import Path

from agentix_logos.proposals import (
    Proposal,
    generate_proposal,
    load_proposals,
    save_proposal,
)


def _init_repo(path: Path) -> Path:
    """Create a minimal git repo."""
    path.mkdir(parents=True, exist_ok=True)
    env = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
    subprocess.run(["git", "init"], cwd=path, capture_output=True, env={**subprocess.os.environ, **env})
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, capture_output=True, env={**subprocess.os.environ, **env})
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, capture_output=True, env={**subprocess.os.environ, **env})
    (path / "README.md").write_text("# test\n")
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True, env={**subprocess.os.environ, **env})
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, capture_output=True, env={**subprocess.os.environ, **env})
    return path


class TestProposalDataclass:
    def test_to_dict(self):
        p = Proposal(
            id="test-123",
            module="logos-storage-module",
            current_sha="aaa",
            proposed_sha="bbb",
            patch="diff --git a/x b/x",
            state="pending",
            created_at="2026-01-01T00:00:00Z",
        )
        d = p.to_dict()
        assert d["id"] == "test-123"
        assert d["state"] == "pending"
        assert "patch_sha256" in d

    def test_to_dict_has_patch_hash(self):
        p = Proposal(id="x", module="m", current_sha="a", proposed_sha="b",
                     patch="hello", state="created", created_at="t")
        d = p.to_dict()
        assert len(d["patch_sha256"]) == 64


class TestSaveAndLoad:
    def test_save_creates_files(self, tmp_path: Path):
        p = Proposal(id="test-save", module="m", current_sha="a", proposed_sha="b",
                     patch="diff content", state="pending", created_at="t")
        save_proposal(p, tmp_path)
        assert (tmp_path / "test-save.patch").exists()
        assert (tmp_path / "test-save.json").exists()

    def test_load_roundtrips(self, tmp_path: Path):
        p = Proposal(id="roundtrip", module="storage", current_sha="aaa",
                     proposed_sha="bbb", patch="diff", state="pending",
                     created_at="2026-01-01T00:00:00Z")
        save_proposal(p, tmp_path)
        loaded = load_proposals(tmp_path)
        assert len(loaded) == 1
        assert loaded[0].id == "roundtrip"
        assert loaded[0].module == "storage"
        assert loaded[0].state == "pending"
        assert loaded[0].patch == "diff"

    def test_load_empty_dir(self, tmp_path: Path):
        assert load_proposals(tmp_path) == []

    def test_load_nonexistent_dir(self, tmp_path: Path):
        assert load_proposals(tmp_path / "nope") == []

    def test_save_creates_dir(self, tmp_path: Path):
        p = Proposal(id="x", module="m", current_sha="a", proposed_sha="b",
                     patch="d", state="pending", created_at="t")
        target = tmp_path / "new" / "dir"
        save_proposal(p, target)
        assert target.exists()


class TestGenerateProposal:
    def test_generates_patch(self, tmp_path: Path):
        """Generate a proposal against a real git repo with a submodule."""
        upstream = _init_repo(tmp_path / "upstream")
        workspace = _init_repo(tmp_path / "ws")

        # Add upstream as a submodule
        env = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
        full_env = {**subprocess.os.environ, **env}
        subprocess.run(
            ["git", "-c", "protocol.file.allow=always", "submodule", "add", str(upstream), "repos/test-mod"],
            cwd=workspace, capture_output=True, env=full_env,
        )
        subprocess.run(["git", "commit", "-m", "add sub"], cwd=workspace, capture_output=True, env=full_env)

        # Make a new commit in upstream
        (upstream / "new.txt").write_text("new")
        subprocess.run(["git", "add", "."], cwd=upstream, capture_output=True, env=full_env)
        subprocess.run(["git", "commit", "-m", "update"], cwd=upstream, capture_output=True, env=full_env)
        new_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=upstream, capture_output=True, text=True, env=full_env,
        ).stdout.strip()

        current_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=workspace / "repos/test-mod",
            capture_output=True, text=True, env=full_env,
        ).stdout.strip()

        proposal = generate_proposal(
            workspace=workspace,
            module_path="repos/test-mod",
            module_name="test-mod",
            current_sha=current_sha,
            proposed_sha=new_sha,
        )

        assert proposal.patch != ""
        assert new_sha in proposal.patch
        assert proposal.state == "pending"
        assert proposal.module == "test-mod"

    def test_empty_diff_fails(self, tmp_path: Path):
        """Pinning to the same SHA produces an empty diff = failed."""
        workspace = _init_repo(tmp_path / "ws")
        env = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
        full_env = {**subprocess.os.environ, **env}

        upstream = _init_repo(tmp_path / "upstream")
        subprocess.run(
            ["git", "-c", "protocol.file.allow=always", "submodule", "add", str(upstream), "repos/mod"],
            cwd=workspace, capture_output=True, env=full_env,
        )
        subprocess.run(["git", "commit", "-m", "add"], cwd=workspace, capture_output=True, env=full_env)

        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=workspace / "repos/mod",
            capture_output=True, text=True, env=full_env,
        ).stdout.strip()

        proposal = generate_proposal(
            workspace=workspace, module_path="repos/mod",
            module_name="mod", current_sha=sha, proposed_sha=sha,
        )
        assert proposal.state == "failed"
        assert "Empty diff" in (proposal.error or "")
