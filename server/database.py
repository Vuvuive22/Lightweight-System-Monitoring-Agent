"""Database layer for Central Monitoring System using SQLite."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_FILE = Path(__file__).parent / "sysmon.db"


def get_db_connection() -> sqlite3.Connection:
    """Return a connection to the SQLite database with row factory enabled."""
    conn = sqlite3.connect(str(DB_FILE))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Create database tables if they do not exist."""
    # Ensure directory exists
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # 1. Table for keeping track of monitored nodes
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS nodes (
                hostname TEXT PRIMARY KEY,
                ip_address TEXT,
                os TEXT,
                cpu_cores INTEGER,
                ram_total INTEGER,
                interval_seconds INTEGER,
                last_seen TEXT
            )
        """)
        
        # 2. Table for granular metrics history
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hostname TEXT,
                timestamp TEXT,
                cpu_percent REAL,
                load_1m REAL,
                load_5m REAL,
                load_15m REAL,
                ram_total INTEGER,
                ram_used INTEGER,
                ram_percent REAL,
                disk_total INTEGER,
                disk_used INTEGER,
                disk_percent REAL,
                disk_io_read INTEGER,
                disk_io_write INTEGER,
                net_rx INTEGER,
                net_tx INTEGER,
                FOREIGN KEY(hostname) REFERENCES nodes(hostname) ON DELETE CASCADE
            )
        """)
        
        # 3. Table for service statuses
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS services (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hostname TEXT,
                timestamp TEXT,
                service_name TEXT,
                status TEXT,
                FOREIGN KEY(hostname) REFERENCES nodes(hostname) ON DELETE CASCADE,
                UNIQUE(hostname, service_name) ON CONFLICT REPLACE
            )
        """)
        
        # 4. Table for alerts and anomalies
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hostname TEXT,
                timestamp TEXT,
                alert_type TEXT,
                severity TEXT,
                message TEXT,
                FOREIGN KEY(hostname) REFERENCES nodes(hostname) ON DELETE CASCADE
            )
        """)

        # 5. Table for file/directory monitoring history
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS file_monitors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hostname TEXT,
                timestamp TEXT,
                filepath TEXT,
                is_directory INTEGER DEFAULT 0,
                exists_flag INTEGER DEFAULT 1,
                size_bytes INTEGER DEFAULT 0,
                modified_time INTEGER DEFAULT 0,
                hash TEXT DEFAULT '',
                file_count INTEGER DEFAULT 0,
                FOREIGN KEY(hostname) REFERENCES nodes(hostname) ON DELETE CASCADE
            )
        """)
        
        # Clean up orphaned records from deleted nodes
        cursor.execute("DELETE FROM alerts WHERE hostname NOT IN (SELECT hostname FROM nodes)")
        cursor.execute("DELETE FROM metrics WHERE hostname NOT IN (SELECT hostname FROM nodes)")
        cursor.execute("DELETE FROM services WHERE hostname NOT IN (SELECT hostname FROM nodes)")
        cursor.execute("DELETE FROM file_monitors WHERE hostname NOT IN (SELECT hostname FROM nodes)")
        
        conn.commit()


def register_node(
    hostname: str,
    ip_address: str,
    os_name: str,
    cpu_cores: int,
    ram_total: int,
    interval_seconds: int,
) -> None:
    """Insert or update a node registration details and refresh last_seen."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db_connection() as conn:
        conn.execute("""
            INSERT INTO nodes (hostname, ip_address, os, cpu_cores, ram_total, interval_seconds, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(hostname) DO UPDATE SET
                ip_address=excluded.ip_address,
                os=excluded.os,
                cpu_cores=excluded.cpu_cores,
                ram_total=excluded.ram_total,
                interval_seconds=excluded.interval_seconds,
                last_seen=excluded.last_seen
        """, (hostname, ip_address, os_name, cpu_cores, ram_total, interval_seconds, now))
        conn.commit()


