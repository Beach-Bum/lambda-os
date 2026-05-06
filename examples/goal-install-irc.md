# Goal — Install logos-irc-module

A reference goal exercising **module install** through the controller.

## Goal text

```
Add logos-irc-module to logos-workspace, capture it in scaffold.toml under
[modules.logos_irc_module] with the latest stable github tag, and verify it
loads cleanly in a sandbox basecamp profile via logoscore.
```

## What the controller should do

1. `agentix controller-plan` — confirm contract.
2. `agentix-logos workspace-status` — confirm logos-workspace is on a clean branch.
3. `agentix-logos modules list` — confirm `logos-irc-module` is **not** already captured.
4. `agentix-logos policy-check` — confirm `require_metadata_json` and `require_rln_for_messaging_modules` apply (IRC is messaging-class).
5. **Verify RLN.** Fetch metadata.json from `github:logos-co/logos-irc-module` and check `rln: true` in capabilities. **If false, refuse the goal** (policy violation).
6. `agentix controller-run "..." --execute` — in the worktree:
   - `ws build logos-irc-module --auto-local`
   - Update `scaffold.toml` `[modules.logos_irc_module]` with `flake = "github:logos-co/logos-irc-module/<tag>"` and `role = "project"`
   - Update `flake.lock` accordingly
7. `agentix-logos verify-logoscore`:
   - `--modules irc_module`
   - `--call "irc_module.healthcheck()"`
   - `--call "irc_module.list_servers()"` (smoke check that init worked)
8. Save proposal patch.
9. Report. Stop.

## Expected proposal contents

- `scaffold.toml` diff: new `[modules.logos_irc_module]` block
- `flake.lock` diff: new entry for `logos-irc-module`
- No `repos/*` changes
- Possibly `.scaffold/state/...` updates (these should be in `policy.ignore_paths`)

## Human apply

```bash
agentix apply-verify --proposal .agentix/proposals/<latest>.patch
ws build logos-basecamp
./repos/logos-basecamp/result/bin/LogosBasecamp --user-dir /tmp/basecamp-irc-test
# Verify IRC module appears in the basecamp module list
```

## Failure scenarios

- **RLN missing in metadata** → policy denies. Patch not saved. Audit shows `policy_violations`.
- **Tag not found** → `nix build` fails. Patch not saved. Audit shows `error="build_failed"`.
- **logoscore healthcheck non-zero** → patch not saved. Audit shows `logoscore_calls[].exit_code != 0`.
- **scaffold.toml malformed after edit** → toml parse error during verify. Patch not saved.
