#!/usr/bin/env bash
# integration-test.sh — End-to-end integration test for agentix-logos
#
# Runs the full RUNBOOK Day 1-5 flow against a real logos-workspace.
# Requires: logos-workspace cloned and built, agentix CLI installed.
#
# Usage:
#   ./scripts/integration-test.sh [--workspace PATH]
#
# Default workspace: ~/projects/logos-workspace

set -uo pipefail

WORKSPACE="${1:-${LOGOS_WORKSPACE_HOME:-$HOME/projects/logos-workspace}}"
AGENTIX_LOGOS_HOME="$(cd "$(dirname "$0")/.." && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m' # used in pause()
BOLD='\033[1m'
NC='\033[0m'

pass=0
fail=0

check() {
    local name="$1"
    shift
    if eval "$@" > /dev/null 2>&1; then
        echo -e "  ${GREEN}PASS${NC}  $name"
        pass=$((pass + 1))
    else
        echo -e "  ${RED}FAIL${NC}  $name"
        fail=$((fail + 1))
    fi
}

check_output() {
    local name="$1"
    local expected="$2"
    shift 2
    local output
    output=$(eval "$@" 2>&1) || true
    if echo "$output" | grep -q "$expected"; then
        echo -e "  ${GREEN}PASS${NC}  $name"
        pass=$((pass + 1))
    else
        echo -e "  ${RED}FAIL${NC}  $name (expected '$expected')"
        echo "        got: $(echo "$output" | head -3)"
        fail=$((fail + 1))
    fi
}

echo -e "${BOLD}agentix-logos integration test${NC}"
echo -e "Workspace: $WORKSPACE"
echo -e "Bridge:    $AGENTIX_LOGOS_HOME"
echo ""

# ── Prerequisites ──
echo -e "${BOLD}Prerequisites${NC}"
check "logos-workspace exists" "test -d '$WORKSPACE'"
check "logos-workspace is git repo" "test -d '$WORKSPACE/.git'"
check "agentix CLI installed" "which agentix"
check "agentix-logos installed" "which agentix-logos"
check "workspace has result/" "test -d '$WORKSPACE/result'"
check "LogosBasecamp binary exists" "test -f '$WORKSPACE/result/bin/LogosBasecamp'"
check "logos_host binary exists" "test -f '$WORKSPACE/result/bin/logos_host'"
check "modules directory exists" "test -d '$WORKSPACE/result/modules'"
echo ""

# ── Day 2: Agentix wiring ──
echo -e "${BOLD}Day 2: Agentix wiring${NC}"
check "policy.json exists" "test -f '$WORKSPACE/.agentix/policy.json'"
check_output "agentix status recognizes workspace" "Agentix workspace: yes" \
    "agentix status --path '$WORKSPACE'"
check_output "controller-plan returns safety contract" "source_workspace_must_remain_untouched" \
    "agentix controller-plan --path '$WORKSPACE' --json"
echo ""

# ── Day 3: Submodule snapshot ──
echo -e "${BOLD}Day 3: Submodule snapshot${NC}"

