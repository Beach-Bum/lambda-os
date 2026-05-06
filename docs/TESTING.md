# Testing Strategy

This document describes how `agentix-logos` is tested, what's covered, what's not, and how to add new tests.

## Two test tiers

### Tier 1: Unit tests (mocks, no workspace needed)

**Location:** `tests/test_smoke.py`, `tests/test_t*.py`

These run on any machine without `logos-workspace` installed. They use:
- `tmp_path` fixtures for temporary directories
- `unittest.mock.patch` to mock `subprocess.run` (for `nix build`, `logoscore`, `git`)
- Synthetic `policy.json` and `audit.jsonl` files written to `tmp_path`
- Real git repos created in `tmp_path` (for snapshot tests that need actual `.git`)

**What they cover:**

| Test file | Module | What's tested |
|---|---|---|
| `test_smoke.py` | All | Package import, CLI parser, policy defaults, call parser, module metadata, policy violations |
| `test_t1_verify_build.py` | `verify_logoscore` | Auto-build triggered when modules_dir missing, correct args, modules_dir resolution, failure surfacing, existing dir skips build |
| `test_t2_snapshot.py` | `workspace` | `extended_source_snapshot()` capture (tracked diff, untracked files, submodule SHAs), `compare_snapshots()` drift detection (all drift types), end-to-end with real git repos |
| `test_t3_policy_drift.py` | `policy` | Submodule drift policy enforcement: no drift, repos/* drift, non-repos drift, disabled, no snapshots, multiple drifts |
| `test_t4_flake_override.py` | `policy` | Regex-based `--override-input` scan: no override, forbidden, allowed, multiple, whitespace variants |
| `test_t5_audit_show.py` | `cli` | `audit show` command: valid line, out of range, missing file, multiple lines, None stripping, zero-index rejection |
| `test_t6_error_ux.py` | `cli` | Workspace validation: missing path, not a git repo, error messages include RUNBOOK.md hint |

**Run:** `uv run pytest -q`

### Tier 2: Integration tests (real workspace, Phase 1 manual)

**Location:** `tests/integration/` (not yet created)

These require:
- A cloned `logos-workspace` at `~/projects/logos-workspace`
- A successful `ws build logos-basecamp` (Day 1 of RUNBOOK.md)
- `logoscore` on PATH

They will test:
- Real `nix build` via `LogosWorkspace.build()`
- Real `logoscore` invocations via `verify_logoscore()`
- Real submodule snapshots across ~43 submodules
- Performance benchmarks (target: <2s for full snapshot)

**Run:** `uv run pytest tests/integration/ -q` (after Day 1 setup)

Integration tests are **not** part of CI. They run manually on the Alienware machine where `logos-workspace` is built. CI runs only Tier 1 tests.

## What's not tested yet

| Area | Why | When |
|---|---|---|
| Real `nix build` | Requires logos-workspace clone + Nix | After RUNBOOK Day 1 |
| Real `logoscore` calls | Requires built modules | After RUNBOOK Day 4 |
| `audit_logos_run()` with real Agentix | Agentix not pip-installable yet | Phase 1 integration |
| `LogosWorkspace.graph()` | Requires `ws` script | After RUNBOOK Day 1 |
| `LogosWorkspace.allocate_sandbox_port()` | Trivial; socket binding is OS-level | Low priority |
| Network namespace isolation | Linux-only, requires unshare | Phase 2 |
| `logoscore --deterministic-mode` | Upstream feature not yet available | Pending logos-co PR |
| Codex audit anchoring | Phase 3 | Phase 3 |

## How to add a new test

### Unit test (Tier 1)

1. Create `tests/test_<feature>.py`
2. Import the module under test
3. Use `tmp_path` for filesystem fixtures
4. Mock `subprocess.run` for any external commands
5. Write policy.json to `tmp_path/.agentix/policy.json` if testing policy
6. Run `uv run pytest -q` and `uv run ruff check .`

Example pattern:

```python
from pathlib import Path
from unittest.mock import MagicMock, patch

def test_my_feature(tmp_path: Path):
    # Setup
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / ".git").mkdir()

    # Mock subprocess
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = b"output"

    with patch("subprocess.run", return_value=mock_proc):
        # Call the function
        result = my_function(ws)

    # Assert
    assert result.success is True
```

### Integration test (Tier 2)

1. Create `tests/integration/test_<feature>.py`
2. Use `pytest.mark.skipif` to skip if workspace doesn't exist:
   ```python
   import pytest
   WS = Path.home() / "projects" / "logos-workspace"
   pytestmark = pytest.mark.skipif(not WS.exists(), reason="logos-workspace not cloned")
   ```
3. Use real `LogosWorkspace(WS)` calls
4. Never write to the workspace — only read
5. Clean up any temp dirs

## Test configuration

Tests are configured in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-v --tb=short"
```

CI runs: `uv run pytest -q` (all Tier 1 tests).

## Mocking patterns

### Mock git submodule (for snapshot tests)

Use `protocol.file.allow=always` when creating local submodules in tests:

```python
def _git(path, *args):
    subprocess.run(
        ["git", "-c", "protocol.file.allow=always", *args],
        cwd=path, capture_output=True, text=True,
    )
```

### Mock LogosWorkspace.build() (for verify tests)

```python
def mock_build(self, target, *, override=None, output_dir=None, timeout=1800):
    from agentix_logos.workspace import BuildResult
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
    return BuildResult(target=target, output_path=str(output_dir), ...)
```
