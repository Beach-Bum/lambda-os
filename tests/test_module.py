"""Unit tests for agentix_logos_module (P1 / PER-17)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from agentix_logos_module import AgentixLogosModule, ModuleResult
from agentix_logos_module.method_dispatch import dispatch

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_DIR = REPO_ROOT / "agentix_logos_module"


# ─────────────────────────────────────────────────────────────────────
# Module artifacts (metadata.json, flake.nix) — structural validation
# ─────────────────────────────────────────────────────────────────────


def test_metadata_json_is_valid():
    """metadata.json parses, has required fields, capabilities are correct."""
    raw = json.loads((MODULE_DIR / "metadata.json").read_text())
    assert raw["name"] == "agentix_logos_module"
    assert "version" in raw
    assert raw["rln"] is False, "agent-control module is not messaging-class"
    caps = set(raw["capabilities"])
    assert {"agent-control", "audit", "policy"}.issubset(caps), caps
    # No messaging/chat/delivery — we don't want require_rln_for_messaging firing.
    assert not (caps & {"messaging", "chat", "delivery"})
    # Main plugin name follows Logos convention
    assert "main" in raw
    assert "plugin" in raw["main"]


def test_flake_nix_exists_and_has_lib_output():
    """flake.nix declares a packages.lib output (logos-modules contract)."""
    flake = (MODULE_DIR / "flake.nix").read_text()
    assert "inherit lib" in flake or "packages.lib" in flake, "flake must expose lib output"
    assert "metadata.json" in flake, "flake should install metadata.json next to lib"


# ─────────────────────────────────────────────────────────────────────
# AgentixLogosModule._invoke — the subprocess wrapper
# ─────────────────────────────────────────────────────────────────────


def _ok_completed(stdout_dict: dict, returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["agentix-logos"],
        returncode=returncode,
        stdout=json.dumps(stdout_dict),
        stderr="",
    )


def _failed_completed(stderr: str = "boom", returncode: int = 1) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["agentix-logos"],
        returncode=returncode,
        stdout="",
        stderr=stderr,
    )


def test_controller_plan_calls_workspace_status(tmp_path: Path):
    """controller_plan invokes `workspace-status --path X --json` on the CLI."""
    expected = {"git_branch": "main", "git_dirty": False, "submodule_count": 42}
    mod = AgentixLogosModule()
    with patch("subprocess.run", return_value=_ok_completed(expected)) as runner, \
         patch("shutil.which", return_value="/fake/agentix-logos"):
        result = mod.controller_plan(str(tmp_path))
    assert result.ok is True
    assert result.exit_code == 0
    assert result.method == "controller_plan"
    assert result.data == expected
    args = runner.call_args[0][0]
    assert args[0] == "/fake/agentix-logos"
    assert "workspace-status" in args
    assert "--json" in args
    assert str(tmp_path) in args


def test_audit_tail_clamps_lines_and_invokes_tail(tmp_path: Path):
    """audit_tail clamps lines into [0, 1000] and calls `audit tail`."""
    expected = {"events": [{"goal": "x"}], "lines_returned": 1, "total_lines": 1}
    mod = AgentixLogosModule()
    with patch("subprocess.run", return_value=_ok_completed(expected)) as runner, \
         patch("shutil.which", return_value="/fake/agentix-logos"):
        # Lines clamped to 1000 even though caller passed 9999
        result = mod.audit_tail(str(tmp_path), lines=9999)
    assert result.ok is True
    assert result.method == "audit_tail"
    args = runner.call_args[0][0]
    assert "audit" in args and "tail" in args
    # The clamp value should appear in the argv
    assert "1000" in args


def test_policy_check_propagates_violations(tmp_path: Path):
    """policy_check returns the CLI's structured violations payload."""
    cli_payload = {
        "policy_loaded": True,
        "logos_block_present": True,
        "violations": [
            {"rule": "require_rln_for_messaging_modules", "severity": "deny",
             "module": "logos-chat-legacy", "details": "..."}
        ],
    }
    mod = AgentixLogosModule()
    with patch("subprocess.run", return_value=_ok_completed(cli_payload)), \
         patch("shutil.which", return_value="/fake/agentix-logos"):
        result = mod.policy_check(str(tmp_path))
    assert result.ok is True
    assert result.data == cli_payload
    assert result.method == "policy_check"


def test_controller_run_is_dry_run_only_with_envelope(tmp_path: Path):
    """controller_run never invokes the CLI in execute mode; wraps state in dry-run envelope."""
    cli_state = {"git_branch": "main", "git_dirty": False}
    mod = AgentixLogosModule()
    with patch("subprocess.run", return_value=_ok_completed(cli_state)) as runner, \
         patch("shutil.which", return_value="/fake/agentix-logos"):
        result = mod.controller_run("swap chat-legacy for chat", str(tmp_path))
    assert result.ok is True
    assert result.method == "controller_run"
    # Envelope contract:
    assert result.data["mode"] == "dry-run"
    assert result.data["from_module"] is True
    assert result.data["goal"] == "swap chat-legacy for chat"
    assert result.data["workspace"] == str(tmp_path)
    assert result.data["workspace_state"] == cli_state
    assert "execute_hint" in result.data
    # CLI was invoked exactly once (workspace-status), NOT controller-run --execute.
    args = runner.call_args[0][0]
    assert "workspace-status" in args
    assert "--execute" not in args


