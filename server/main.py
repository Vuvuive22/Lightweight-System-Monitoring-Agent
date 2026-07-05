"""FastAPI Central Server for monitoring native agents.

Performs metric collection, alerts checks, anomaly detection,
and file/directory integrity monitoring.
Serves a multi-node Web Dashboard.
"""

from __future__ import annotations

import logging
import statistics
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from server import database
from server.config import load_server_config

# Setup server logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("sysmon-server")

# Load configuration
config = load_server_config()
ALERT_COOLDOWN = config.get("alert_cooldown_seconds", 300)


# -------------------------------------------------------------------------
# Application Lifespan (startup / shutdown)
# -------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(application: FastAPI):
    """Prepare database and start background tasks on startup."""
    database.init_db()

    # Start offline detection worker thread
    t = threading.Thread(target=_offline_check_loop, name="sysmon-offline-checker")
    t.daemon = True
    t.start()
    logger.info("Offline nodes detection thread started")

    yield  # Application is running

    logger.info("Sysmon Central Server shutting down")


app = FastAPI(title="Sysmon Central Monitor", lifespan=lifespan)

# Mount Static UI Dashboard
STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Dictionary to prevent spamming notifications (metric alert cooldowns)
# Format: { (hostname, metric_name): last_alert_time }
_last_alert_time: dict[tuple[str, str], float] = {}
_service_states: dict[tuple[str, str], str] = {}


# -------------------------------------------------------------------------
# Core Monitoring Logic (Alerts & Anomaly Checks)
# -------------------------------------------------------------------------

def evaluate_thresholds(hostname: str, timestamp: str, data: dict[str, Any]) -> None:
    """Evaluate metric values against static thresholds."""
    thresholds = config.get("alert_thresholds", {})
    now_ts = time.time()
    
    # 1. CPU check
    cpu = data.get("cpu", {})
    cpu_val = cpu.get("cpu_percent")
    if cpu_val is not None and cpu_val >= thresholds.get("cpu_percent", 90.0):
        key = (hostname, "cpu_threshold")
        if now_ts - _last_alert_time.get(key, 0.0) >= ALERT_COOLDOWN:
            _last_alert_time[key] = now_ts
            msg = f"CPU Load high: {cpu_val:.1f}% (ngưỡng: {thresholds.get('cpu_percent')}%)"
            database.save_alert(hostname, timestamp, "THRESHOLD_CPU", "WARNING", msg)

    # 2. Memory check
    memory = data.get("memory", {})
    ram_val = memory.get("used_percent")
    if ram_val is not None and ram_val >= thresholds.get("ram_percent", 95.0):
        key = (hostname, "ram_threshold")
        if now_ts - _last_alert_time.get(key, 0.0) >= ALERT_COOLDOWN:
            _last_alert_time[key] = now_ts
            msg = f"RAM Usage high: {ram_val:.1f}% (ngưỡng: {thresholds.get('ram_percent')}%)"
            database.save_alert(hostname, timestamp, "THRESHOLD_RAM", "WARNING", msg)

    # 3. Disk check
    disk = data.get("disk", {})
    if disk:
        for mount, info in disk.items():
            if info is None:
                continue
            disk_val = info.get("used_percent")
            if disk_val is not None and disk_val >= thresholds.get("disk_percent", 90.0):
                key = (hostname, f"disk_threshold_{mount}")
                if now_ts - _last_alert_time.get(key, 0.0) >= ALERT_COOLDOWN:
                    _last_alert_time[key] = now_ts
                    msg = f"Disk {mount} high: {disk_val:.1f}% (ngưỡng: {thresholds.get('disk_percent')}%)"
                    database.save_alert(hostname, timestamp, "THRESHOLD_DISK", "WARNING", msg)


def evaluate_anomalies(hostname: str, timestamp: str, data: dict[str, Any]) -> None:
    """Run Z-score anomaly checks using historical SQLite metrics database."""
    an_cfg = config.get("anomaly_detection", {})
    if not an_cfg.get("enabled", True):
        return

    window_size = an_cfg.get("window_size", 30)
    z_threshold = an_cfg.get("z_threshold", 2.5)

    # Fetch last metrics from DB
    history = database.get_metrics_history(hostname, limit=window_size)
    if len(history) < 10:  # Need baseline
        return

    # Helper to check a specific metric field
    def check_field(field_name: str, current_value: float, alert_type: str, label: str):
        values = [row[field_name] for row in history if row[field_name] is not None]
        if len(values) < 5:
            return
        
        # Calculate statistics of historical window
        mean = statistics.mean(values)
        stdev = statistics.stdev(values)

        if stdev == 0:
            return

        z_score = (current_value - mean) / stdev
        if abs(z_score) > z_threshold:
            now_ts = time.time()
            key = (hostname, f"anomaly_{field_name}")
            if now_ts - _last_alert_time.get(key, 0.0) >= ALERT_COOLDOWN:
                _last_alert_time[key] = now_ts
                msg = f"Phát hiện bất thường ({label}): {current_value:.1f} (Z-Score: {z_score:.2f}, Mean: {mean:.1f})"
                database.save_alert(hostname, timestamp, alert_type, "WARNING", msg)

    # Check metrics
    cpu = data.get("cpu", {})
    cpu_val = cpu.get("cpu_percent")
    if cpu_val is not None:
        check_field("cpu_percent", cpu_val, "ANOMALY_CPU", "CPU %")

    memory = data.get("memory", {})
    ram_val = memory.get("used_percent")
    if ram_val is not None:
        check_field("ram_percent", ram_val, "ANOMALY_RAM", "RAM %")


