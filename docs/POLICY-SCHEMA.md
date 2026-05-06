# Policy Schema — `logos:` block in `policy.json`

`agentix-logos` extends Agentix's `policy.json` with a `logos:` block that captures Logos-specific rules.

## Full schema

```jsonc
{
  // Inherited from Agentix base
  "denied": ["sudo", "rm -rf", "git push", "/etc/nixos"],
  "review": ["nix flake update", "submodule add", "patch apply"],
  "allowed": ["ws build", "ws develop", "nix build", "logoscore", "lm"],

  // Logos-specific block
  "logos": {
    // Modules
    "require_metadata_json": true,
    "require_signed_metadata": false,        // off by default; opt-in once key registry is wired
    "key_registry_path": null,               // path to IFT key registry JSON (null = bundled stub)
    "require_rln_for_messaging_modules": true,
    "module_install_via": "ws --auto-local", // or "manual"

    // Flake input safety
    "forbid_flake_overrides": [
      "nixpkgs",
      "logos-blockchain"
    ],

    // Sandbox isolation
    "forbidden_paths_for_writes": [
      "~/.local/share/logos-basecamp",
      "~/Library/Application Support/LogosBasecamp",
      "logos-workspace/repos/*",
      "/etc/nixos"
    ],
    "sandbox_port_range": [13000, 14000],
    "redirect_logos_user_dir": true,

    // Wallet / deploy
    "forbid_wallet_operations": true,        // no lgs wallet topup, no lgs deploy
    "forbid_live_localnet": true,            // no lgs localnet without --bind sandbox port

    // LEZ
    "lez_programs_pinned": true,             // refuse if program ID pin drifted
    "forbid_lez_program_deploy": true,       // human-only

    // LIPs
    "lip_edits_via_proposal_only": true,     // controller writes proposed-lips/*.md only

    // Audit
    "audit_extra_fields_required": [
      "agentix_logos_version",
      "logos_workspace_commit",
      "modules_touched",
      "logoscore_calls",
      "nix_flake_lock_changed"
    ],

    // Submodule snapshot extension
    "submodule_snapshot": true,              // see BRIDGE-SPEC.md § Submodule snapshot
    "submodule_drift_is_violation": true,    // any repos/* SHA change = mutated

    // Paths to exempt from untracked-file snapshot (build artifacts, etc.)
    "ignore_paths": [
      "repos/*/result",
      "repos/*/result-*",
      ".direnv",
      ".scaffold/state/*.lock"
    ]
  }
}
```

## Rule semantics

### `require_metadata_json` (default `true`)

Every module touched in a proposal must have a `metadata.json` at its repo root. Missing → policy violation.

### `require_signed_metadata` (default `false`)

When enabled, every module touched in a proposal must have a `signature` field on its `metadata.json` that verifies against the IFT key registry. The signature format is `"<key_id>:<sig_hex>"` — ed25519 signature over the canonical JSON serialisation of the metadata dict (sort_keys, no whitespace, signature field excluded). Verification logic lives in `agentix_logos/keys.py` (`KeyRegistry.verify`).

**Implementation status:** Phase 2 ships with a stub registry at `examples/key-registry-stub.json` containing two test keys whose private seeds are documented in `tests/test_keys.py`. This lets the rule be exercised end-to-end before the real IFT registry exists. Phase 3+ replaces the stub with the canonical IFT registry once Logos publishes one — operators set `key_registry_path` in `policy.json` to point at the new file.

