#!/usr/bin/env bash
# =========================================================================
#  agent.sh — Lightweight Linux Native Agent (Zero-Dependency)
#
#  Collects detailed CPU, RAM, disk, network, service metrics, and
#  file/directory monitoring data directly from /proc and standard
#  OS utilities, then posts them as JSON to the Central Server.
# =========================================================================

set -euo pipefail

# Locate script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${SCRIPT_DIR}/config.json"

# Default fallback configurations
SERVER_URL="http://127.0.0.1:8000/api/report"
INTERVAL=10
HOSTNAME_OVERRIDE=""
DISK_MOUNTS=("/")
SERVICES=()
WATCHED_FILES=()
WATCHED_DIRECTORIES=()
LOG_FILE_PATH=""
SYSLOG_ENABLED="false"

# -------------------------------------------------------------------------
# Structured Logging (stdout + file + syslog)
# -------------------------------------------------------------------------
log_message() {
    local level="${1}"
    local msg="${2}"
    local log_line
    log_line="$(date -u +"%Y-%m-%dT%H:%M:%SZ") [${level}] ${msg}"

    # 1. Always print to stdout (journald captures this under systemd)
    echo "${log_line}"

    # 2. Append to log file if configured
    if [[ -n "${LOG_FILE_PATH}" ]]; then
        mkdir -p "$(dirname "${LOG_FILE_PATH}")" 2>/dev/null || true
        echo "${log_line}" >> "${LOG_FILE_PATH}"
    fi

    # 3. Send to syslog via logger if enabled
    if [[ "${SYSLOG_ENABLED}" == "true" ]]; then
        local syslog_priority="local0.info"
        case "${level}" in
            ERROR)   syslog_priority="local0.err" ;;
            WARNING) syslog_priority="local0.warning" ;;
            INFO)    syslog_priority="local0.info" ;;
            DEBUG)   syslog_priority="local0.debug" ;;
        esac
        logger -t sysmon-agent -p "${syslog_priority}" "${msg}" 2>/dev/null || true
    fi
}

# -------------------------------------------------------------------------
# Configuration Parser (Simple JSON parser using grep/sed/awk)
# -------------------------------------------------------------------------
load_config() {
    if [[ -f "${CONFIG_FILE}" ]]; then
        # Extract string values
        SERVER_URL=$(grep -o '"server_url": *"[^"]*"' "${CONFIG_FILE}" | head -n1 | cut -d'"' -f4 || echo "${SERVER_URL}")
        HOSTNAME_OVERRIDE=$(grep -o '"hostname_override": *"[^"]*"' "${CONFIG_FILE}" | head -n1 | cut -d'"' -f4 || echo "")

        # Extract integer values
        INTERVAL=$(grep -o '"interval": *[0-9]*' "${CONFIG_FILE}" | head -n1 | grep -o '[0-9]*' || echo "${INTERVAL}")

        # Extract disk mount points array
        local mounts_raw
        mounts_raw=$(grep -o '"disk_mount_points": *\[[^]*]*\]' "${CONFIG_FILE}" || echo "")
        if [[ -n "${mounts_raw}" ]]; then
            mapfile -t DISK_MOUNTS < <(echo "${mounts_raw}" | grep -o '"[^"]*"' | tr -d '"')
        fi

        # Extract services array
        local services_raw
        services_raw=$(grep -o '"services": *\[[^]*]*\]' "${CONFIG_FILE}" || echo "")
        if [[ -n "${services_raw}" ]]; then
            mapfile -t SERVICES < <(echo "${services_raw}" | grep -o '"[^"]*"' | tr -d '"')
        fi

        # Extract watched_files array
        local files_raw
        files_raw=$(grep -o '"watched_files": *\[[^]*]*\]' "${CONFIG_FILE}" || echo "")
        if [[ -n "${files_raw}" ]]; then
            mapfile -t WATCHED_FILES < <(echo "${files_raw}" | grep -o '"[^"]*"' | tr -d '"')
        fi

        # Extract watched_directories array
        local dirs_raw
        dirs_raw=$(grep -o '"watched_directories": *\[[^]*]*\]' "${CONFIG_FILE}" || echo "")
        if [[ -n "${dirs_raw}" ]]; then
            mapfile -t WATCHED_DIRECTORIES < <(echo "${dirs_raw}" | grep -o '"[^"]*"' | tr -d '"')
        fi

        # Extract logging config
        LOG_FILE_PATH=$(grep -o '"log_file": *"[^"]*"' "${CONFIG_FILE}" | head -n1 | cut -d'"' -f4 || echo "")
        SYSLOG_ENABLED=$(grep -o '"syslog_enabled": *[a-z]*' "${CONFIG_FILE}" | head -n1 | grep -o '[a-z]*$' || echo "false")
    fi

    # Determine hostname
    if [[ -n "${HOSTNAME_OVERRIDE}" ]]; then
        HOST="${HOSTNAME_OVERRIDE}"
    else
        HOST=$(hostname)
    fi
}

