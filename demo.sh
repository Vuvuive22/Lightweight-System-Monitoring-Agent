#!/usr/bin/env bash
# =========================================================================
#  demo.sh — Interactive demo of sysmon-agent
#
#  This script demonstrates all core features of the agent without
#  requiring the .deb package.  Run directly from the project root.
#
#  Usage:
#    chmod +x demo.sh
#    sudo ./demo.sh          # root recommended for broad monitoring
#
#  What it does:
#    1. Creates a temporary config for the demo
#    2. Starts the agent in the background
#    3. Demonstrates metrics collection (waits for 2 cycles)
#    4. Demonstrates filesystem monitoring (creates/modifies/deletes files)
#    5. Displays collected logs
#    6. Cleans up and exits
# =========================================================================

set -euo pipefail

# Colors for terminal output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

DEMO_DIR=$(mktemp -d /tmp/sysmon-demo.XXXXXX)
DEMO_LOG="${DEMO_DIR}/demo.log"
DEMO_CONFIG="${DEMO_DIR}/config.json"
DEMO_WATCH_DIR="${DEMO_DIR}/watched"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_PID=""

# -------------------------------------------------------------------
# Cleanup function
# -------------------------------------------------------------------
cleanup() {
    echo -e "\n${YELLOW}[CLEANUP]${NC} Stopping agent and cleaning up..."
    if [[ -n "${AGENT_PID}" ]] && kill -0 "${AGENT_PID}" 2>/dev/null; then
        kill -SIGTERM "${AGENT_PID}" 2>/dev/null || true
        wait "${AGENT_PID}" 2>/dev/null || true
        echo -e "${GREEN}[✓]${NC} Agent stopped (PID ${AGENT_PID})"
    fi
    echo -e "${GREEN}[✓]${NC} Demo files at: ${DEMO_DIR}"
    echo -e "    Log: ${DEMO_LOG}"
}
trap cleanup EXIT

# -------------------------------------------------------------------
# Header
# -------------------------------------------------------------------
echo -e "${BOLD}${CYAN}"
echo "╔══════════════════════════════════════════════════════════╗"
echo "║     Lightweight System Monitoring Agent — Live Demo      ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# -------------------------------------------------------------------
# Step 1: Check dependencies
# -------------------------------------------------------------------
echo -e "${BOLD}[STEP 1/6] Checking dependencies...${NC}"

MISSING=""
python3 -c "import psutil" 2>/dev/null || MISSING="${MISSING} psutil"
python3 -c "import watchdog" 2>/dev/null || MISSING="${MISSING} watchdog"

if [[ -n "${MISSING}" ]]; then
    echo -e "${RED}[✗] Missing Python packages:${MISSING}${NC}"
    echo -e "    Install with: pip3 install${MISSING}"
    exit 1
fi
echo -e "${GREEN}[✓]${NC} Python 3 + psutil + watchdog available"

# -------------------------------------------------------------------
# Step 2: Create demo config
# -------------------------------------------------------------------
echo -e "\n${BOLD}[STEP 2/6] Creating demo configuration...${NC}"

mkdir -p "${DEMO_WATCH_DIR}"

cat > "${DEMO_CONFIG}" <<EOF
{
    "interval": 10,
    "disk_mount_points": ["/"],
    "monitored_paths": ["${DEMO_WATCH_DIR}"],
    "dashboard": {
        "enabled": false
    },
    "logging": {
        "mode": "file",
        "log_file_path": "${DEMO_LOG}",
        "max_bytes": 5242880,
        "backup_count": 3,
        "syslog_address": "127.0.0.1",
        "syslog_port": 514,
        "syslog_protocol": "udp"
    }
}
EOF

echo -e "${GREEN}[✓]${NC} Config created: ${DEMO_CONFIG}"
echo -e "    Interval: 10s | Watch dir: ${DEMO_WATCH_DIR}"
echo -e "    Log file: ${DEMO_LOG}"

# -------------------------------------------------------------------
# Step 3: Start the agent
# -------------------------------------------------------------------
echo -e "\n${BOLD}[STEP 3/6] Starting sysmon-agent...${NC}"

cd "${SCRIPT_DIR}"
PYTHONPATH="${SCRIPT_DIR}" python3 -m agent.main "${DEMO_CONFIG}" &
AGENT_PID=$!

sleep 2

if kill -0 "${AGENT_PID}" 2>/dev/null; then
    echo -e "${GREEN}[✓]${NC} Agent running (PID ${AGENT_PID})"
else
    echo -e "${RED}[✗]${NC} Agent failed to start!"
    cat "${DEMO_LOG}" 2>/dev/null || echo "(no log output)"
    exit 1
fi

