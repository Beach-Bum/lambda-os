# Audit Schema — Logos Extensions

`agentix-logos` extends the Agentix base audit event with Logos-specific fields. Each controller-run / worktree-run for a Logos goal appends one JSONL line to `<workspace>/.agentix/audit.jsonl`.

## Base fields (from Agentix)

```json
{
  "timestamp": "2026-05-06T08:14:23.451Z",
  "action": "controller_run",
  "mode": "execute",
  "goal": "swap logos-chat-legacy-module for logos-chat-module in profile alice",
  "result": "ok",
  "passed": true,
  "source_modified": false,
  "proposal_saved": true,
  "error": null,
  "path": "/Users/ned/projects/logos-workspace"
}
```

## Logos extensions

```json
{
  "agentix_logos_version": "0.0.1",
  "logos_workspace_commit": "9f2a1bc4...",

  "modules_touched": [
    "logos-chat-legacy-module",
    "logos-chat-module"
  ],

  "modules_versions": {
    "logos-chat-legacy-module": ["v0.2.1", null],
    "logos-chat-module": [null, "v0.4.0"]
  },

  "logoscore_calls": [
    {
      "module": "chat_module",
      "method": "healthcheck",
      "args": [],
      "exit_code": 0,
      "stdout_sha256": "e5f3...",
      "stderr_sha256": "d41d...",
      "duration_seconds": 2.31
    },
    {
      "module": "chat_module",
      "method": "make_intro_bundle",
      "args": ["test_user"],
      "exit_code": 0,
      "stdout_sha256": "abc1...",
      "stderr_sha256": "d41d...",
      "duration_seconds": 0.42
    }
  ],

  "nix_flake_lock_changed": true,
  "lez_program_pin_changed": false,

  "scaffold_modules_changed": [
    "logos-chat-legacy-module",
    "logos-chat-module"
  ],

  "submodule_drift": [],

  "policy_violations": [],

  "build_targets": ["logos-basecamp"],
  "build_overrides": {
    "logos-chat-module": "github:logos-co/logos-chat-module/v0.4.0"
  },

  "sandbox_user_dir": "/tmp/agentix-xxxxxxxx/sandbox-user-dir",
  "sandbox_localnet_port": null,

  "stops_before_apply": true,
  "stops_before_rebuild": true,
  "stops_before_lgs_deploy": true,
  "stops_before_lgs_wallet": true
}
```

## Field reference

### Identity / versioning

- **`agentix_logos_version`** — bridge version (semver). Distinguishes audit lines across schema migrations.
- **`logos_workspace_commit`** — `git rev-parse HEAD` of `logos-workspace` at run start. Critical for Phase 3 Codex anchoring.

### Modules

- **`modules_touched: string[]`** — list of module names involved in the goal. Drives policy checks.
- **`modules_versions: { [name: string]: [before|null, after|null] }`** — version transitions. `null` on the `before` side = install. `null` on `after` = removal.

### Verify rung

- **`logoscore_calls: LogoscoreCall[]`** — every `logoscore -c "..."` invocation in the verify rung. `stdout_sha256` enables deterministic diffing across runs (with `--deterministic-mode` once that's PR'd upstream).

### Nix / LEZ state

- **`nix_flake_lock_changed: boolean`** — `flake.lock` differs between snapshot and post-run. If true, the proposal includes a flake.lock delta.
- **`lez_program_pin_changed: boolean`** — if a `[lez.programs.*]` pin changed in `scaffold.toml`. Triggers `lez_programs_pinned` policy check.
- **`scaffold_modules_changed: string[]`** — which `[modules.*]` entries in `scaffold.toml` were updated.

### Source-untouched audit

- **`submodule_drift: SubmoduleDrift[]`** — non-empty only on `source_workspace_mutated` errors. Each entry: `{path, before_sha, after_sha}`. Should always be empty in successful runs.
- **`policy_violations: PolicyViolation[]`** — empty in successful runs. Non-empty entries block the proposal save (severity=deny) or are logged for review (severity=review).

