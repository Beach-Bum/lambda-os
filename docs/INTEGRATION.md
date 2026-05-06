# Integration: Agentix controller-run → agentix-logos verify-logoscore

This document describes precisely how Agentix's `controller-run` wires into `agentix-logos`'s `verify-logoscore` rung. Read [`BRIDGE-SPEC.md`](./BRIDGE-SPEC.md) for the full architecture; this document zooms in on the runtime integration point.

## Event flow

A Logos goal (e.g. "swap chat-legacy for chat-module") flows through these stages:

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. PLAN                                                          │
│    agentix controller-plan --path <workspace> --json             │
│    → Returns: allowed_commands, forbidden_commands,              │
│      safety_boundaries, audit_log_location                       │
├─────────────────────────────────────────────────────────────────┤
│ 2. SNAPSHOT (before)                                             │
│    LogosWorkspace(workspace).extended_source_snapshot()           │
│    → Returns: SourceSnapshot{tracked_diff, untracked_sha256s,    │
│      submodule_shas}                                             │
├─────────────────────────────────────────────────────────────────┤
│ 3. SANDBOX                                                       │
│    agentix controller-run "<goal>" --path <workspace> --execute  │
│    → Creates git worktree under /tmp/agentix-<hash>              │
│    → Applies goal changes inside worktree only                   │
│    → Source workspace untouched                                  │
├─────────────────────────────────────────────────────────────────┤
│ 4. VERIFY (this is the integration point)                        │
│    agentix-logos verify-logoscore                                 │
│      --workspace <workspace>                                     │
│      --worktree <temp-worktree>                                  │
│      --modules <modules-touched>                                 │
│      --call "<module>.healthcheck()"                             │
│      --json                                                      │
│    → Builds modules into worktree/modules-built (auto-build)     │
│    → Runs logoscore with LOGOS_USER_DIR=<worktree>/sandbox-user  │
│    → Returns: (all_passed, [LogoscoreCall, ...])                 │
├─────────────────────────────────────────────────────────────────┤
│ 5. SNAPSHOT (after)                                              │
│    LogosWorkspace(workspace).extended_source_snapshot()           │
│    → Compare with before snapshot via compare_snapshots()        │
│    → Any repos/* SHA drift → source_workspace_mutated            │
├─────────────────────────────────────────────────────────────────┤
│ 6. POLICY CHECK                                                  │
│    check_logos_policy(workspace, proposal_diff, modules_touched,│
│                       before_snapshot, after_snapshot)            │
│    → Checks: metadata, RLN, forbidden paths, flake overrides,   │
│      submodule drift                                             │
│    → Returns: [PolicyViolation, ...]                             │
├─────────────────────────────────────────────────────────────────┤
│ 7. AUDIT                                                         │
│    audit_logos_run(workspace, base_event, modules_touched, ...)  │
│    → Appends one JSONL line to .agentix/audit.jsonl              │
│    → Includes all Logos extensions (see AUDIT-SCHEMA.md)         │
├─────────────────────────────────────────────────────────────────┤
│ 8. PROPOSAL                                                     │
│    If all_passed and no policy violations:                       │
│      Save .agentix/proposals/<date>-<goal-slug>.patch           │
│    If failed:                                                    │
│      No proposal saved; audit records the failure                │
├─────────────────────────────────────────────────────────────────┤
│ 9. STOP                                                          │
│    Controller stops. Human reviews proposal and decides to       │
│    apply or reject.                                              │
└─────────────────────────────────────────────────────────────────┘
```

## JSON event flow — worked example

### Step 2: Before snapshot

```json
{
  "tracked_diff": "",
  "untracked_sha256s": {},
  "submodule_shas": {
    "repos/logos-storage-module": "a1b2c3d4e5f6...",
    "repos/logos-chat-module": "f6e5d4c3b2a1...",
    "repos/logos-chat-legacy-module": "1234abcd..."
  }
}
```

### Step 4: verify-logoscore result

```json
{
  "status": "ok",
  "modules": ["chat_module"],
  "calls": [
    {
      "module": "chat_module",
      "method": "healthcheck",
      "args": [],
      "exit_code": 0,
      "stdout_sha256": "e5f3a2b1...",
      "stderr_sha256": "d41d8cd9...",
      "duration_seconds": 2.31,
      "raw_call": "chat_module.healthcheck()"
    }
  ],
  "passed": true
}
```

### Step 5: Drift report

```json
{
  "has_drift": false,
  "tracked_changed": false,
  "untracked_added": [],
  "untracked_removed": [],
  "untracked_modified": [],
  "submodule_drifts": []
}
```

### Step 6: Policy check result

```json
{
  "violations": []
}
```

### Step 7: Audit event (see [`AUDIT-SCHEMA.md`](./AUDIT-SCHEMA.md))

```json
{
  "timestamp": "2026-05-06T08:14:23.451Z",
  "action": "controller_run",
  "mode": "execute",
  "goal": "swap logos-chat-legacy-module for logos-chat-module",
  "result": "ok",
  "passed": true,
  "source_modified": false,
  "proposal_saved": true,
  "agentix_logos_version": "0.0.1",
  "logos_workspace_commit": "9f2a1bc4...",
  "modules_touched": ["logos-chat-legacy-module", "logos-chat-module"],
  "modules_versions": {
    "logos-chat-legacy-module": ["v0.2.1", null],
    "logos-chat-module": [null, "v0.4.0"]
  },
  "logoscore_calls": [{"...": "see above"}],
  "nix_flake_lock_changed": true,
  "lez_program_pin_changed": false,
  "policy_violations": [],
  "stops_before_apply": true,
  "stops_before_rebuild": true,
  "stops_before_lgs_deploy": true,
  "stops_before_lgs_wallet": true
}
```

## Auto-build integration

When `verify_logoscore` is called without a pre-built `modules_dir`, it automatically invokes `LogosWorkspace.build()` for each module (see [`BRIDGE-SPEC.md § workspace.py`](./BRIDGE-SPEC.md)):

1. Creates `<worktree>/modules-built/` directory
2. Calls `ws.build(module_name, output_dir=modules_built)` per module
3. Runs `nix build .#<module>` with `--out-link` pointing into the worktree
4. If any build fails, raises `RuntimeError` with the error details

Build output **never** goes to the source workspace or user state directories.

## Sandbox isolation

The verify rung enforces isolation through:

- **`LOGOS_USER_DIR`**: Set to `<worktree>/sandbox-user-dir`, preventing any read/write to the user's real basecamp state
- **Build outputs**: Constrained to `<worktree>/modules-built/`, never the source workspace
- **Subprocess timeout**: Each logoscore call respects the `--timeout` parameter (default 120s)
- **No network**: On Linux, optional `unshare`-based network namespace (see [`BRIDGE-SPEC.md § Sandbox isolation`](./BRIDGE-SPEC.md))

## Failure modes

| Failure | Stage | Result |
|---|---|---|
| Build fails | Step 4 (auto-build) | `RuntimeError`, no logoscore calls, audit records failure |
| logoscore returns non-zero | Step 4 (verify) | `passed=false`, all calls still recorded, no proposal saved |
| Submodule drift detected | Step 5 (snapshot) | `source_workspace_mutated`, policy violation |
| Policy violation (deny) | Step 6 | Proposal not saved, violations in audit |
| Timeout | Step 4 | `exit_code=124`, stderr includes timeout message |

## Reference: BRIDGE-SPEC.md sections

- **§ workspace.py**: `LogosWorkspace` class, `build()`, `submodule_snapshot()`
- **§ verify_logoscore.py**: `verify_logoscore()` function, `LogoscoreCall` dataclass
- **§ Source-workspace-untouched extension**: Submodule snapshot before/after
- **§ Sandbox isolation**: `LOGOS_USER_DIR`, network namespace, port allocation
- **§ Audit log file format**: JSONL schema, Phase 3 Codex migration