def evaluate_services(hostname: str, timestamp: str, services: dict[str, str]) -> None:
    """Detect service state transitions, logging failed/stopped instances."""
    if not services:
        return

    for service_name, status in services.items():
        key = (hostname, service_name)
        prev_status = _service_states.get(key)
        
        # Save current state in cache
        _service_states[key] = status

        # Alert if service stopped or failed (avoid alert on first report)
        if prev_status is not None and prev_status != status:
            if status == "failed":
                msg = f"Dịch vụ [{service_name}] bị CRASH (failed) trên node {hostname}!"
                database.save_alert(hostname, timestamp, "SERVICE_CRASHED", "CRITICAL", msg)
            elif status == "inactive" and prev_status == "active":
                msg = f"Dịch vụ [{service_name}] bị DỪNG (inactive) trên node {hostname}."
                database.save_alert(hostname, timestamp, "SERVICE_STOPPED", "WARNING", msg)
            elif status == "active" and prev_status in ("inactive", "failed"):
                msg = f"Dịch vụ [{service_name}] đã KHỞI ĐỘNG LẠI (active) thành công trên node {hostname}."
                database.save_alert(hostname, timestamp, "SERVICE_STARTED", "INFO", msg)


def evaluate_file_changes(hostname: str, timestamp: str, file_items: list[dict[str, Any]]) -> None:
    """Detect file/directory changes by comparing with previous state in database."""
    if not file_items:
        return

    now_ts = time.time()

    for item in file_items:
        filepath = item.get("path", "")
        if not filepath:
            continue

        current_exists = item.get("exists", True)
        current_hash = item.get("hash", "")
        current_size = item.get("size_bytes", 0)
        is_directory = item.get("is_directory", False)

        # Get previous state from database
        prev_state = database.get_last_file_state(hostname, filepath)

        if prev_state is None:
            # First report for this file — no comparison possible
            continue

        prev_exists = bool(prev_state.get("exists_flag", 1))
        prev_hash = prev_state.get("hash", "")
        prev_size = prev_state.get("size_bytes", 0)

        # 1. File/directory deleted
        if prev_exists and not current_exists:
            key = (hostname, f"file_deleted_{filepath}")
            if now_ts - _last_alert_time.get(key, 0.0) >= ALERT_COOLDOWN:
                _last_alert_time[key] = now_ts
                item_type = "Thư mục" if is_directory else "File"
                msg = f"{item_type} [{filepath}] đã bị XÓA trên node {hostname}!"
                database.save_alert(hostname, timestamp, "FILE_DELETED", "CRITICAL", msg)

        # 2. File hash changed (content modified) — only for files, not directories
        if current_exists and not is_directory and prev_hash and current_hash:
            if prev_hash != current_hash:
                key = (hostname, f"file_modified_{filepath}")
                if now_ts - _last_alert_time.get(key, 0.0) >= ALERT_COOLDOWN:
                    _last_alert_time[key] = now_ts
                    msg = f"File [{filepath}] đã bị THAY ĐỔI NỘI DUNG trên node {hostname} (hash: {prev_hash[:8]}... → {current_hash[:8]}...)"
                    database.save_alert(hostname, timestamp, "FILE_MODIFIED", "WARNING", msg)

        # 3. Size changed significantly (for both files and directories)
        if current_exists and prev_exists and prev_size > 0:
            size_change_ratio = abs(current_size - prev_size) / prev_size if prev_size > 0 else 0
            if size_change_ratio > 0.5:  # More than 50% change
                key = (hostname, f"file_size_{filepath}")
                if now_ts - _last_alert_time.get(key, 0.0) >= ALERT_COOLDOWN:
                    _last_alert_time[key] = now_ts
                    item_type = "Thư mục" if is_directory else "File"
                    msg = f"{item_type} [{filepath}] thay đổi kích thước đáng kể: {prev_size} → {current_size} bytes ({size_change_ratio*100:.0f}%)"
                    database.save_alert(hostname, timestamp, "FILE_SIZE_CHANGED", "INFO", msg)


# -------------------------------------------------------------------------
# API Endpoints
# -------------------------------------------------------------------------