Failure modes that produce `severity: "deny"` violations:
- Module's `metadata.json` has no `signature` field → per-module violation
- Signature claims an unknown `key_id` (not in registry) → per-module violation
- Signature does not verify against the registered public key (tampered metadata) → per-module violation
- Registry file unreadable or malformed → single violation, not per-module spam (rule fails closed; can't enforce safely)

The rule is OFF by default because most current Logos modules aren't yet signed. Operators opt in once their module set is signature-clean.

### `key_registry_path` (default `null`)

Path to the IFT key registry JSON file. Resolution:
- Absolute path used as-is
- Relative path resolved against the workspace root
- `null` (default) → fall back to the bundled `examples/key-registry-stub.json` next to the agentix-logos package

Only consulted when `require_signed_metadata` is `true`.

### `require_rln_for_messaging_modules` (default `true`)

Any module whose `metadata.json` declares `capabilities` containing `"messaging"` or `"chat"` or `"delivery"` must also declare `rln: true` (or equivalent — exact field TBD). Without RLN, messaging modules are a spam vector. Phase 1 fires this rule on the legacy chat module to validate the policy infrastructure.

### `module_install_via` (default `"ws --auto-local"`)

Controls how new modules are added to the workspace. `"ws --auto-local"` means use the workspace's flake override mechanism. `"manual"` allows direct `flake.nix` edits but emits a warning. Other values: violation.

### `forbid_flake_overrides` (default `["nixpkgs", "logos-blockchain"]`)

Flake inputs in this list cannot be `--override-input`'d in the controller path. They're system-wide and require human judgment.

### `forbidden_paths_for_writes`

Paths that the controller path must never write to. Includes user basecamp profiles (cross-platform) and the source `repos/*/` subdirs. Triggers `source_workspace_mutated` if violated.

### `sandbox_port_range` (default `[13000, 14000]`)

Any localnet started in the controller path must use a port in this range. The default Logos localnet port (3040) is forbidden — would conflict with the user's real instance.

### `redirect_logos_user_dir` (default `true`)

When the controller invokes `logoscore`, `lgs basecamp launch`, etc., set `LOGOS_USER_DIR=<worktree>/sandbox-user-dir`. Hard-isolates from the user's basecamp state.

### `forbid_wallet_operations` (default `true`)

`lgs wallet topup`, `lgs deploy`, and any wallet keystore mutation are forbidden in the controller path. They consume testnet faucet funds and produce on-chain side effects. Human-only.

### `forbid_live_localnet` (default `true`)

`lgs localnet start` without an explicit `--bind 127.0.0.1:<sandbox-port>` is a violation.

### `lez_programs_pinned` (default `true`, **implemented**)

Every `[lez.programs.<name>]` block in the workspace's `scaffold.toml` must declare a `program_id` pin of the form `"sha256:<hex>"`. The rule recomputes the hash from source (the `entry_point` binary if set, otherwise the entire `source` directory traversed in deterministic order) and refuses the proposal on drift.

Implementation: `agentix_logos/lez.py` provides `compute_program_id_from_source` and `parse_lez_programs_from_scaffold`. The rule is enforced in `agentix_logos/policy.check_logos_policy`.

Failure modes that produce `severity: "deny"` violations:

- LEZ program declared without a `program_id` pin (operator must capture one before proposing)
- `source` or `entry_point` path doesn't exist on disk
- Source bytes recomputed hash doesn't match the captured pin (drift — source mutated without updating pin)
- `[lez.programs]` block is structurally malformed (e.g. non-string `source`)

The rule is a no-op for workspaces without a `scaffold.toml` or without any `[lez.programs]` blocks. Reference goal: `examples/goal-deploy-lez-program.md`.

`scaffold.toml` block shape:

```toml
[lez.programs.my_program]
source = "programs/my_program"
program_id = "sha256:abc123..."
entry_point = "release/my_program.bin"  # optional; if unset, the whole source dir is hashed
```

### `forbid_lez_program_deploy` (default `true`)

`lgs deploy` is human-only. Controller may produce a deploy-ready proposal patch but must not execute the deploy.

### `lip_edits_via_proposal_only` (default `true`)

Goals that would modify a LIP write the proposed change to `<worktree>/proposed-lips/<name>.md` and save the proposal. The human opens the actual PR against `logos-co/logos-lips`.

### `audit_extra_fields_required`

List of audit fields that must appear in every Logos-tagged audit event. Missing → integration bug, surfaced to human.

### `submodule_snapshot` (default `true`)

Enable the Submodule SHA snapshot extension to the source-untouched check. See [`BRIDGE-SPEC.md § Submodule snapshot extension`](./BRIDGE-SPEC.md).

### `submodule_drift_is_violation` (default `true`)

Any submodule SHA change in `repos/*` outside an explicit `flake.lock` update → `source_workspace_mutated`.

### `ignore_paths`

Glob list of paths exempt from the untracked-file snapshot. Use sparingly; each entry is a hole in the safety net.

## Loading order

`agentix-logos` reads:

1. The Agentix base policy (denied/review/allowed).
2. The `logos:` block.
3. Merges them. The `logos:` block can extend `denied` and `review` but **never** weaken the base.

If `policy.json` is missing the `logos:` block entirely, defaults apply. A missing file is a hard failure (the controller refuses to run).

## Example violation report

```json
{
  "policy_violations": [
    {
      "rule": "require_rln_for_messaging_modules",
      "severity": "deny",
      "module": "logos-chat-legacy-module",
      "details": "Module declares capabilities=[messaging] but rln=false in metadata.json"
    },
    {
      "rule": "forbidden_paths_for_writes",
      "severity": "deny",
      "path": "/Users/ned/Library/Application Support/LogosBasecamp/profiles/alice/modules",
      "details": "Proposal writes to user basecamp profile; redirect to worktree"
    }
  ]
}
```

`severity: "deny"` blocks the proposal save. `severity: "review"` is logged but doesn't block (Phase 2: surface to human via prompt before saving).

## Future schema extensions (Phase 2-4)

- **`min_nix_lock_age_seconds`** — refuse pins newer than N seconds (anti-supply-chain).
- **`lez_program_allowlist`** — only permit deploys of pre-approved program IDs.
- **`required_codex_anchors`** — proposals must be Codex-published with valid CID before applying (Phase 3).
- **`required_multisig_signatures`** — apply requires N-of-M lez-multisig signatures (Phase 3).
- **`policy_lez_program`** — the policy is itself a LEZ program ID; bridge fetches and runs it for verification (Phase 4).