# -------------------------------------------------------------------------
# Metric Collection Helpers
# -------------------------------------------------------------------------
get_cpu_usage() {
    # Read /proc/stat twice with 1s interval to calculate CPU usage %
    local stat1
    stat1=$(grep '^cpu ' /proc/stat)
    sleep 1
    local stat2
    stat2=$(grep '^cpu ' /proc/stat)

    # Parse ticks
    local ticks1=(${stat1})
    local ticks2=(${stat2})

    # Calculations: user nice system idle iowait irq softirq steal
    local idle1=$((ticks1[4] + ticks1[5]))
    local idle2=$((ticks2[4] + ticks2[5]))

    local non_idle1=$((ticks1[1] + ticks1[2] + ticks1[3] + ticks1[6] + ticks1[7] + ticks1[8]))
    local non_idle2=$((ticks2[1] + ticks2[2] + ticks2[3] + ticks2[6] + ticks2[7] + ticks2[8]))

    local total1=$((idle1 + non_idle1))
    local total2=$((idle2 + non_idle2))

    local total_delta=$((total2 - total1))
    local idle_delta=$((idle2 - idle1))

    local cpu_percent=0
    if (( total_delta > 0 )); then
        cpu_percent=$(awk "BEGIN {print (1 - ${idle_delta} / ${total_delta}) * 100}")
    fi

    # Load average
    local load_1m load_5m load_15m
    read -r load_1m load_5m load_15m _ < /proc/loadavg

    local logical_cores
    logical_cores=$(nproc)

    # Output CPU JSON chunk
    cat <<EOF
"cpu": {
    "cpu_percent": ${cpu_percent},
    "cpu_count_logical": ${logical_cores},
    "load_1m": ${load_1m},
    "load_5m": ${load_5m},
    "load_15m": ${load_15m}
}
EOF
}

get_memory_usage() {
    local mem_total=0 mem_free=0 mem_avail=0 mem_buf=0 mem_cached=0 mem_swap_total=0 mem_swap_free=0
    
    # Parse MemInfo (values are in kB)
    while read -r name value unit; do
        case "${name}" in
            MemTotal:) mem_total=$((value * 1024)) ;;
            MemFree:) mem_free=$((value * 1024)) ;;
            MemAvailable:) mem_avail=$((value * 1024)) ;;
            Buffers:) mem_buf=$((value * 1024)) ;;
            Cached:) mem_cached=$((value * 1024)) ;;
            SwapTotal:) mem_swap_total=$((value * 1024)) ;;
            SwapFree:) mem_swap_free=$((value * 1024)) ;;
        esac
    done < /proc/meminfo

    local mem_used=$((mem_total - mem_avail))
    local mem_percent=0
    if (( mem_total > 0 )); then
        mem_percent=$(awk "BEGIN {print (${mem_used} / ${mem_total}) * 100}")
    fi

    local swap_used=$((mem_swap_total - mem_swap_free))

    cat <<EOF
"memory": {
    "total_bytes": ${mem_total},
    "available_bytes": ${mem_avail},
    "used_bytes": ${mem_used},
    "used_percent": ${mem_percent},
    "buffers_bytes": ${mem_buf},
    "cached_bytes": ${mem_cached},
    "swap_total_bytes": ${mem_swap_total},
    "swap_used_bytes": ${swap_used}
}
EOF
}