@app.post("/api/report")
async def report_metrics(request: Request, payload: dict[str, Any]):
    """Endpoint for native agents to submit metric reports."""
    # Retrieve client IP
    client_ip = request.client.host if request.client else "unknown"
    
    hostname = payload.get("hostname")
    os_name = payload.get("os", "Linux")
    timestamp = payload.get("timestamp")
    
    if not hostname or not timestamp:
        raise HTTPException(status_code=400, detail="Hostname and timestamp required")

    # Get CPU cores and RAM size
    cpu = payload.get("cpu", {})
    cpu_cores = cpu.get("cpu_count_logical", 1)
    
    memory = payload.get("memory", {})
    ram_total = memory.get("total_bytes", 0)

    # 1. Register/Refresh Node Information
    database.register_node(
        hostname=hostname,
        ip_address=client_ip,
        os_name=os_name,
        cpu_cores=cpu_cores,
        ram_total=ram_total,
        interval_seconds=10,  # assume 10s default
    )

    # Clean up any offline alerts since the node is now active
    database.delete_offline_alerts(hostname)

    # 2. Save Historical Metrics & Service Status
    database.save_metrics(hostname, timestamp, payload)
    
    services = payload.get("services", {})
    database.save_services(hostname, timestamp, services)

    # 3. Save File Monitoring Data
    file_items = payload.get("file_monitoring", [])
    if file_items:
        # Evaluate changes BEFORE saving new state (compare with previous)
        evaluate_file_changes(hostname, timestamp, file_items)
        database.save_file_monitors(hostname, timestamp, file_items)

    # 4. Analyze data for alerts & anomalies
    evaluate_thresholds(hostname, timestamp, payload)
    evaluate_anomalies(hostname, timestamp, payload)
    evaluate_services(hostname, timestamp, services)

    return {"status": "ok", "message": "Metrics saved"}


@app.get("/api/nodes")
def get_nodes():
    """Return list of monitored nodes and statuses."""
    return database.get_nodes()


@app.get("/api/nodes/{hostname}/metrics")
def get_node_metrics(hostname: str, limit: int = 50):
    """Return metrics history for a node."""
    return database.get_metrics_history(hostname, limit)


@app.get("/api/alerts")
def get_alerts(limit: int = 50):
    """Return list of recent alerts."""
    return database.get_alerts_history(limit)


@app.get("/api/nodes/{hostname}/services")
def get_node_services(hostname: str):
    """Return current service statuses for a node."""
    nodes = database.get_nodes()
    node = next((n for n in nodes if n["hostname"] == hostname), None)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    return node.get("services", {})


@app.get("/api/nodes/{hostname}/files")
def get_node_files(hostname: str):
    """Return current file/directory monitoring status for a node."""
    return database.get_file_monitor_history(hostname)


@app.delete("/api/nodes/{hostname}")
def delete_node(hostname: str):
    """Remove a node and all its associated data."""
    nodes = database.get_nodes()
    node = next((n for n in nodes if n["hostname"] == hostname), None)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    database.delete_node(hostname)
    # Clean up in-memory caches
    keys_to_remove = [k for k in _last_alert_time if k[0] == hostname]
    for k in keys_to_remove:
        del _last_alert_time[k]
    keys_to_remove = [k for k in _service_states if k[0] == hostname]
    for k in keys_to_remove:
        del _service_states[k]
    return {"status": "ok", "message": f"Node '{hostname}' deleted"}


@app.get("/", response_class=HTMLResponse)
def index():
    """Root redirect to Web Dashboard."""
    dashboard_file = STATIC_DIR / "index.html"
    if dashboard_file.is_file():
        return dashboard_file.read_text(encoding="utf-8")
    return "<h1>Sysmon Central dashboard.html not found under static/</h1>"


# -------------------------------------------------------------------------
# Heartbeat Monitoring Thread (Offline Node Detection)
# -------------------------------------------------------------------------

def _offline_check_loop() -> None:
    """Periodically check database nodes and alert if any goes offline."""
    time.sleep(5)  # Wait for startup
    while True:
        try:
            nodes = database.get_nodes()
            now_ts = time.time()
            now_iso = datetime.now(timezone.utc).isoformat()
            
            for node in nodes:
                # If calculated as offline
                if not node["online"]:
                    hostname = node["hostname"]
                    key = (hostname, "offline_status")
                    
                    # Alert if it was seen at least once and alert not in cooldown
                    if node["last_seen"] and (now_ts - _last_alert_time.get(key, 0.0) >= ALERT_COOLDOWN):
                        _last_alert_time[key] = now_ts
                        msg = f"⚠ Node [{hostname}] mất kết nối (Mất tín hiệu report từ {node['last_seen']})."
                        database.save_alert(hostname, now_iso, "NODE_OFFLINE", "CRITICAL", msg)
                        logger.warning(msg)
        except Exception as e:
            logger.error("Error in offline checker loop: %s", e)
            
        time.sleep(15)




def run_server() -> None:
    """Run the server using Uvicorn."""
    host = config.get("host", "0.0.0.0")
    port = int(config.get("port", 8000))
    logger.info("Starting Sysmon Central Server on %s:%d ...", host, port)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run_server()
