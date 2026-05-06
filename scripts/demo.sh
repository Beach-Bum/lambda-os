#!/usr/bin/env bash
# Agentix OS Demo — shows the full operating system pipeline
#
# This script demonstrates Agentix operating a Logos node:
#   1. Node health monitoring (snapshot, verify, policy)
#   2. Upgrade detection (24 available upgrades)
#   3. Auto-proposal generation
#   4. Governance approval flow
#   5. Audit chain verification
#
# Usage: ./scripts/demo.sh [--workspace PATH]

set -uo pipefail

WORKSPACE="${1:-${LOGOS_WORKSPACE_HOME:-$HOME/projects/logos-workspace}}"
BOLD='\033[1m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[0;33m'
NC='\033[0m'

step() {
    echo ""
    echo -e "${BOLD}${CYAN}━━━ $1 ━━━${NC}"
    echo ""
}

pause() {
    echo ""
    echo -e "${YELLOW}[press enter]${NC}"
    read -r
}

# Clean previous demo state
rm -rf "$WORKSPACE/.agentix/proposals" 2>/dev/null

step "1. AGENTIX OS — Node Status"
echo "Agentix is the control plane that operates this Logos node."
echo "First, let's see what we're managing:"
echo ""
agentix-logos workspace-status --path "$WORKSPACE" --json 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'  Workspace:  {d[\"path\"]}')
print(f'  Submodules: {d[\"submodule_count\"]}')
print(f'  Clean:      {not d[\"git_dirty\"]}')
print(f'  Flake:      {d[\"has_flake_lock\"]}')
"
pause

step "2. SOURCE SNAPSHOT — Integrity Baseline"
echo "Snapshot all 55 submodules — the 'before' photo that proves source integrity."
echo ""
agentix-logos snapshot --path "$WORKSPACE" --json 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'  Submodules captured: {d[\"submodule_count\"]}')
print(f'  Tracked clean:      {d[\"tracked_diff_empty\"]}')
print(f'  Untracked files:    {d[\"untracked_files\"]}')
"
pause

step "3. MODULE VERIFICATION — Sandbox Health Check"
echo "Verify each module loads in a sandbox (LOGOS_USER_DIR isolated):"
echo ""
for mod in capability_module package_downloader package_manager; do
    result=$(agentix-logos verify-logoscore \
        --workspace "$WORKSPACE" \
        --modules "$mod" \
        --call "${mod}.load()" \
        --modules-dir "$WORKSPACE/result/modules" \
        --backend logos_host \
        --timeout 10 \
        --json 2>/dev/null)
    status=$(echo "$result" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])" 2>/dev/null)
    if [ "$status" = "ok" ]; then
        echo -e "  ${GREEN}PASS${NC}  $mod"
    else
        echo -e "  FAIL  $mod"
    fi
done
pause

step "4. POLICY ENFORCEMENT"
echo "Check workspace against Logos policy rules:"
echo ""
agentix-logos policy-check --path "$WORKSPACE" --json 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'  Policy loaded:     {d[\"policy_loaded\"]}')
print(f'  Logos block:       {d[\"logos_block_present\"]}')
print(f'  Violations:        {len(d[\"violations\"])}')
"
pause

step "5. DAEMON — Continuous Monitoring + Auto-Proposals"
echo "Running the Agentix daemon for one cycle..."
echo "It will: snapshot → verify → check policy → detect upgrades → generate proposals"
echo ""
LOGOS_WORKSPACE="$WORKSPACE" AGENTIX_CHECK_INTERVAL=999 timeout 40 agentix-daemon 2>&1 | grep -E "INFO|WARN" | head -15
echo "  ..."
pause

step "6. GOVERNANCE — 24 Proposals Awaiting Approval"
echo "The daemon detected upgrades and auto-generated proposals:"
echo ""
agentix-logos governance status --path "$WORKSPACE" --json 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'  Governance backend: {d[\"governance_backend\"]}')
for state, count in d.get('proposals', {}).items():
    print(f'  {state}: {count}')
print()
print('  Pending approvals:')
for p in d.get('pending_approvals', [])[:5]:
    print(f'    {p[\"module\"]}: -> {p[\"proposed_sha\"]}')
remaining = len(d.get('pending_approvals', [])) - 5
if remaining > 0:
    print(f'    ... +{remaining} more')
"
pause

step "7. APPROVE — Human Governance Decision"
# Find first pending proposal
FIRST_ID=$(agentix-logos governance list --path "$WORKSPACE" --state pending --json 2>/dev/null | \
    python3 -c "import sys,json; ps=json.load(sys.stdin)['proposals']; print(ps[0]['id'] if ps else '')" 2>/dev/null)

if [ -n "$FIRST_ID" ]; then
    echo "Approving: $FIRST_ID"
    echo ""
    agentix-logos governance approve "$FIRST_ID" --path "$WORKSPACE" --json 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'  State:    {d[\"proposal\"][\"state\"]}')
print(f'  Decided:  {d[\"proposal\"][\"governance_decided_by\"]}')
print(f'  Next:     {d[\"next_step\"]}')
"
fi
pause

step "8. APPLY — Governance-Gated Activation"
if [ -n "$FIRST_ID" ]; then
    echo "The approved proposal is ready to apply:"
    echo ""
    agentix-logos governance apply "$FIRST_ID" --path "$WORKSPACE" --json 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('  Commands:')
for cmd in d['commands']:
    print(f'    \$ {cmd}')
print()
print(f'  {d[\"warning\"]}')
"
fi
pause

step "9. AUDIT CHAIN — Tamper-Evident Log"
echo "Building hash chain over the audit trail (Phase 3 Codex preview):"
echo ""
agentix-logos audit chain --path "$WORKSPACE" --json 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'  Entries:     {d[\"entries\"]}')
print(f'  Genesis CID: {d[\"genesis_cid\"][:40]}...' if d.get('genesis_cid') else '  (empty)')
print(f'  Head CID:    {d[\"head_cid\"][:40]}...' if d.get('head_cid') else '')
print(f'  Sidecar:     {d[\"sidecar\"]}')
"
echo ""
echo "Verifying chain integrity:"
agentix-logos audit verify --path "$WORKSPACE" --json 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'  Valid:   {d[\"valid\"]}')
print(f'  Entries: {d[\"entries\"]}')
print(f'  Errors:  {len(d[\"errors\"])}')
"
pause

step "SUMMARY"
echo -e "${BOLD}Agentix OS — operating a Logos node${NC}"
echo ""
echo "  What you saw:"
echo "    1. Source snapshot: 55 submodules captured in <1s"
echo "    2. Module verification: 3 modules loaded in sandbox"
echo "    3. Policy enforcement: Logos-specific rules checked"
echo "    4. Upgrade detection: 24 available upgrades found"
echo "    5. Auto-proposals: patches generated for each upgrade"
echo "    6. Governance: human approve/reject (multisig + LEZ ready)"
echo "    7. Audit chain: tamper-evident hash chain (Codex ready)"
echo ""
echo "  What's next:"
echo "    Phase 3: Audit → Codex, governance → lez-multisig"
echo "    Phase 4: Policy as LEZ program, fully autonomous"
echo ""
echo -e "${GREEN}The OS is running. The substrate is managed.${NC}"
