# Goal — Swap chat-legacy for chat

A reference goal exercising **module swap** — replacing one capability with another. The hardest of the three reference goals because both modules touch messaging policy.

## Goal text

```
In logos-workspace, swap logos-chat-legacy-module for logos-chat-module in
basecamp profile alice. Verify the new chat module's intro-bundle flow works
via logoscore.
```

## What the controller should do

1. Standard ladder: `controller-plan` → `workspace-status` → `modules list` → `policy-check`.
2. Confirm both modules appear in `modules list`:
   - `logos-chat-legacy-module` is currently captured (role=project) for profile alice
   - `logos-chat-module` may or may not be captured yet
3. **Verify RLN compliance.** Check both `metadata.json`s for RLN capability. The `require_rln_for_messaging_modules` policy applies to both. If `logos-chat-legacy-module` doesn't have RLN but `logos-chat-module` does, this swap is *good* — note that in the proposal.
4. Run `agentix controller-run "..." --execute` — in the worktree:
   - Remove `[modules.logos_chat_legacy_module]` from `scaffold.toml`
   - Add `[modules.logos_chat_module]` with `flake = "github:logos-co/logos-chat-module/<latest>"`
   - Update `flake.lock`
   - `ws build logos-chat-module --auto-local`
5. `agentix-logos verify-logoscore`:
   - `--modules chat_module`
   - `--call "chat_module.healthcheck()"`
   - `--call "chat_module.create_intro_bundle('test_user')"` — captures bundle output
   - `--call "chat_module.parse_intro_bundle(<captured>)"` — round-trip the bundle
6. Save proposal. Report. Stop.

## Expected proposal contents

- `scaffold.toml` diff:
  - Removal of `[modules.logos_chat_legacy_module]` block
  - Addition of `[modules.logos_chat_module]` block
- `flake.lock` diff:
  - Removal of `logos-chat-legacy-module` entry
  - Addition of `logos-chat-module` entry
- Nothing else

## Human apply

```bash
agentix apply-verify --proposal .agentix/proposals/<latest>.patch
ws build logos-basecamp
./repos/logos-basecamp/result/bin/LogosBasecamp --user-dir /tmp/basecamp-chat-swap
# Verify Chat panel uses the new module:
# - Create an intro bundle
# - Paste another user's bundle (or a synthetic one)
# - Send a message
# - Verify it's RLN-protected (no spam vector)
```

## Why this goal stresses the bridge

- Touches **two modules** in one proposal — exercise of the modules-touched audit field
- Exercises the **RLN policy** on both removal and addition sides
- The verify rung needs to pass output between calls (intro bundle creation → parsing) — this is the smallest interesting **stateful** logoscore sequence
- Tests submodule snapshot extension: `repos/logos-chat-legacy-module` and `repos/logos-chat-module` both move

## Failure scenarios

- **Intro bundle round-trip mismatch** → new module is incompatible with bundle format expected by clients. Patch not saved.
- **RLN missing in `logos-chat-module`** → `require_rln_for_messaging_modules` denies. (Should not happen — chat is RLN by default — but the policy fires if it does.)
- **Submodule snapshot drift** → if either chat submodule's SHA changed during build (it shouldn't, but build can race), `source_workspace_mutated`. Investigate timing of pre/post snapshot.
