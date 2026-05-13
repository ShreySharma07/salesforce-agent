#!/usr/bin/env bash
# demo.sh — one-shot demo runner for the agent platform.
#
# Usage:
#   ./scripts/demo.sh path/to/recording.mov
#   ./scripts/demo.sh path/to/recording.mov --name "My Demo"
#
# Prerequisites:
#   - Backend running:  cd backend && python -m app.main
#   - Image built:      docker build -t agent-sandbox -f sandbox/Dockerfile .
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"
BACKEND_URL="http://localhost:8001"
AUTOMATION_NAME="${2:-Demo $(date '+%H:%M:%S')}"

# ── colours ────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BOLD='\033[1m'; NC='\033[0m'
die()  { echo -e "${RED}✗ ERROR: $*${NC}" >&2; exit 1; }
ok()   { echo -e "${GREEN}  ✓ $*${NC}"; }
warn() { echo -e "${YELLOW}  ⚠  $*${NC}"; }
step() { echo -e "\n${BOLD}$*${NC}"; }

# ── args ───────────────────────────────────────────────────────────────────
if [[ $# -lt 1 || "$1" == "-h" || "$1" == "--help" ]]; then
    echo "Usage: $0 <path/to/recording.mov> [automation-name]"
    exit 1
fi
VIDEO_PATH="$(realpath "$1")"
[[ -f "$VIDEO_PATH" ]] || die "Video not found: $VIDEO_PATH"

echo ""
echo -e "${BOLD}══════════════════════════════════════════${NC}"
echo -e "${BOLD}   Agent Platform Demo${NC}"
echo -e "${BOLD}══════════════════════════════════════════${NC}"

# ── 1. Prerequisites ───────────────────────────────────────────────────────
step "[1/4] Checking prerequisites..."

if curl -sf "$BACKEND_URL/health" > /dev/null 2>&1; then
    ok "Backend is running at $BACKEND_URL"
else
    die "Backend is not running.\n\n    Start it with:\n      cd backend && python -m app.main\n"
fi

if docker image inspect agent-sandbox:latest > /dev/null 2>&1; then
    ok "Docker image agent-sandbox:latest found"
else
    die "Docker image agent-sandbox:latest not found.\n\n    Build it with:\n      docker build -t agent-sandbox -f sandbox/Dockerfile $REPO_ROOT\n"
fi

# ── 2. Process video ───────────────────────────────────────────────────────
step "[2/4] Processing video: $VIDEO_PATH"

PLAN_JSON="$(mktemp /tmp/demo_plan_XXXX.json)"
trap 'rm -f "$PLAN_JSON"' EXIT

(cd "$BACKEND_DIR" && python -m scripts.process_video "$VIDEO_PATH" --output "$PLAN_JSON") \
    || die "process_video failed — see output above"

PLAN_ID="$(python3 -c "import json; print(json.load(open('$PLAN_JSON'))['id'])")"
PLAN_GOAL="$(python3 -c "import json; print(json.load(open('$PLAN_JSON'))['goal'])")"
PLAN_STEPS="$(python3 -c "import json; print(len(json.load(open('$PLAN_JSON'))['steps']))")"

ok "Plan ID:   $PLAN_ID"
ok "Goal:      $PLAN_GOAL"
ok "Steps:     $PLAN_STEPS"

# Copy plan into backend local storage so run_plan_e2e can find it
STORAGE_DIR="$BACKEND_DIR/.local_storage/plans"
mkdir -p "$STORAGE_DIR"
cp "$PLAN_JSON" "$STORAGE_DIR/${PLAN_ID}.json"
ok "Saved to   backend/.local_storage/plans/${PLAN_ID}.json"

# ── 3. Confirm ────────────────────────────────────────────────────────────
step "[3/4] Ready to run"
echo ""
echo "  The sandbox will open in your browser automatically (noVNC live view)."
echo "  Press Ctrl-C at any time to abort."
echo ""
read -rp "  Press ENTER to start the run... "

# ── 4. Execute ────────────────────────────────────────────────────────────
step "[4/4] Running plan..."
echo ""

(cd "$BACKEND_DIR" && python -m scripts.run_plan_e2e \
    ".local_storage/plans/${PLAN_ID}.json" \
    --watch \
    --name "$AUTOMATION_NAME")
