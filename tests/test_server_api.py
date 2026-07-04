"""Comprehensive integration tests for the Central Monitoring Server.

Covers API endpoints, multi-node registration, alert logic, config loading,
and the delete node functionality.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server import database
# Force database file to a temporary test file
database.DB_FILE = database.Path(__file__).parent / "test_sysmon.db"

from server.main import app, _last_alert_time, _service_states

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_database():
    """Reset database tables and memory caches before each test."""
    database.init_db()
    with database.get_db_connection() as conn:
        conn.execute("DELETE FROM alerts")
        conn.execute("DELETE FROM metrics")
        conn.execute("DELETE FROM services")
        conn.execute("DELETE FROM nodes")
        conn.commit()
    _last_alert_time.clear()
    _service_states.clear()
    yield


# =========================================================================
# Helper: build a realistic agent payload
# =========================================================================

def build_payload(
    hostname: str = "test-node",
    cpu: float = 25.0,
    ram: float = 50.0,
    disk: float = 40.0,
    services: dict | None = None,
    timestamp: str = "2026-07-03T12:00:00Z",
    os_name: str = "Linux",
) -> dict:
    return {
        "hostname": hostname,
        "timestamp": timestamp,
        "os": os_name,
        "cpu": {
            "cpu_percent": cpu,
            "cpu_count_logical": 4,
            "load_1m": 0.5,
            "load_5m": 0.3,
            "load_15m": 0.1,
        },
        "memory": {
            "total_bytes": 8589934592,
            "available_bytes": int(8589934592 * (100 - ram) / 100),
            "used_bytes": int(8589934592 * ram / 100),
            "used_percent": ram,
        },
        "disk": {
            "/": {
                "total_bytes": 53687091200,
                "used_bytes": int(53687091200 * disk / 100),
                "free_bytes": int(53687091200 * (100 - disk) / 100),
                "used_percent": disk,
            }
        },
        "disk_io": {
            "device": "sda",
            "read_bytes": 102400,
            "write_bytes": 204800,
        },
        "network": {
            "eth0": {
                "rx_bytes": 512000,
                "tx_bytes": 256000,
            }
        },
        "services": services or {},
    }


# =========================================================================
# 1. API Endpoint Tests
# =========================================================================

class TestReportEndpoint:
    """Tests for POST /api/report."""

    def test_report_success(self):
        payload = build_payload(hostname="linux-vm-1")
        resp = client.post("/api/report", json=payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_report_missing_hostname(self):
        payload = {"timestamp": "2026-07-03T12:00:00Z", "cpu": {"cpu_percent": 10.0}}
        resp = client.post("/api/report", json=payload)
        assert resp.status_code == 400

    def test_report_missing_timestamp(self):
        payload = {"hostname": "node-x", "cpu": {"cpu_percent": 10.0}}
        resp = client.post("/api/report", json=payload)
        assert resp.status_code == 400

    def test_report_empty_body(self):
        resp = client.post("/api/report", json={})
        assert resp.status_code == 400


class TestNodesEndpoint:
    """Tests for GET /api/nodes and related node endpoints."""

    def test_empty_nodes(self):
        resp = client.get("/api/nodes")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_nodes_after_report(self):
        client.post("/api/report", json=build_payload(hostname="vm-alpha"))
        resp = client.get("/api/nodes")
        nodes = resp.json()
        assert len(nodes) == 1
        assert nodes[0]["hostname"] == "vm-alpha"
        assert nodes[0]["os"] == "Linux"

    def test_node_records_os_and_cores(self):
        client.post("/api/report", json=build_payload(hostname="win-1", os_name="Windows"))
        nodes = client.get("/api/nodes").json()
        assert nodes[0]["os"] == "Windows"
        assert nodes[0]["cpu_cores"] == 4

    def test_multiple_nodes_registration(self):
        """Multiple nodes should all appear in /api/nodes."""
        for name in ["node-a", "node-b", "node-c"]:
            client.post("/api/report", json=build_payload(hostname=name))
        nodes = client.get("/api/nodes").json()
        hostnames = [n["hostname"] for n in nodes]
        assert "node-a" in hostnames
        assert "node-b" in hostnames
        assert "node-c" in hostnames
        assert len(nodes) == 3


class TestMetricsEndpoint:
    """Tests for GET /api/nodes/{hostname}/metrics."""

    def test_metrics_history_empty(self):
        resp = client.get("/api/nodes/nonexistent/metrics")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_metrics_history_after_reports(self):
        for i in range(5):
            p = build_payload(hostname="vm-1", cpu=10.0 + i, timestamp=f"2026-07-03T12:00:{i:02d}Z")
            client.post("/api/report", json=p)
        metrics = client.get("/api/nodes/vm-1/metrics").json()
        assert len(metrics) == 5
        # Should be in ascending time order
        assert metrics[0]["cpu_percent"] == 10.0
        assert metrics[4]["cpu_percent"] == 14.0

    def test_metrics_limit_parameter(self):
        for i in range(10):
            p = build_payload(hostname="vm-2", cpu=20.0 + i, timestamp=f"2026-07-03T12:{i:02d}:00Z")
            client.post("/api/report", json=p)
        metrics = client.get("/api/nodes/vm-2/metrics?limit=3").json()
        assert len(metrics) == 3

    def test_metrics_stores_disk_io(self):
        client.post("/api/report", json=build_payload(hostname="vm-io"))
        metrics = client.get("/api/nodes/vm-io/metrics").json()
        assert metrics[0]["disk_io_read"] == 102400
        assert metrics[0]["disk_io_write"] == 204800

    def test_metrics_stores_network(self):
        client.post("/api/report", json=build_payload(hostname="vm-net"))
        metrics = client.get("/api/nodes/vm-net/metrics").json()
        assert metrics[0]["net_rx"] == 512000
        assert metrics[0]["net_tx"] == 256000


class TestServicesEndpoint:
    """Tests for GET /api/nodes/{hostname}/services."""

    def test_services_for_node(self):
        payload = build_payload(hostname="svc-node", services={"nginx": "active", "mysql": "inactive"})
        client.post("/api/report", json=payload)
        resp = client.get("/api/nodes/svc-node/services")
        assert resp.status_code == 200
        services = resp.json()
        assert services["nginx"] == "active"
        assert services["mysql"] == "inactive"

    def test_services_node_not_found(self):
        resp = client.get("/api/nodes/ghost-node/services")
        assert resp.status_code == 404


class TestDeleteEndpoint:
    """Tests for DELETE /api/nodes/{hostname}."""

    def test_delete_existing_node(self):
        client.post("/api/report", json=build_payload(hostname="to-delete"))
        resp = client.delete("/api/nodes/to-delete")
        assert resp.status_code == 200
        assert "deleted" in resp.json()["message"]
        # Verify node is gone
        nodes = client.get("/api/nodes").json()
        assert len(nodes) == 0

    def test_delete_nonexistent_node(self):
        resp = client.delete("/api/nodes/ghost-node")
        assert resp.status_code == 404


class TestAlertsEndpoint:
    """Tests for GET /api/alerts."""

    def test_alerts_empty(self):
        resp = client.get("/api/alerts")
        assert resp.status_code == 200
        assert resp.json() == []


class TestDashboard:
    """Tests for GET / (dashboard HTML page)."""

    def test_dashboard_returns_html(self):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Sysmon Central Dashboard" in resp.text


# =========================================================================
# 2. Threshold Alert Tests
# =========================================================================

class TestThresholdAlerts:
    """Static threshold-based alert detection tests."""

    def test_cpu_threshold_alert(self):
        """CPU >= 90% should trigger a THRESHOLD_CPU alert."""
        client.post("/api/report", json=build_payload(hostname="alert-cpu", cpu=95.0))
        alerts = client.get("/api/alerts").json()
        cpu_alerts = [a for a in alerts if a["alert_type"] == "THRESHOLD_CPU"]
        assert len(cpu_alerts) == 1
        assert "CPU Load high" in cpu_alerts[0]["message"]

    def test_cpu_below_threshold_no_alert(self):
        """CPU below 90% should not trigger alert."""
        client.post("/api/report", json=build_payload(hostname="safe-cpu", cpu=50.0))
        alerts = client.get("/api/alerts").json()
        assert len(alerts) == 0

    def test_ram_threshold_alert(self):
        """RAM >= 95% should trigger a THRESHOLD_RAM alert."""
        client.post("/api/report", json=build_payload(hostname="alert-ram", ram=97.0))
        alerts = client.get("/api/alerts").json()
        ram_alerts = [a for a in alerts if a["alert_type"] == "THRESHOLD_RAM"]
        assert len(ram_alerts) == 1

    def test_disk_threshold_alert(self):
        """Disk >= 90% should trigger a THRESHOLD_DISK alert."""
        client.post("/api/report", json=build_payload(hostname="alert-disk", disk=95.0))
        alerts = client.get("/api/alerts").json()
        disk_alerts = [a for a in alerts if a["alert_type"] == "THRESHOLD_DISK"]
        assert len(disk_alerts) == 1
        assert "Disk" in disk_alerts[0]["message"]

    def test_disk_below_threshold_no_alert(self):
        """Disk below 90% should not trigger alert."""
        client.post("/api/report", json=build_payload(hostname="safe-disk", disk=45.0))
        alerts = client.get("/api/alerts").json()
        assert len(alerts) == 0

    def test_multiple_thresholds_at_once(self):
        """CPU and RAM both exceeding thresholds should generate 2 alerts."""
        client.post("/api/report", json=build_payload(hostname="hot-node", cpu=92.0, ram=98.0))
        alerts = client.get("/api/alerts").json()
        types = [a["alert_type"] for a in alerts]
        assert "THRESHOLD_CPU" in types
        assert "THRESHOLD_RAM" in types


# =========================================================================
# 3. Service Monitoring Tests
# =========================================================================

class TestServiceAlerts:
    """Service state change detection tests."""

    def test_service_crash_alert(self):
        """Service going from active to failed should produce CRITICAL alert."""
        client.post("/api/report", json=build_payload(
            hostname="svc-1", services={"nginx": "active"}, timestamp="2026-07-03T12:00:00Z"))
        client.post("/api/report", json=build_payload(
            hostname="svc-1", services={"nginx": "failed"}, timestamp="2026-07-03T12:00:10Z"))
        alerts = client.get("/api/alerts").json()
        assert len(alerts) == 1
        assert alerts[0]["alert_type"] == "SERVICE_CRASHED"
        assert alerts[0]["severity"] == "CRITICAL"

    def test_service_stopped_alert(self):
        """Service going from active to inactive should produce WARNING alert."""
        client.post("/api/report", json=build_payload(
            hostname="svc-2", services={"mysql": "active"}, timestamp="2026-07-03T12:00:00Z"))
        client.post("/api/report", json=build_payload(
            hostname="svc-2", services={"mysql": "inactive"}, timestamp="2026-07-03T12:00:10Z"))
        alerts = client.get("/api/alerts").json()
        assert len(alerts) == 1
        assert alerts[0]["alert_type"] == "SERVICE_STOPPED"

    def test_service_restart_alert(self):
        """Service recovery from inactive to active should produce INFO alert."""
        client.post("/api/report", json=build_payload(
            hostname="svc-3", services={"redis": "active"}, timestamp="2026-07-03T12:00:00Z"))
        client.post("/api/report", json=build_payload(
            hostname="svc-3", services={"redis": "inactive"}, timestamp="2026-07-03T12:00:10Z"))
        client.post("/api/report", json=build_payload(
            hostname="svc-3", services={"redis": "active"}, timestamp="2026-07-03T12:00:20Z"))
        alerts = client.get("/api/alerts").json()
        types = [a["alert_type"] for a in alerts]
        assert "SERVICE_STOPPED" in types
        assert "SERVICE_STARTED" in types

    def test_first_report_no_service_alert(self):
        """First report should NOT trigger any service alerts (no baseline)."""
        client.post("/api/report", json=build_payload(
            hostname="svc-first", services={"nginx": "failed"}))
        alerts = client.get("/api/alerts").json()
        service_alerts = [a for a in alerts if "SERVICE" in a["alert_type"]]
        assert len(service_alerts) == 0


# =========================================================================
# 4. Z-Score Anomaly Detection Tests
# =========================================================================

class TestAnomalyDetection:
    """Z-Score based anomaly detection tests."""

    def _seed_baseline(self, hostname: str, normal_cpu: float = 10.0, count: int = 12):
        """Seed normal baseline metrics to build history."""
        for i in range(count):
            cpu_val = normal_cpu + (i % 3 - 1) * 0.2  # 9.8, 10.0, 10.2
            p = build_payload(hostname=hostname, cpu=cpu_val, timestamp=f"2026-07-03T12:{i:02d}:00Z")
            client.post("/api/report", json=p)

    def test_anomaly_detected_on_spike(self):
        """A sudden CPU spike far above baseline should trigger ANOMALY_CPU."""
        self._seed_baseline("anomaly-1")
        # Send a spike well above normal (35% vs ~10% baseline)
        client.post("/api/report", json=build_payload(
            hostname="anomaly-1", cpu=35.0, timestamp="2026-07-03T12:15:00Z"))
        alerts = client.get("/api/alerts").json()
        anomaly = [a for a in alerts if a["alert_type"] == "ANOMALY_CPU"]
        assert len(anomaly) == 1
        assert "bất thường" in anomaly[0]["message"]

    def test_no_anomaly_on_normal_values(self):
        """Values within normal range should not trigger anomaly alerts."""
        self._seed_baseline("anomaly-2")
        # Send another normal value
        client.post("/api/report", json=build_payload(
            hostname="anomaly-2", cpu=10.5, timestamp="2026-07-03T12:15:00Z"))
        alerts = client.get("/api/alerts").json()
        anomaly = [a for a in alerts if a["alert_type"] == "ANOMALY_CPU"]
        assert len(anomaly) == 0

    def test_anomaly_needs_baseline(self):
        """With too few data points (<10), no anomaly should trigger."""
        for i in range(3):
            client.post("/api/report", json=build_payload(
                hostname="anomaly-3", cpu=10.0, timestamp=f"2026-07-03T12:0{i}:00Z"))
        # Send spike
        client.post("/api/report", json=build_payload(
            hostname="anomaly-3", cpu=90.0, timestamp="2026-07-03T12:05:00Z"))
        alerts = client.get("/api/alerts").json()
        # Only static threshold might fire, not anomaly (insufficient baseline)
        anomaly = [a for a in alerts if "ANOMALY" in a["alert_type"]]
        assert len(anomaly) == 0


# =========================================================================
# 5. Config Loading Tests
# =========================================================================

class TestConfigLoading:
    """Tests for server configuration module."""

    def test_config_defaults(self):
        from server.config import _DEFAULTS
        assert _DEFAULTS["port"] == 8000
        assert _DEFAULTS["alert_thresholds"]["cpu_percent"] == 90.0
        assert _DEFAULTS["anomaly_detection"]["enabled"] is True
        assert _DEFAULTS["anomaly_detection"]["z_threshold"] == 2.5

    def test_load_server_config_returns_dict(self):
        from server.config import load_server_config
        cfg = load_server_config()
        assert isinstance(cfg, dict)
        assert "alert_thresholds" in cfg
        assert "anomaly_detection" in cfg


# =========================================================================
# 6. Database Layer Tests
# =========================================================================

class TestDatabaseLayer:
    """Direct tests for database.py functions."""

    def test_init_db_creates_tables(self):
        database.init_db()
        with database.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row["name"] for row in cursor.fetchall()}
        assert "nodes" in tables
        assert "metrics" in tables
        assert "services" in tables
        assert "alerts" in tables

    def test_register_node_and_retrieve(self):
        database.register_node("db-test-node", "10.0.0.1", "Linux", 2, 4294967296, 10)
        nodes = database.get_nodes()
        assert len(nodes) == 1
        assert nodes[0]["hostname"] == "db-test-node"
        assert nodes[0]["ip_address"] == "10.0.0.1"

    def test_register_node_updates_on_conflict(self):
        """Registering same hostname again should update, not duplicate."""
        database.register_node("dup-node", "10.0.0.1", "Linux", 2, 4000000000, 10)
        database.register_node("dup-node", "10.0.0.2", "Linux", 4, 8000000000, 15)
        nodes = database.get_nodes()
        assert len(nodes) == 1
        assert nodes[0]["ip_address"] == "10.0.0.2"
        assert nodes[0]["cpu_cores"] == 4

    def test_save_and_get_alerts(self):
        database.register_node("alert-node", "10.0.0.1", "Linux", 2, 4294967296, 10)
        database.save_alert("alert-node", "2026-07-03T12:00:00Z", "TEST_ALERT", "INFO", "Test message")
        alerts = database.get_alerts_history(10)
        assert len(alerts) == 1
        assert alerts[0]["alert_type"] == "TEST_ALERT"

    def test_delete_node_removes_all_data(self):
        database.register_node("del-node", "10.0.0.1", "Linux", 2, 4294967296, 10)
        database.save_alert("del-node", "2026-07-03T12:00:00Z", "ALERT", "INFO", "msg")
        database.delete_node("del-node")
        nodes = database.get_nodes()
        assert len(nodes) == 0

    def test_get_metrics_history_ordering(self):
        """Metrics should be returned in ascending timestamp order."""
        database.register_node("order-node", "10.0.0.1", "Linux", 2, 4294967296, 10)
        for i in range(5):
            data = {"cpu": {"cpu_percent": 10.0 + i}, "memory": {}, "disk": {}, "disk_io": {}, "network": {}}
            database.save_metrics("order-node", f"2026-07-03T12:0{i}:00Z", data)
        history = database.get_metrics_history("order-node", limit=5)
        assert history[0]["cpu_percent"] == 10.0
        assert history[4]["cpu_percent"] == 14.0


# =========================================================================
# 7. File Monitoring Database Tests
# =========================================================================

class TestFileMonitoringDB:
    """Tests for file monitoring database operations."""

    def test_file_monitors_table_exists(self):
        """file_monitors table should be created during init_db."""
        with database.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='file_monitors'")
            assert cursor.fetchone() is not None

    def test_save_and_get_file_monitors(self):
        """Save file monitor data and retrieve it."""
        database.register_node("fm-node", "10.0.0.1", "Linux", 2, 4294967296, 10)
        items = [
            {"path": "/etc/passwd", "is_directory": False, "exists": True, "size_bytes": 1024, "modified_time": 1720000000, "hash": "abc123def456", "file_count": 0},
            {"path": "/var/www", "is_directory": True, "exists": True, "size_bytes": 50000, "modified_time": 1720000000, "hash": "", "file_count": 15},
        ]
        database.save_file_monitors("fm-node", "2026-07-03T12:00:00Z", items)
        history = database.get_file_monitor_history("fm-node")
        assert len(history) == 2

    def test_get_last_file_state(self):
        """get_last_file_state should return most recent record for a path."""
        database.register_node("state-node", "10.0.0.1", "Linux", 2, 4294967296, 10)
        database.save_file_monitors("state-node", "2026-07-03T12:00:00Z", [
            {"path": "/etc/passwd", "is_directory": False, "exists": True, "size_bytes": 1000, "modified_time": 1720000000, "hash": "aaa111", "file_count": 0},
        ])
        database.save_file_monitors("state-node", "2026-07-03T12:01:00Z", [
            {"path": "/etc/passwd", "is_directory": False, "exists": True, "size_bytes": 2000, "modified_time": 1720000060, "hash": "bbb222", "file_count": 0},
        ])
        state = database.get_last_file_state("state-node", "/etc/passwd")
        assert state is not None
        assert state["hash"] == "bbb222"
        assert state["size_bytes"] == 2000

    def test_file_monitor_empty_history(self):
        """Should return empty list when no file monitors exist."""
        database.register_node("empty-fm", "10.0.0.1", "Linux", 2, 4294967296, 10)
        history = database.get_file_monitor_history("empty-fm")
        assert len(history) == 0


# =========================================================================
# 8. File Monitoring API & Alert Tests
# =========================================================================

class TestFileMonitoringAPI:
    """Tests for the /api/nodes/{hostname}/files endpoint and file change alerts."""

    def test_files_endpoint_returns_empty(self):
        """GET /api/nodes/{hostname}/files should return empty list initially."""
        payload = build_payload(hostname="file-node")
        client.post("/api/report", json=payload)
        resp = client.get("/api/nodes/file-node/files")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_files_endpoint_returns_data(self):
        """After report with file_monitoring, endpoint should return file data."""
        payload = build_payload(hostname="file-node-2")
        payload["file_monitoring"] = [
            {"path": "/etc/passwd", "is_directory": False, "exists": True, "size_bytes": 1024, "modified_time": 1720000000, "hash": "abc123", "file_count": 0},
        ]
        client.post("/api/report", json=payload)
        resp = client.get("/api/nodes/file-node-2/files")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["filepath"] == "/etc/passwd"

    def test_file_deleted_alert(self):
        """Deleting a file should trigger FILE_DELETED alert."""
        # First report: file exists
        payload1 = build_payload(hostname="del-file-node", timestamp="2026-07-03T12:00:00Z")
        payload1["file_monitoring"] = [
            {"path": "/etc/important.conf", "is_directory": False, "exists": True, "size_bytes": 512, "modified_time": 1720000000, "hash": "hash1", "file_count": 0},
        ]
        client.post("/api/report", json=payload1)

        # Second report: file no longer exists
        payload2 = build_payload(hostname="del-file-node", timestamp="2026-07-03T12:01:00Z")
        payload2["file_monitoring"] = [
            {"path": "/etc/important.conf", "is_directory": False, "exists": False, "size_bytes": 0, "modified_time": 0, "hash": "", "file_count": 0},
        ]
        client.post("/api/report", json=payload2)

        alerts = client.get("/api/alerts").json()
        file_alerts = [a for a in alerts if a["alert_type"] == "FILE_DELETED"]
        assert len(file_alerts) >= 1
        assert "/etc/important.conf" in file_alerts[0]["message"]

    def test_file_modified_alert(self):
        """Changing file hash should trigger FILE_MODIFIED alert."""
        # First report
        payload1 = build_payload(hostname="mod-file-node", timestamp="2026-07-03T12:00:00Z")
        payload1["file_monitoring"] = [
            {"path": "/etc/ssh/sshd_config", "is_directory": False, "exists": True, "size_bytes": 3000, "modified_time": 1720000000, "hash": "original_hash_value", "file_count": 0},
        ]
        client.post("/api/report", json=payload1)

        # Second report: hash changed
        payload2 = build_payload(hostname="mod-file-node", timestamp="2026-07-03T12:01:00Z")
        payload2["file_monitoring"] = [
            {"path": "/etc/ssh/sshd_config", "is_directory": False, "exists": True, "size_bytes": 3100, "modified_time": 1720000060, "hash": "tampered_hash_value", "file_count": 0},
        ]
        client.post("/api/report", json=payload2)

        alerts = client.get("/api/alerts").json()
        mod_alerts = [a for a in alerts if a["alert_type"] == "FILE_MODIFIED"]
        assert len(mod_alerts) >= 1
        assert "/etc/ssh/sshd_config" in mod_alerts[0]["message"]

    def test_no_alert_on_first_file_report(self):
        """First file report should NOT trigger alerts (no baseline to compare)."""
        payload = build_payload(hostname="first-file-node", timestamp="2026-07-03T12:00:00Z")
        payload["file_monitoring"] = [
            {"path": "/etc/hosts", "is_directory": False, "exists": True, "size_bytes": 500, "modified_time": 1720000000, "hash": "somehash", "file_count": 0},
        ]
        client.post("/api/report", json=payload)

        alerts = client.get("/api/alerts").json()
        file_alerts = [a for a in alerts if a["hostname"] == "first-file-node" and "FILE" in a["alert_type"]]
        assert len(file_alerts) == 0

    def test_report_without_file_monitoring_is_ok(self):
        """Reports without file_monitoring field should still work (backward compat)."""
        payload = build_payload(hostname="no-fm-node")
        # Explicitly ensure no file_monitoring key
        payload.pop("file_monitoring", None)
        resp = client.post("/api/report", json=payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

