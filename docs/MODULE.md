# Agentix Control Plane Module

The Logos module that exposes the Agentix control plane to the runtime. This is how the OS makes its own management capabilities available to the things running on it — other modules can query workspace state, check policy, verify modules, and read the audit trail, all through standard Logos IPC.

## What this is

The control plane's in-runtime interface. When loaded into `logoscore`, any other module can call Agentix methods: "is this workspace clean?", "does this change violate policy?", "did the last operation pass verification?" This is how Agentix operates Logos from within — the OS introspecting and managing itself.

## Design constraints

- **Read-only / dry-run from the module surface.** The control plane exposes inspection and planning, never mutation. Activation goes through governance (human today, lez-multisig tomorrow), not through module IPC.
- **Same CLI underneath.** The module shells out to `agentix-logos` — same binary, same behavior, same audit trail. The module is an IPC adapter, not a reimplementation.
- **Not a messaging module.** `metadata.json` declares `rln: false` and capabilities `["agent-control", "audit", "policy", "verification"]`. RLN policy doesn't apply.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  logoscore                                                   │
│   │                                                          │
│   │  -c "agentix_logos_module.controller_plan(/path/to/ws)" │
│   ▼                                                          │
│  agentix_logos_module/<lib>                                  │
│   │                                                          │
│   │  Phase 2 (this issue):  Python reference shim            │
│   │  Phase 2 RUNBOOK / 3:   C++/Qt plugin (logos-cpp-sdk)    │
│   │                                                          │
│   ▼                                                          │
│  subprocess.run(["agentix-logos", "workspace-status",        │
│                  "--path", "/path/to/ws", "--json"])         │
│   │                                                          │
│   ▼                                                          │
│  agentix-logos CLI (already shipped from Phase 1)            │
│   │                                                          │
│   ▼                                                          │
│  Returns JSON to caller, wrapped in a ModuleResult envelope. │
└─────────────────────────────────────────────────────────────┘
```

The Python class `AgentixLogosModule` in `agentix_logos_module/agentix_logos_module.py` is the **reference implementation** of the contract. The eventual C++/Qt plugin shim mirrors it method-for-method; both shell out to the same `agentix-logos` binary. Tests in `tests/test_module.py` mock `subprocess.run` and verify the dispatch + envelope behaviour against the Python reference.

When the C++/Qt plugin lands (Phase 2 RUNBOOK / Phase 3), the `flake.nix` here gains a real build phase (against `logos-cpp-sdk`); the Python reference stays as a smoke target and as documentation.

## Methods

All methods return a `ModuleResult` envelope:

```json
{
  "ok": true,
  "exit_code": 0,
  "data": { "...": "..." },
  "method": "controller_plan",
  "cli_args": ["agentix-logos", "workspace-status", "--path", "/...", "--json"],
  "stderr_excerpt": ""
}
```

`ok` is `true` iff the underlying CLI exited 0 *and* returned valid JSON. On failure, `data` is empty and `stderr_excerpt` carries up to 2KB of CLI stderr for debugging.

### `controller_plan(workspace_path)`

Returns the read-only workspace state a controller-run would consult.

**Wraps:** `agentix-logos workspace-status --path WORKSPACE --json`

**Args:**
- `workspace_path` (string): filesystem path to the logos-workspace checkout.

**Result `data`:**
```json
{
  "path": "/Users/x/projects/logos-workspace",
  "git_branch": "main",
  "git_dirty": false,
  "submodule_count": 43,
  "dirty_submodules": [],
  "has_scaffold_toml": true,
  "has_flake_lock": true
}
```

### `controller_run(goal, workspace_path)`

**Dry-run only from the module surface.** Returns the controller's plan envelope describing what an actual `agentix controller-run --execute` would do. Does not invoke the controller. See [§ Why no execute](#why-no-execute).

**Wraps:** `agentix-logos workspace-status --path WORKSPACE --json` (same call as `controller_plan`, wrapped in a controller envelope).

**Args:**
- `goal` (string): the goal text the human would pass to `agentix controller-run`.
- `workspace_path` (string): filesystem path to the logos-workspace.

**Result `data`:**
```json
{
  "mode": "dry-run",
  "from_module": true,
  "goal": "swap chat-legacy for chat",
  "workspace": "/Users/x/projects/logos-workspace",
  "workspace_state": { "...as controller_plan..." },
  "execute_hint": "To actually execute this goal, run `agentix controller-run \"...\" --path ... --execute` from a human-driven terminal. The module surface is dry-run only by design."
}
```

### `audit_tail(workspace_path, lines=10)`

Returns the last N audit events from `<workspace>/.agentix/audit.jsonl`.

**Wraps:** `agentix-logos audit tail --path WORKSPACE --lines N --json`

**Args:**
- `workspace_path` (string): filesystem path to the logos-workspace.
- `lines` (int, default 10): number of trailing events. Clamped to `[0, 1000]` to bound the response size from a module call.

**Result `data`:**
```json
{
  "lines_returned": 10,
  "total_lines": 137,
  "events": [
    { "timestamp": "...", "action": "controller_run", ... },
    "..."
  ]
}
```

### `policy_check(workspace_path)`

Validates the workspace's `.agentix/policy.json` and returns any policy violations the rule engine surfaces.

**Wraps:** `agentix-logos policy-check --path WORKSPACE --json`

**Args:**
- `workspace_path` (string): filesystem path to the logos-workspace.

**Result `data`:**
```json
{
  "policy_loaded": true,
  "logos_block_present": true,
  "violations": [
    {
      "rule": "require_rln_for_messaging_modules",
      "severity": "deny",
      "module": "logos-chat-legacy",
      "details": "Module logos-chat-legacy declares messaging capability but rln=false"
    }
  ]
}
```

## Lifecycle

1. **Build.** `nix build .#lib` from `agentix_logos_module/`. Output is `result/lib/` containing the platform-appropriate library (Phase 2: stub) plus the Python reference + dispatch shim.
2. **Bundle.** `nix bundle --bundler github:logos-co/nix-bundle-lgx github:Beach-Bum/agentix-logos#lib` produces the `.lgx` artifact loadable by basecamp.
3. **Load.** Either through `logos-package-manager-module` (operator opts in via the basecamp UI) or via `logoscore -m <build-dir> -l agentix_logos_module ...`.
4. **Call.** `logoscore -c "agentix_logos_module.controller_plan(/path/to/ws)"` returns JSON. Other Logos modules can call the same methods through the runtime's inter-module dispatch.