get_disk_usage() {
    # 1. Capacity metrics per mount point
    local disk_json="{"
    local first=true
    for mount in "${DISK_MOUNTS[@]}"; do
        if [[ ! -d "${mount}" ]]; then
            continue
        fi
        
        # Read from df: blocks, used, available, percent
        local df_info
        df_info=$(df -B1 "${mount}" | tail -n 1)
        # Parse fields: Filesystem size used avail use% mount
        local size used avail percent
        size=$(echo "${df_info}" | awk '{print $2}')
        used=$(echo "${df_info}" | awk '{print $3}')
        avail=$(echo "${df_info}" | awk '{print $4}')
        percent=$(echo "${df_info}" | awk '{print $5}' | tr -d '%')

        if [ "${first}" = true ]; then
            first=false
        else
            disk_json+=", "
        fi
        
        disk_json+="\"${mount}\": {
            \"total_bytes\": ${size},
            \"used_bytes\": ${used},
            \"free_bytes\": ${avail},
            \"used_percent\": ${percent}
        }"
    done
    disk_json+="}"

    # 2. IO stats (reads, writes sectors)
    # diskstats contains sectors read (field 6) and sectors written (field 10) for devices
    # We focus on the primary disk (usually sda, sdb, nvme0n1, etc.)
    local disk_dev="sda"
    if [[ -b "/dev/nvme0n1" ]]; then
        disk_dev="nvme0n1"
    elif [[ -b "/dev/vda" ]]; then
        disk_dev="vda"
    fi

    local sectors_read=0 sectors_write=0
    if [[ -f "/proc/diskstats" ]]; then
        local stats
        stats=$(grep " ${disk_dev} " /proc/diskstats || echo "")
        if [[ -n "${stats}" ]]; then
            sectors_read=$(echo "${stats}" | awk '{print $6}')
            sectors_write=$(echo "${stats}" | awk '{print $10}')
        fi
    fi
    # Convert sectors to bytes (1 sector = 512 bytes)
    local io_read_bytes=$((sectors_read * 512))
    local io_write_bytes=$((sectors_write * 512))

    cat <<EOF
"disk": ${disk_json},
"disk_io": {
    "device": "${disk_dev}",
    "read_bytes": ${io_read_bytes},
    "write_bytes": ${io_write_bytes}
}
EOF
}

get_network_usage() {
    # Loop through interfaces in /proc/net/dev
    local net_json="{"
    local first=true
    
    while read -r line; do
        # Format: eth0: 12345 123 0 0 ...
        if [[ "${line}" =~ : ]]; then
            local iface
            iface=$(echo "${line}" | cut -d':' -f1 | tr -d ' ')
            # Skip loopback
            if [ "${iface}" = "lo" ]; then
                continue
            fi
            
            local data
            data=$(echo "${line}" | cut -d':' -f2)
            local rx_bytes
            rx_bytes=$(echo "${data}" | awk '{print $1}')
            local tx_bytes
            tx_bytes=$(echo "${data}" | awk '{print $9}')

            if [ "${first}" = true ]; then
                first=false
            else
                net_json+=", "
            fi
            net_json+="\"${iface}\": {
                \"rx_bytes\": ${rx_bytes},
                \"tx_bytes\": ${tx_bytes}
            }"
        fi
    done < /proc/net/dev
    net_json+="}"

    cat <<EOF
"network": ${net_json}
EOF
}

get_services_status() {
    local services_json="{"
    local first=true

    for service in "${SERVICES[@]}"; do
        local status="inactive"
        if systemctl is-active --quiet "${service}" 2>/dev/null; then
            status="active"
        elif systemctl status "${service}" 2>/dev/null | grep -q "failed"; then
            status="failed"
        fi

        if [ "${first}" = true ]; then
            first=false
        else
            services_json+=", "
        fi
        services_json+="\"${service}\": \"${status}\""
    done
    services_json+="}"

    cat <<EOF
"services": ${services_json}
EOF
}

