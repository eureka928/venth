#!/usr/bin/env bash
#
# Safely review and test a contributor's PR in an isolated sandbox.
#
# Usage:
#   ./scripts/review_pr.sh <PR_NUMBER>                  # Review in mock mode (safe)
#   ./scripts/review_pr.sh <PR_NUMBER> --live <API_KEY>  # Test with real API (after code review)
#
# What this does:
#   1. Fetches the PR into a local branch
#   2. Creates a fresh virtual environment (isolated from your main venv)
#   3. Installs only the PR's dependencies inside that sandbox
#   4. Runs the tool's tests
#   5. Cleans up the sandbox venv when done
#

set -euo pipefail

PR_NUMBER="${1:-}"
LIVE_MODE="${2:-}"
API_KEY="${3:-}"

if [ -z "$PR_NUMBER" ]; then
    echo "Usage: ./scripts/review_pr.sh <PR_NUMBER> [--live <API_KEY>]"
    exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SANDBOX_DIR="$REPO_ROOT/.sandbox-pr-$PR_NUMBER"
SANDBOX_VENV="$SANDBOX_DIR/venv"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  PR #$PR_NUMBER Review Sandbox"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ─── Step 1: Fetch the PR ─────────────────────────────────────────
echo ""
echo "📥 Fetching PR #$PR_NUMBER..."
cd "$REPO_ROOT"
git fetch origin "pull/$PR_NUMBER/head:pr-$PR_NUMBER" 2>/dev/null || {
    echo "❌ Failed to fetch PR #$PR_NUMBER. Does it exist?"
    exit 1
}
git checkout "pr-$PR_NUMBER"

# ─── Step 2: Detect which tool(s) changed ─────────────────────────
echo ""
echo "🔍 Detecting changed tools..."
CHANGED_TOOLS=$(git diff --name-only main..."pr-$PR_NUMBER" -- 'tools/' | \
    grep -v '_template' | \
    sed 's|tools/\([^/]*\)/.*|\1|' | \
    sort -u)

if [ -z "$CHANGED_TOOLS" ]; then
    echo "⚠️  No tool changes detected in this PR."
    echo "   Changed files:"
    git diff --name-only main..."pr-$PR_NUMBER"
    exit 0
fi

echo "   Found tool(s): $CHANGED_TOOLS"

# ─── Step 3: Security pre-check ───────────────────────────────────
echo ""
echo "🔒 Security Pre-Check — Review these before proceeding:"
echo "───────────────────────────────────────────────────────"

for tool in $CHANGED_TOOLS; do
    TOOL_DIR="$REPO_ROOT/tools/$tool"

    echo ""
    echo "📦 tools/$tool/requirements.txt:"
    if [ -f "$TOOL_DIR/requirements.txt" ]; then
        cat "$TOOL_DIR/requirements.txt" | sed 's/^/   /'
    else
        echo "   (none)"
    fi

    echo ""
    echo "🔎 Suspicious patterns in tools/$tool/:"
    # Look for env var access, network calls, subprocess, eval, exec
    SUSPICIOUS=$(grep -rn \
        -e 'os\.environ' \
        -e 'subprocess' \
        -e 'eval(' \
        -e 'exec(' \
        -e 'requests\.get\|requests\.post\|urllib\|httpx\|aiohttp' \
        -e '__import__' \
        -e 'socket\.' \
        "$TOOL_DIR" --include="*.py" 2>/dev/null | \
        grep -v 'synth_client' | \
        grep -v 'test_' || true)

    if [ -z "$SUSPICIOUS" ]; then
        echo "   ✅ No suspicious patterns found"
    else
        echo "   ⚠️  REVIEW THESE LINES:"
        echo "$SUSPICIOUS" | sed 's/^/   /'
    fi
done

echo ""
echo "───────────────────────────────────────────────────────"
read -p "Continue with sandbox testing? (y/n): " CONFIRM
if [ "$CONFIRM" != "y" ]; then
    echo "Aborted."
    git checkout main
    exit 0
fi

# ─── Step 4: Create sandbox venv ──────────────────────────────────
echo ""
echo "📦 Creating sandbox virtual environment..."
mkdir -p "$SANDBOX_DIR"
python3 -m venv "$SANDBOX_VENV"
source "$SANDBOX_VENV/bin/activate"

# Install base deps
pip install -q requests pytest

# Install tool-specific deps
for tool in $CHANGED_TOOLS; do
    TOOL_DIR="$REPO_ROOT/tools/$tool"
    if [ -f "$TOOL_DIR/requirements.txt" ]; then
        echo "   Installing deps for tools/$tool..."
        pip install -q -r "$TOOL_DIR/requirements.txt"
    fi
done

# ─── Step 5: Run tests ───────────────────────────────────────────
echo ""

if [ "$LIVE_MODE" = "--live" ] && [ -n "$API_KEY" ]; then
    echo "🔴 LIVE MODE — Running tests with real API key"
    export SYNTH_API_KEY="$API_KEY"
else
    echo "🟢 MOCK MODE — Running tests with mock data (safe)"
    unset SYNTH_API_KEY 2>/dev/null || true
fi

for tool in $CHANGED_TOOLS; do
    TOOL_DIR="$REPO_ROOT/tools/$tool"
    echo ""
    echo "🧪 Testing tools/$tool..."

    if [ -d "$TOOL_DIR/tests" ]; then
        python -m pytest "$TOOL_DIR/tests" -v --tb=short || true
    else
        echo "   ⚠️  No tests/ directory found"
    fi

    if [ -f "$TOOL_DIR/main.py" ]; then
        echo ""
        echo "🚀 Running tools/$tool/main.py..."
        timeout 30 python "$TOOL_DIR/main.py" || echo "   ⚠️  main.py exited with error or timed out"
    fi
done

# ─── Step 6: Cleanup ─────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Review complete!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
deactivate 2>/dev/null || true

read -p "Clean up sandbox and switch back to main? (y/n): " CLEANUP
if [ "$CLEANUP" = "y" ]; then
    rm -rf "$SANDBOX_DIR"
    git checkout main
    git branch -D "pr-$PR_NUMBER" 2>/dev/null || true
    echo "🧹 Cleaned up."
else
    echo "Sandbox left at: $SANDBOX_DIR"
    echo "Branch: pr-$PR_NUMBER"
    echo "To clean up later: rm -rf $SANDBOX_DIR && git checkout main && git branch -D pr-$PR_NUMBER"
fi