def save_metrics(hostname: str, timestamp: str, data: dict[str, Any]) -> None:
    """Extract metrics from agent payload and save to historical database."""
    cpu = data.get("cpu", {})
    memory = data.get("memory", {})
    disk = data.get("disk", {})
    disk_io = data.get("disk_io", {})
    network = data.get("network", {})

    # Extract primary disk capacity
    disk_total = 0
    disk_used = 0
    disk_percent = 0.0
    if disk:
        # Get first mount point (e.g. "/" or "C:")
        primary_mount = next(iter(disk.values()), {})
        if primary_mount:
            disk_total = primary_mount.get("total_bytes", 0)
            disk_used = primary_mount.get("used_bytes", 0)
            disk_percent = primary_mount.get("used_percent", 0.0)

    # Accumulate Rx/Tx bytes across all network adapters
    net_rx = 0
    net_tx = 0
    if network:
        for adapter_info in network.values():
            if isinstance(adapter_info, dict):
                net_rx += adapter_info.get("rx_bytes", 0)
                net_tx += adapter_info.get("tx_bytes", 0)

    with get_db_connection() as conn:
        conn.execute("""
            INSERT INTO metrics (
                hostname, timestamp, cpu_percent, load_1m, load_5m, load_15m,
                ram_total, ram_used, ram_percent, disk_total, disk_used, disk_percent,
                disk_io_read, disk_io_write, net_rx, net_tx
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            hostname, timestamp,
            cpu.get("cpu_percent", 0.0),
            cpu.get("load_1m", 0.0),
            cpu.get("load_5m", 0.0),
            cpu.get("load_15m", 0.0),
            memory.get("total_bytes", 0),
            memory.get("used_bytes", 0),
            memory.get("used_percent", 0.0),
            disk_total, disk_used, disk_percent,
            disk_io.get("read_bytes", 0),
            disk_io.get("write_bytes", 0),
            net_rx, net_tx
        ))
        conn.commit()


def save_services(hostname: str, timestamp: str, services: dict[str, str]) -> None:
    """Save service statuses for the node."""
    if not services:
        return
    with get_db_connection() as conn:
        for service_name, status in services.items():
            conn.execute("""
                INSERT INTO services (hostname, timestamp, service_name, status)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(hostname, service_name) DO UPDATE SET
                    timestamp=excluded.timestamp,
                    status=excluded.status
            """, (hostname, timestamp, service_name, status))
        conn.commit()


def save_alert(hostname: str, timestamp: str, alert_type: str, severity: str, message: str) -> None:
    """Log an alert or anomaly to the database."""
    with get_db_connection() as conn:
        conn.execute("""
            INSERT INTO alerts (hostname, timestamp, alert_type, severity, message)
            VALUES (?, ?, ?, ?, ?)
        """, (hostname, timestamp, alert_type, severity, message))
        conn.commit()


# -------------------------------------------------------------------------
# File Monitoring Database Operations
# -------------------------------------------------------------------------

def save_file_monitors(hostname: str, timestamp: str, file_items: list[dict[str, Any]]) -> None:
    """Save file/directory monitoring data to the database."""
    if not file_items:
        return
    with get_db_connection() as conn:
        for item in file_items:
            conn.execute("""
                INSERT INTO file_monitors (
                    hostname, timestamp, filepath, is_directory, exists_flag,
                    size_bytes, modified_time, hash, file_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                hostname, timestamp,
                item.get("path", ""),
                1 if item.get("is_directory", False) else 0,
                1 if item.get("exists", True) else 0,
                item.get("size_bytes", 0),
                item.get("modified_time", 0),
                item.get("hash", ""),
                item.get("file_count", 0),
            ))
        conn.commit()


def get_last_file_state(hostname: str, filepath: str) -> dict[str, Any] | None:
    """Get the most recent file monitoring record for a specific path on a node."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM file_monitors
            WHERE hostname=? AND filepath=?
            ORDER BY timestamp DESC, id DESC
            LIMIT 1
        """, (hostname, filepath))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_file_monitor_history(hostname: str, limit: int = 20) -> list[dict[str, Any]]:
    """Get the latest file monitoring records for a node (most recent per filepath)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # Get the latest record for each unique filepath on this node
        cursor.execute("""
            SELECT fm.* FROM file_monitors fm
            INNER JOIN (
                SELECT filepath, MAX(id) as max_id
                FROM file_monitors
                WHERE hostname=?
                GROUP BY filepath
            ) latest ON fm.id = latest.max_id
            ORDER BY fm.filepath ASC
            LIMIT ?
        """, (hostname, limit))
        return [dict(row) for row in cursor.fetchall()]


# -------------------------------------------------------------------------
# Node & Metrics Query Operations
# -------------------------------------------------------------------------

def get_nodes() -> list[dict[str, Any]]:
    """Retrieve all nodes, categorizing them as Online or Offline based on last_seen."""
    nodes_list = []
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM nodes ORDER BY hostname ASC")
        rows = cursor.fetchall()
        
        now = datetime.now(timezone.utc)
        for row in rows:
            node = dict(row)
            # Offline check: if last report older than (2 * interval) seconds
            last_seen_str = node["last_seen"]
            interval = node["interval_seconds"] or 10
            
            online = False
            if last_seen_str:
                try:
                    last_seen_dt = datetime.fromisoformat(last_seen_str.replace("Z", "+00:00"))
                    seconds_diff = (now - last_seen_dt).total_seconds()
                    if seconds_diff <= (interval * 2) + 2:
                        online = True
                except Exception:
                    pass
            
            node["online"] = online
            
            # Retrieve latest services status
            cursor.execute("SELECT service_name, status FROM services WHERE hostname=?", (node["hostname"],))
            node["services"] = {r["service_name"]: r["status"] for r in cursor.fetchall()}
            
            nodes_list.append(node)
            
    return nodes_list


def get_metrics_history(hostname: str, limit: int = 50) -> list[dict[str, Any]]:
    """Get the last N metrics history for a node (ascending order by time)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM (
                SELECT * FROM metrics 
                WHERE hostname=? 
                ORDER BY timestamp DESC 
                LIMIT ?
            ) ORDER BY timestamp ASC
        """, (hostname, limit))
        return [dict(row) for row in cursor.fetchall()]


def get_alerts_history(limit: int = 50) -> list[dict[str, Any]]:
    """Get the list of recent system-wide alerts."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM alerts ORDER BY timestamp DESC, id DESC LIMIT ?", (limit,))
        return [dict(row) for row in cursor.fetchall()]


def delete_node(hostname: str) -> None:
    """Remove a node and all its referenced metrics/alerts."""
    with get_db_connection() as conn:
        conn.execute("DELETE FROM nodes WHERE hostname=?", (hostname,))
        conn.commit()


def delete_offline_alerts(hostname: str) -> None:
    """Remove any NODE_OFFLINE alerts for a node since it is now online."""
    with get_db_connection() as conn:
        conn.execute("DELETE FROM alerts WHERE hostname=? AND alert_type='NODE_OFFLINE'", (hostname,))
        conn.commit()