# -------------------------------------------------------------------------
# File & Directory Monitoring
# -------------------------------------------------------------------------
get_file_monitoring() {
    local items_json="["
    local first=true

    # 1. Monitor individual files
    for filepath in "${WATCHED_FILES[@]}"; do
        if [ "${first}" = true ]; then
            first=false
        else
            items_json+=", "
        fi

        if [[ -f "${filepath}" ]]; then
            local fsize fmtime fhash
            fsize=$(stat -c %s "${filepath}" 2>/dev/null || echo 0)
            fmtime=$(stat -c %Y "${filepath}" 2>/dev/null || echo 0)
            fhash=$(md5sum "${filepath}" 2>/dev/null | awk '{print $1}' || echo "unknown")

            items_json+="{"
            items_json+="\"path\": \"${filepath}\", "
            items_json+="\"is_directory\": false, "
            items_json+="\"exists\": true, "
            items_json+="\"size_bytes\": ${fsize}, "
            items_json+="\"modified_time\": ${fmtime}, "
            items_json+="\"hash\": \"${fhash}\", "
            items_json+="\"file_count\": 0"
            items_json+="}"
        else
            items_json+="{"
            items_json+="\"path\": \"${filepath}\", "
            items_json+="\"is_directory\": false, "
            items_json+="\"exists\": false, "
            items_json+="\"size_bytes\": 0, "
            items_json+="\"modified_time\": 0, "
            items_json+="\"hash\": \"\", "
            items_json+="\"file_count\": 0"
            items_json+="}"
        fi
    done

    # 2. Monitor directories
    for dirpath in "${WATCHED_DIRECTORIES[@]}"; do
        if [ "${first}" = true ]; then
            first=false
        else
            items_json+=", "
        fi

        if [[ -d "${dirpath}" ]]; then
            local dsize dcount dmtime
            dsize=$(du -sb "${dirpath}" 2>/dev/null | awk '{print $1}' || echo 0)
            dcount=$(find "${dirpath}" -type f 2>/dev/null | wc -l || echo 0)
            dmtime=$(stat -c %Y "${dirpath}" 2>/dev/null || echo 0)

            items_json+="{"
            items_json+="\"path\": \"${dirpath}\", "
            items_json+="\"is_directory\": true, "
            items_json+="\"exists\": true, "
            items_json+="\"size_bytes\": ${dsize}, "
            items_json+="\"modified_time\": ${dmtime}, "
            items_json+="\"hash\": \"\", "
            items_json+="\"file_count\": ${dcount}"
            items_json+="}"
        else
            items_json+="{"
            items_json+="\"path\": \"${dirpath}\", "
            items_json+="\"is_directory\": true, "
            items_json+="\"exists\": false, "
            items_json+="\"size_bytes\": 0, "
            items_json+="\"modified_time\": 0, "
            items_json+="\"hash\": \"\", "
            items_json+="\"file_count\": 0"
            items_json+="}"
        fi
    done

    items_json+="]"

    cat <<EOF
"file_monitoring": ${items_json}
EOF
}

# -------------------------------------------------------------------------
# Main Collection Loop
# -------------------------------------------------------------------------
collect_and_send() {
    local timestamp
    timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

    # Gather metrics (CPU will sleep 1s inside, which is fine)
    local cpu_chunk
    cpu_chunk=$(get_cpu_usage)
    local mem_chunk
    mem_chunk=$(get_memory_usage)
    local disk_chunk
    disk_chunk=$(get_disk_usage)
    local net_chunk
    net_chunk=$(get_network_usage)
    local svc_chunk
    svc_chunk=$(get_services_status)
    local file_chunk
    file_chunk=$(get_file_monitoring)

    # Construct complete JSON payload
    local payload
    payload=$(cat <<EOF
{
    "timestamp": "${timestamp}",
    "os": "Linux",
    "hostname": "${HOST}",
    ${cpu_chunk},
    ${mem_chunk},
    ${disk_chunk},
    ${net_chunk},
    ${svc_chunk},
    ${file_chunk}
}
EOF
)

    log_message "INFO" "Sending metrics report to ${SERVER_URL}..."
    if curl -s -X POST -H "Content-Type: application/json" -d "${payload}" "${SERVER_URL}" > /dev/null; then
        log_message "INFO" "Metrics sent successfully"
    else
        log_message "ERROR" "Failed to send metrics to Central Server"
    fi
}

# Run loop
load_config
log_message "INFO" "sysmon-agent Native Linux starts monitoring node: ${HOST}"
log_message "INFO" "Server Target: ${SERVER_URL}"
log_message "INFO" "Reporting every ${INTERVAL}s. Watched files: ${#WATCHED_FILES[@]}, Watched dirs: ${#WATCHED_DIRECTORIES[@]}"
if [[ -n "${LOG_FILE_PATH}" ]]; then
    log_message "INFO" "Logging to file: ${LOG_FILE_PATH}"
fi
if [[ "${SYSLOG_ENABLED}" == "true" ]]; then
    log_message "INFO" "Syslog integration enabled"
fi

while true; do
    collect_and_send
    # Sleep remaining interval (adjusting for CPU delta sleep of 1 second)
    sleep $((INTERVAL > 1 ? INTERVAL - 1 : 1))
done
