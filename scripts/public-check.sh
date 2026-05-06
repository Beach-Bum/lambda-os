#!/usr/bin/env bash
# Pre-publish check using Agentix's public-check primitive.
# Run before any `git push` to a public branch.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

if ! command -v agentix >/dev/null 2>&1; then
    echo "ERROR: agentix not on PATH. Install Agentix first:"
    echo "  cd ~/projects/agentix && uv tool install --editable . --reinstall"
    exit 1
fi

echo "[agentix-logos] running public-check on $REPO_ROOT"
agentix public-check --path "$REPO_ROOT"

echo
echo "[agentix-logos] running export-public dry-run to /tmp/agentix-logos-public-check"
rm -rf /tmp/agentix-logos-public-check
agentix export-public --path "$REPO_ROOT" --dest /tmp/agentix-logos-public-check --yes

echo
echo "[agentix-logos] re-checking exported tree"
agentix public-check --path /tmp/agentix-logos-public-check

echo
echo "[agentix-logos] OK — safe to publish"