def test_invoke_handles_nonzero_exit(tmp_path: Path):
    """Non-zero CLI exit → ok=False with stderr_excerpt populated."""
    mod = AgentixLogosModule()
    with patch("subprocess.run", return_value=_failed_completed("workspace not found", returncode=1)), \
         patch("shutil.which", return_value="/fake/agentix-logos"):
        result = mod.controller_plan(str(tmp_path))
    assert result.ok is False
    assert result.exit_code == 1
    assert result.data == {}
    assert "workspace not found" in result.stderr_excerpt


def test_invoke_handles_invalid_json(tmp_path: Path):
    """CLI returns garbage stdout → ok=False with parse error in stderr_excerpt."""
    mod = AgentixLogosModule()
    bad_proc = subprocess.CompletedProcess(
        args=["agentix-logos"],
        returncode=0,
        stdout="this is not json {",
        stderr="",
    )
    with patch("subprocess.run", return_value=bad_proc), \
         patch("shutil.which", return_value="/fake/agentix-logos"):
        result = mod.controller_plan(str(tmp_path))
    assert result.ok is False
    assert "non-JSON" in result.stderr_excerpt


def test_invoke_handles_timeout(tmp_path: Path):
    """Subprocess timeout → ok=False with exit_code 124."""
    mod = AgentixLogosModule(timeout_seconds=1)
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="x", timeout=1)), \
         patch("shutil.which", return_value="/fake/agentix-logos"):
        result = mod.controller_plan(str(tmp_path))
    assert result.ok is False
    assert result.exit_code == 124
    assert "timeout" in result.stderr_excerpt


def test_invoke_handles_missing_binary(tmp_path: Path):
    """When agentix-logos is not on PATH, ok=False with exit_code 127."""
    mod = AgentixLogosModule()
    with patch("shutil.which", return_value=None):
        result = mod.controller_plan(str(tmp_path))
    assert result.ok is False
    assert result.exit_code == 127
    assert "not found on PATH" in result.stderr_excerpt


# ─────────────────────────────────────────────────────────────────────
# method_dispatch — logoscore-style call parsing
# ─────────────────────────────────────────────────────────────────────


def test_dispatch_parses_module_qualified_call(tmp_path: Path):
    """Dispatch handles 'agentix_logos_module.controller_plan(/path)' form."""
    mod = AgentixLogosModule()
    with patch("subprocess.run", return_value=_ok_completed({"git_branch": "main"})), \
         patch("shutil.which", return_value="/fake/agentix-logos"):
        out = dispatch(
            f"agentix_logos_module.controller_plan({tmp_path})",
            module=mod,
        )
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["method"] == "controller_plan"


def test_dispatch_parses_unqualified_call(tmp_path: Path):
    """Dispatch also accepts 'controller_plan(/path)' without module prefix."""
    mod = AgentixLogosModule()
    with patch("subprocess.run", return_value=_ok_completed({"git_branch": "main"})), \
         patch("shutil.which", return_value="/fake/agentix-logos"):
        out = dispatch(f"controller_plan({tmp_path})", module=mod)
    payload = json.loads(out)
    assert payload["method"] == "controller_plan"


def test_dispatch_audit_tail_with_lines_arg(tmp_path: Path):
    """Dispatch coerces the lines argument from string to int."""
    mod = AgentixLogosModule()
    with patch("subprocess.run", return_value=_ok_completed({"events": []})) as runner, \
         patch("shutil.which", return_value="/fake/agentix-logos"):
        dispatch(f"audit_tail({tmp_path}, 25)", module=mod)
    args = runner.call_args[0][0]
    assert "25" in args


def test_dispatch_rejects_unknown_method():
    """Unknown method names raise ValueError."""
    with pytest.raises(ValueError, match="unknown method"):
        dispatch("agentix_logos_module.do_something_evil(/x)")


def test_dispatch_rejects_wrong_arity():
    """Wrong argument count raises ValueError."""
    with pytest.raises(ValueError, match="takes 1 argument"):
        dispatch("controller_plan()")
    with pytest.raises(ValueError, match="takes 2 arguments"):
        dispatch("controller_run(only-one-arg)")


def test_dispatch_rejects_malformed_call():
    """Malformed strings raise ValueError."""
    with pytest.raises(ValueError, match="malformed method call"):
        dispatch("not_a_call_at_all")
    with pytest.raises(ValueError, match="malformed method call"):
        dispatch("controller_plan(/x")  # unclosed paren


# ─────────────────────────────────────────────────────────────────────
# ModuleResult shape
# ─────────────────────────────────────────────────────────────────────


def test_module_result_to_dict_is_json_safe():
    """ModuleResult.to_dict() round-trips through json.dumps without error."""
    r = ModuleResult(
        ok=True,
        exit_code=0,
        data={"x": 1},
        method="controller_plan",
        cli_args=["agentix-logos", "workspace-status"],
        stderr_excerpt="",
    )
    encoded = json.dumps(r.to_dict())
    decoded = json.loads(encoded)
    assert decoded["ok"] is True
    assert decoded["data"] == {"x": 1}
