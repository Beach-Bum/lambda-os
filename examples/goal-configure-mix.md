# Goal — Configure mix network routing

A reference goal exercising **mix network configuration via Agentix proposal**. The proposal updates the `[mix]` block in `scaffold.toml` with validated routing parameters. The `require_rln_for_messaging_modules` policy rule cross-references mix state to ensure RLN remains enabled when messaging modules are loaded.

## Goal text

```
Configure the mix network routing in logos-workspace. Set mix_node_count
to 5, enable RLN, and add capability filters for messaging and relay.
Validate the configuration passes all sanity checks before saving the
proposal.
```

## What the controller should do

1. Standard ladder: `controller-plan` → `workspace-status` → `modules list` → `policy-check`.
2. Read the current `[mix]` block from `scaffold.toml` (via `agentix_logos.mix.parse_mix_config`). If no block exists, create one with sane defaults.
3. Validate the proposed config: `agentix_logos.mix.validate_mix_config(config)`. All checks must pass before saving.
4. Run `agentix controller-run "..." --execute` — in the worktree:
   - Update `[mix]` block with the requested parameters.
5. **Policy check.** The `require_rln_for_messaging_modules` rule cross-references mix state:
   - If any messaging-class module is loaded AND `[mix].rln_enabled = false` → deny.
   - This ensures operators cannot disable mix-layer RLN while messaging modules depend on it.
6. Save proposal. Report. Stop.

## Expected proposal contents

- `scaffold.toml` diff: `[mix]` block updated with validated parameters.
- Nothing else. No module installs, no flake lock changes (unless a mix dependency shifted).

## Configuration shape

```toml
[mix]
rln_enabled = true
mix_node_count = 5
capability_filters = ["messaging", "relay"]
```

## Validation rules

- `mix_node_count` must be in range [3, 50]. Less than 3 isn't a meaningful mix network; more than 50 is operator misconfiguration.
- `capability_filters` must contain only recognised values: messaging, chat, delivery, store, relay, filter, lightpush.
- `rln_enabled` should be true when messaging modules are loaded (enforced by policy cross-reference).

## Failure scenarios

- **`require_rln_for_messaging_modules` deny with mix cross-reference** → the proposal sets `rln_enabled = false` while a messaging module is loaded. Fix: set `rln_enabled = true` or remove the messaging module first.
- **`validate_mix_config` violation: node count out of bounds** → operator specified a node count outside [3, 50]. Adjust to a sane value.
- **`validate_mix_config` violation: unrecognised capability filter** → a typo or unsupported filter value. Check against the valid set.
- **`[mix]` block malformed** → field type mismatch (e.g. `rln_enabled = "yes"`). The parser raises ValueError with the bad field; fix and retry.

## Why this matters for the architecture

Mix routing is the privacy layer of the Logos network stack. Misconfiguring it (too few nodes, wrong capabilities, RLN disabled) degrades anonymity guarantees for all messaging. The policy cross-reference ensures that the safety of the messaging layer and the mix layer are validated together — you can't weaken one without the policy engine catching the dependency.
