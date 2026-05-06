# Goal — Deploy a LEZ program (proposal-only)

A reference goal exercising **LEZ program deployment via Agentix proposal**. The proposal contains the deployment patch (`scaffold.toml` `[lez.programs.*]` pin + `flake.lock` for the new program ID). Actual `lgs deploy` execution stays human-only per `forbid_lez_program_deploy` and the propose-verify-human-apply contract.

## Goal text

```
Deploy a new version of the my_lez_program program in logos-workspace.
Capture the recomputed program_id in scaffold.toml [lez.programs.my_lez_program],
update flake.lock if any source dependencies changed, and verify the
program ID hashes deterministically. Do NOT execute lgs deploy from the
controller path; produce a reviewable proposal instead.
```

## What the controller should do

1. Standard ladder: `controller-plan` → `workspace-status` → `modules list` → `policy-check`.
2. Read the current `[lez.programs.my_lez_program]` block from `scaffold.toml` (via `agentix_logos.lez.parse_lez_programs_from_scaffold`). Capture the existing `program_id` pin.
3. Locate the program source: `<workspace>/<source>` (default: `programs/my_lez_program`). If `entry_point` is set, that's the path that gets hashed.
4. Build the program (in the worktree, never the source workspace):
   - `cd programs/my_lez_program && cargo build --release` (Rust LEZ programs)
   - Or invoke the project's build script. The output should land at the path scaffold.toml's `entry_point` points to.
5. Compute the new program_id: `agentix_logos.lez.compute_program_id_from_source(entry_point_path)`.
6. Run `agentix controller-run "..." --execute` — in the worktree:
   - Update `[lez.programs.my_lez_program].program_id` to the new `sha256:<hex>` pin.
   - `nix flake lock --update-input` if any source deps shifted.
7. **Verify, do not deploy.** The `lez_programs_pinned` policy rule is now consulted: it recomputes the hash from source and confirms the pin matches. No drift → proposal saves.
8. Save proposal. Report. Stop.

## Expected proposal contents

- `scaffold.toml` diff: `[lez.programs.my_lez_program].program_id` updated to the new `sha256:<hex>`.
- `flake.lock` diff: only if a source dependency genuinely shifted.
- The compiled binary at `programs/my_lez_program/<entry_point>` exists in the worktree but is gitignored upstream (we don't commit binaries) — its hash is what we pin.
- Nothing else. No `lgs deploy` invocation. No wallet operations.

## Human apply

```bash
agentix apply-verify --proposal .agentix/proposals/<latest>.patch
# Now scaffold.toml is updated. Operator opens a terminal and runs:
cd ~/projects/logos-workspace
lgs deploy my_lez_program          # human-only — this is the actual deploy
# or with the pinned program_id explicitly verified first:
lgs deploy my_lez_program --program-id sha256:<hex>
```

## Why deploy is split out

Looking at `docs/POLICY-SCHEMA.md`:

- `forbid_lez_program_deploy: true` — the controller path never invokes `lgs deploy`.
- `forbid_wallet_operations: true` — and never touches the wallet that would pay for the deploy tx.
- `lez_programs_pinned: true` — but it WILL refuse to apply a proposal whose pin drifted from source.

So the controller produces a verified pin and a clean patch; the human owns the real deploy + the wallet authorisation in their terminal. Phase 3+ generalises "human-in-terminal" to "lez-multisig signature threshold," but the split between proposal and execution stays.

## Failure scenarios

- **`lez_programs_pinned` deny with `program_id drift`** → the source bytes changed but the pin in the proposal doesn't match. Re-run the controller (it'll recompute and update). If pin still doesn't match, build is non-deterministic — fix the build.
- **`lez_programs_pinned` deny with `no program_id pin`** → the program exists in `scaffold.toml` without a captured pin. Run `agentix controller-run` once to compute and capture; second run should pass.
- **`lez_programs_pinned` deny with `source/entry_point not found`** → either the source path in scaffold.toml is wrong, or the build didn't produce the expected binary. Inspect the worktree before applying.
- **`forbid_lez_program_deploy` deny** *(future, not yet enforced)* → the proposal contains a `lgs deploy` invocation. Strip it and let the human run deploy in their terminal.
- **`scaffold.toml [lez.programs] is malformed`** → field type mismatch (e.g. `source = 42`). The error message names the bad field; fix and retry.

## Why this is a strong demo for the architecture talk

LEZ deployment is the most "decentralised-OS"-shaped operation in the stack — a piece of code goes onto a sovereign execution layer, anchored, paid for, runnable. Showing Agentix verifying the pin without ever touching the wallet or executing the deploy is the propose-verify-human-apply contract at the most consequential layer. The worked failure modes (drift detection, missing pin, malformed scaffold) demonstrate the policy engine catching real concerns rather than rubber-stamping.