## Why no execute

The module surface deliberately omits any mutating operation. Reasoning:

1. **Logos modules run inside the Logos runtime.** A module that could trigger `agentix controller-run --execute` from inside basecamp would let any other module (or any caller) start mutating workspace state without the operator's explicit terminal-side approval.
2. **Agentix's contract is human-final-apply.** From `docs/CLAUDE-LOGOS-CONTROLLER.md`: *"Apply, verify, and activation are human-only."* That contract has to hold from inside Logos too — otherwise the runtime defeats the purpose.
3. **Phase 3 changes the human, not the contract.** When `lez-multisig` becomes the apply gate (Phase 3 design in `docs/CODEX-AUDIT-ANCHOR.md`), execution goes through multisig signature, not through this module. The module remains read-only / dry-run.

`controller_run`'s dry-run envelope returns the plan + an `execute_hint` string telling the caller *exactly* what command a human would run in their terminal. The intent is: a UI or higher-order module can read the plan, surface "Hey, run this command to apply" to the operator, and never escalate privilege.

## Error model

Every method returns a `ModuleResult`. Failures are encoded structurally rather than raised:

| Failure | `ok` | `exit_code` | `stderr_excerpt` |
|---|---|---|---|
| `agentix-logos` not on PATH | `false` | `127` | "binary not found on PATH..." |
| CLI exited non-zero | `false` | propagated | last 2KB of stderr |
| CLI returned non-JSON stdout | `false` | propagated | "non-JSON output: ..." |
| Subprocess timeout | `false` | `124` | `"timeout after Ns"` |
| Successful invocation | `true` | `0` | `""` |

Module dispatch (`method_dispatch.py`) **does** raise `ValueError` for malformed call strings (unknown method names, wrong arity, syntax errors). Logoscore should propagate these to the caller as method-call errors — they indicate a programming error in the caller, not a runtime fault.

## Phase 2 status and limits

This issue ships:
- ✅ Module directory layout matching `logos-modules` conventions
- ✅ `metadata.json` with capabilities, no RLN, platform_main map
- ✅ `flake.nix` with `packages.lib` output (Phase 2 stub installPhase)
- ✅ Python reference implementation of all four methods
- ✅ `method_dispatch.py` parsing logoscore-style `module.method(args)` calls
- ✅ `ModuleResult` envelope with structured failure modes
- ✅ Unit tests with `subprocess.run` mocked (17 tests in `tests/test_module.py`)

Deferred (Phase 2 RUNBOOK or follow-up issue):
- ⏳ C++/Qt plugin compiled against `logos-cpp-sdk`
- ⏳ End-to-end test loading the `.lgx` in basecamp via the Logos package manager
- ⏳ `logoscore -m result/lib -l agentix_logos_module -c "..."` smoke test against a real built artifact (requires logos-workspace per RUNBOOK D1+)

## Cross-reference

- `agentix_logos_module/metadata.json` — module manifest
- `agentix_logos_module/flake.nix` — Nix build (Phase 2 stub)
- `agentix_logos_module/agentix_logos_module.py` — reference implementation
- `agentix_logos_module/method_dispatch.py` — logoscore call parsing + dispatch
- `tests/test_module.py` — unit test surface
- `docs/BRIDGE-SPEC.md` § "Phase 2 (months 2-3)" — the broader Phase 2 scope this lands inside
- `docs/CLAUDE-LOGOS-CONTROLLER.md` — the human-final-apply contract this module honours