### Build / sandbox

- **`build_targets: string[]`** — `nix build` / `ws build` targets executed during verify.
- **`build_overrides: { [input: string]: string }`** — `--override-input` flags applied during build.
- **`sandbox_user_dir: string`** — `LOGOS_USER_DIR` value used for `logoscore` / `lgs basecamp launch` calls. Must be under the temp worktree.
- **`sandbox_localnet_port: number | null`** — port used if a sandbox localnet was started.

### Safety attestations

These are **claims** the controller makes, captured for audit. Each must be `true` in a successful Logos run:

- **`stops_before_apply`** — controller did not invoke `agentix apply-verify`
- **`stops_before_rebuild`** — controller did not invoke `rebuild-nixos` or `nixos-rebuild switch`
- **`stops_before_lgs_deploy`** — controller did not invoke `lgs deploy`
- **`stops_before_lgs_wallet`** — controller did not invoke `lgs wallet topup` or other wallet mutations

If any of these are `false` in an audit line, the run was not safety-compliant and must be investigated.

## Schema versioning

The audit schema follows semver. Breaking changes bump the major version of `agentix_logos_version`. Old audit lines remain valid; consumers (audit summary tools, future Codex indexers) handle multiple schema versions.

The schema version is also recorded in:

```bash
agentix audit summary --path . --json | jq .schema_versions
```

## Phase 3 migration: audit → Codex

In Phase 3, each audit line becomes a Codex object addressed by `sha256(line)`. The local `audit.jsonl` retains the same format; a sidecar `audit-codex.jsonl` adds `{cid, sequence_number, anchor_lez_tx_id}` for each line that's been published to Codex.

The schema documented here remains canonical. The Codex object **is** this JSON line, content-addressed.

## Tooling

- **`agentix audit tail --path . --json`** — last N events, base + extensions
- **`agentix audit summary --path . --json`** — aggregate stats; Logos extensions surface as additional sections
- **`agentix-logos audit show <line-number> --path . --json`** — pretty-print a single event with module/version delta highlighted
- **`agentix-logos audit verify --path . --json`** *(Phase 2)* — replay logoscore calls deterministically and confirm stdout SHA matches recorded hash. Catches retroactive tampering.

## Example failure event

```json
{
  "timestamp": "2026-05-06T08:14:23.451Z",
  "action": "controller_run",
  "mode": "execute",
  "goal": "swap chat-legacy for chat",
  "result": "blocked",
  "passed": false,
  "source_modified": false,
  "proposal_saved": false,
  "error": "policy_violation",
  "path": "/Users/ned/projects/logos-workspace",
  "agentix_logos_version": "0.0.1",
  "logos_workspace_commit": "9f2a1bc4...",
  "modules_touched": ["logos-chat-legacy-module"],
  "modules_versions": {"logos-chat-legacy-module": ["v0.2.1", null]},
  "logoscore_calls": [],
  "nix_flake_lock_changed": false,
  "lez_program_pin_changed": false,
  "scaffold_modules_changed": [],
  "submodule_drift": [],
  "policy_violations": [
    {
      "rule": "require_rln_for_messaging_modules",
      "severity": "deny",
      "module": "logos-chat-legacy-module",
      "details": "Removing this module without replacement leaves no RLN-protected chat path"
    }
  ],
  "build_targets": [],
  "build_overrides": {},
  "sandbox_user_dir": "/tmp/agentix-aabbccdd/sandbox-user-dir",
  "sandbox_localnet_port": null,
  "stops_before_apply": true,
  "stops_before_rebuild": true,
  "stops_before_lgs_deploy": true,
  "stops_before_lgs_wallet": true
}
```

This is what a clean policy-blocked event looks like: source untouched, no proposal saved, no logoscore work done (the policy fired before verify), full traceability of why.