# -------------------------------------------------------------------
# Step 4: Wait for first metrics collection
# -------------------------------------------------------------------
echo -e "\n${BOLD}[STEP 4/6] Waiting for metrics collection (≈12 seconds)...${NC}"
echo -e "    ${CYAN}The agent collects CPU, RAM, Disk, and Network stats...${NC}"

sleep 12

echo -e "${GREEN}[✓]${NC} Metrics collected! Showing latest snapshot:\n"

# Extract and pretty-print the most recent metrics line
if command -v python3 &>/dev/null; then
    grep "metrics_snapshot" "${DEMO_LOG}" | tail -1 | \
        python3 -c "
import sys, json
for line in sys.stdin:
    # Extract JSON from the log line
    try:
        data = json.loads(line)
        msg = data.get('message', '')
    except json.JSONDecodeError:
        msg = line
    if 'metrics_snapshot' in str(msg):
        # The message field contains 'metrics_snapshot {...}'
        json_str = str(msg).replace('metrics_snapshot ', '', 1)
        try:
            obj = json.loads(json_str)
            print(json.dumps(obj, indent=2))
        except json.JSONDecodeError:
            print(msg)
" 2>/dev/null || grep "metrics_snapshot" "${DEMO_LOG}" | tail -1
fi

# -------------------------------------------------------------------
# Step 5: Demonstrate filesystem monitoring
# -------------------------------------------------------------------
echo -e "\n${BOLD}[STEP 5/6] Demonstrating filesystem monitoring...${NC}"

echo -e "    ${CYAN}→ Creating file: ${DEMO_WATCH_DIR}/test_file.txt${NC}"
echo "Hello from sysmon-agent demo!" > "${DEMO_WATCH_DIR}/test_file.txt"
sleep 1

echo -e "    ${CYAN}→ Modifying file: ${DEMO_WATCH_DIR}/test_file.txt${NC}"
echo "This line was appended." >> "${DEMO_WATCH_DIR}/test_file.txt"
sleep 1

echo -e "    ${CYAN}→ Creating another file: ${DEMO_WATCH_DIR}/another.log${NC}"
echo "Second file" > "${DEMO_WATCH_DIR}/another.log"
sleep 1

echo -e "    ${CYAN}→ Deleting file: ${DEMO_WATCH_DIR}/another.log${NC}"
rm -f "${DEMO_WATCH_DIR}/another.log"
sleep 2

echo -e "\n${GREEN}[✓]${NC} Filesystem events detected:\n"

grep "fs_event" "${DEMO_LOG}" | while IFS= read -r line; do
    if command -v python3 &>/dev/null; then
        echo "${line}" | python3 -c "
import sys, json
for l in sys.stdin:
    try:
        data = json.loads(l)
        msg = data.get('message', '')
    except json.JSONDecodeError:
        msg = l
    if 'fs_event' in str(msg):
        json_str = str(msg).replace('fs_event ', '', 1)
        try:
            obj = json.loads(json_str)
            etype = obj.get('event_type', '?')
            src = obj.get('src_path', '?')
            size = obj.get('file_size', 'N/A')
            ts = obj.get('timestamp', '?')[:19]
            print(f'    [{etype:>10}] {src}  (size: {size})  @ {ts}')
        except json.JSONDecodeError:
            print(f'    {msg}')
" 2>/dev/null || echo "    ${line}"
    fi
done

# -------------------------------------------------------------------
# Step 6: Summary
# -------------------------------------------------------------------
echo -e "\n${BOLD}[STEP 6/6] Demo Summary${NC}"
echo -e "${CYAN}─────────────────────────────────────────────────────────${NC}"

TOTAL_LINES=$(wc -l < "${DEMO_LOG}" 2>/dev/null || echo 0)
METRICS_COUNT=$(grep -c "metrics_snapshot" "${DEMO_LOG}" 2>/dev/null || echo 0)
FS_COUNT=$(grep -c "fs_event" "${DEMO_LOG}" 2>/dev/null || echo 0)

echo -e "  Total log entries:        ${BOLD}${TOTAL_LINES}${NC}"
echo -e "  Metrics snapshots:        ${BOLD}${METRICS_COUNT}${NC}"
echo -e "  Filesystem events:        ${BOLD}${FS_COUNT}${NC}"
echo -e "  Agent PID:                ${BOLD}${AGENT_PID}${NC}"
echo -e "  Log file:                 ${BOLD}${DEMO_LOG}${NC}"

echo -e "${CYAN}─────────────────────────────────────────────────────────${NC}"
echo -e "\n${GREEN}${BOLD}✓ Demo complete!${NC}"
echo -e "  Full log: ${YELLOW}cat ${DEMO_LOG} | python3 -m json.tool${NC}"
echo -e "  The agent is now being stopped..."