SNAP_OUTPUT=$(cd "$AGENTIX_LOGOS_HOME" && uv run python3 -c "
from pathlib import Path
from agentix_logos.workspace import LogosWorkspace
import time, json

ws = LogosWorkspace(Path('$WORKSPACE'))
start = time.time()
snap = ws.extended_source_snapshot()
duration = time.time() - start

snap2 = ws.extended_source_snapshot()
report = ws.compare_snapshots(snap, snap2)

print(json.dumps({
    'duration': round(duration, 2),
    'submodules': len(snap.submodule_shas),
    'drift': report.has_drift,
}))
" 2>&1)

SNAP_DURATION=$(echo "$SNAP_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['duration'])" 2>/dev/null || echo "0")
SNAP_SUBS=$(echo "$SNAP_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['submodules'])" 2>/dev/null || echo "0")
SNAP_DRIFT=$(echo "$SNAP_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['drift'])" 2>/dev/null || echo "True")

check "snapshot captures submodules (>40)" test "$SNAP_SUBS" -gt 40
check "snapshot under 2s (${SNAP_DURATION}s)" "python3 -c 'assert float(\"$SNAP_DURATION\") < 2.0'"
check "no drift on consecutive reads" test "$SNAP_DRIFT" = "False"

# CLI snapshot command
check_output "agentix-logos snapshot works" "submodule_count" \
    "agentix-logos snapshot --path '$WORKSPACE' --json"
echo ""

# ── Day 4: Module verification ──
echo -e "${BOLD}Day 4: Module verification${NC}"

VERIFY_OUTPUT=$(cd "$AGENTIX_LOGOS_HOME" && uv run agentix-logos verify-logoscore \
    --workspace "$WORKSPACE" \
    --modules capability_module \
    --call "capability_module.load()" \
    --modules-dir "$WORKSPACE/result/modules" \
    --backend logos_host \
    --timeout 10 \
    --json 2>&1) || true

VERIFY_STATUS=$(echo "$VERIFY_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "")
VERIFY_BACKEND=$(echo "$VERIFY_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('backend',''))" 2>/dev/null || echo "")

check "verify-logoscore returns ok" test "$VERIFY_STATUS" = "ok"
check "backend is logos_host" test "$VERIFY_BACKEND" = "logos_host"
echo ""

# ── Day 5: End-to-end proposal ──
echo -e "${BOLD}Day 5: End-to-end proposal flow${NC}"

E2E_OUTPUT=$(cd "$AGENTIX_LOGOS_HOME" && uv run python3 -c "
import json, os, subprocess, tempfile, hashlib, time
from pathlib import Path
from agentix_logos.workspace import LogosWorkspace

workspace = Path('$WORKSPACE')
ws = LogosWorkspace(workspace)

# Before snapshot
before = ws.extended_source_snapshot()
old_sha = before.submodule_shas.get('repos/logos-storage-module', '')

# Create worktree
wt = Path(tempfile.mkdtemp(prefix='agentix-e2e-test-'))
subprocess.run(['git', 'worktree', 'add', str(wt), 'HEAD'],
               cwd=workspace, capture_output=True, check=True)

# Get a previous commit to pin to
prev = subprocess.run(['git', 'log', '--format=%H', '-2'],
    cwd=workspace / 'repos/logos-storage-module',
    capture_output=True, text=True).stdout.strip().split('\n')
new_sha = prev[1] if len(prev) > 1 else prev[0]

# Pin via update-index
subprocess.run(['git', 'update-index', '--cacheinfo',
    f'160000,{new_sha},repos/logos-storage-module'],
    cwd=wt, capture_output=True, check=True)

# Get diff
diff = subprocess.run(['git', 'diff', '--cached'],
    cwd=wt, capture_output=True, text=True).stdout

# After snapshot
after = ws.extended_source_snapshot()
report = ws.compare_snapshots(before, after)

# Cleanup
subprocess.run(['git', 'worktree', 'remove', str(wt), '--force'],
    cwd=workspace, capture_output=True)

print(json.dumps({
    'has_diff': len(diff) > 0,
    'source_untouched': not report.has_drift,
    'old_sha': old_sha[:12],
    'new_sha': new_sha[:12],
}))
" 2>&1)

E2E_DIFF=$(echo "$E2E_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['has_diff'])" 2>/dev/null || echo "False")
E2E_UNTOUCHED=$(echo "$E2E_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['source_untouched'])" 2>/dev/null || echo "False")

check "proposal diff is non-empty" test "$E2E_DIFF" = "True"
check "source workspace untouched after proposal" test "$E2E_UNTOUCHED" = "True"
echo ""

# ── Summary ──
total=$((pass + fail))
echo -e "${BOLD}Results: ${GREEN}$pass passed${NC}, ${RED}$fail failed${NC} / $total total"

if [ "$fail" -gt 0 ]; then
    echo -e "${RED}Integration test FAILED${NC}"
    exit 1
fi

echo -e "${GREEN}Integration test PASSED${NC}"
