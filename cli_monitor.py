#!/usr/bin/env python3
"""cli_monitor.py — Real-time Terminal Dashboard for System Monitoring Agent.

Displays CPU, RAM, Disk, and Network metrics in a beautifully formatted text UI.
Supports both Windows and Linux terminals out-of-the-box.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any

# Ensure we can import the agent package even if not installed in site-packages
sys.path.insert(0, str(Path(__file__).parent.resolve()))

import psutil
from agent.collectors.metrics import SystemMetricsCollector

# Initialize ANSI formatting. Call empty command on Windows to enable ANSI processing.
if os.name == 'nt':
    os.system('')
    # Force UTF-8 encoding on standard output/error to prevent UnicodeEncodeError on Windows
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding='utf-8')
        except Exception:
            pass

# ANSI styling codes
RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RED = "\033[31m"
BLUE = "\033[34m"
BG_BLUE = "\033[44m"


def format_bytes(n_bytes: int) -> str:
    """Format bytes to human-readable string (e.g. 1.23 GB)."""
    val = float(n_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if val < 1024.0:
            return f"{val:.2f} {unit}"
        val /= 1024.0
    return f"{val:.2f} PB"


def format_rate(rate_bytes_per_sec: float) -> str:
    """Format network throughput rate (e.g. 245.50 KB/s)."""
    val = float(rate_bytes_per_sec)
    for unit in ["B/s", "KB/s", "MB/s", "GB/s"]:
        if val < 1024.0:
            return f"{val:.2f} {unit}"
        val /= 1024.0
    return f"{val:.2f} PB/s"


def make_progress_bar(percent: float, width: int = 30) -> str:
    """Generate a clean ASCII progress bar with color thresholds."""
    filled_width = int(round(width * percent / 100))
    bar = "█" * filled_width + "░" * (width - filled_width)
    
    # Apply color depending on load level
    if percent >= 90.0:
        color = RED
    elif percent >= 75.0:
        color = YELLOW
    else:
        color = GREEN
        
    return f"{color}[{bar}]{RESET} {percent:5.1f}%"


def get_default_mounts() -> list[str]:
    """Auto-detect fixed drives/mounts for the host OS."""
    mounts = []
    if platform.system() == "Windows":
        try:
            for part in psutil.disk_partitions(all=False):
                # Monitor only fixed local drives to avoid blocking on CD-ROMs/network mounts
                if 'fixed' in part.opts or not part.opts:
                    mounts.append(part.mountpoint)
        except Exception:
            pass
        if not mounts:
            mounts = ["C:\\"]
    else:
        mounts = ["/"]
    return mounts


def render_dashboard(
    collector: SystemMetricsCollector, mounts: list[str], interval: float = 1.0
) -> None:
    """Fetch and print the system metrics in a structured grid layout."""
    # Retrieve metrics
    snapshot = collector.collect_all()
    
    # Prepare metadata
    timestamp = snapshot.get("timestamp", "")
    # Trim decimal places and TZ info for readability
    if timestamp:
        timestamp = timestamp.split(".")[0].replace("T", " ")
        
    os_name = f"{platform.system()} {platform.release()}"
    
    # 1. Print Header
    print(f"{BG_BLUE}{BOLD}  SYSTEM MONITORING AGENT — LIVE DASHBOARD  {RESET}")
    print(f"{BOLD}OS:{RESET} {os_name:<30} | {BOLD}Time:{RESET} {timestamp} UTC")
    print("─" * 52)
    
    # 2. CPU Metrics
    cpu = snapshot.get("cpu")
    if cpu:
        percent = cpu.get("cpu_percent", 0.0)
        p_cores = cpu.get("cpu_count_physical", 0)
        l_cores = cpu.get("cpu_count_logical", 0)
        print(f"{BOLD}CPU Load:{RESET}  {make_progress_bar(percent)}")
        print(f"          Cores: {p_cores} Physical / {l_cores} Logical")
    else:
        print(f"{BOLD}CPU Load:{RESET}  {RED}[Failed to collect]{RESET}")
    print("─" * 52)
        
    # 3. RAM/Memory Metrics
    mem = snapshot.get("memory")
    if mem:
        percent = mem.get("used_percent", 0.0)
        used = mem.get("used_bytes", 0)
        total = mem.get("total_bytes", 0)
        free = mem.get("available_bytes", 0)
        print(f"{BOLD}RAM Usage:{RESET} {make_progress_bar(percent)}")
        print(f"          Details: {format_bytes(used)} / {format_bytes(total)} used ({format_bytes(free)} free)")
    else:
        print(f"{BOLD}RAM Usage:{RESET} {RED}[Failed to collect]{RESET}")
    print("─" * 52)
    
    # 4. Disk Metrics
    disk_data = snapshot.get("disk")
    if disk_data:
        print(f"{BOLD}Disk Usage:{RESET}")
        for mount in mounts:
            info = disk_data.get(mount)
            if info:
                percent = info.get("used_percent", 0.0)
                used = info.get("used_bytes", 0)
                total = info.get("total_bytes", 0)
                free = info.get("free_bytes", 0)
                print(f"  {CYAN}{mount:<8}{RESET} {make_progress_bar(percent, 20)}")
                print(f"           Details: {format_bytes(used)} / {format_bytes(total)} used ({format_bytes(free)} free)")
            else:
                print(f"  {CYAN}{mount:<8}{RESET} {RED}[N/A]{RESET}")
    else:
        print(f"{BOLD}Disk Usage:{RESET} {RED}[Failed to collect]{RESET}")
    print("─" * 52)
    
    # 5. Network Metrics
    net = snapshot.get("network")
    if net:
        delta = net.get("delta", {})
        cumulative = net.get("cumulative", {})
        
        down_rate = delta.get("bytes_recv_per_sec", 0.0)
        up_rate = delta.get("bytes_sent_per_sec", 0.0)
        
        down_total = cumulative.get("bytes_recv", 0)
        up_total = cumulative.get("bytes_sent", 0)
        
        print(f"{BOLD}Network Activity:{RESET}")
        print(f"  {GREEN}Download:{RESET} {format_rate(down_rate):<12} (Total Received: {format_bytes(down_total)})")
        print(f"  {BLUE}Upload:{RESET}   {format_rate(up_rate):<12} (Total Sent:     {format_bytes(up_total)})")
    else:
        print(f"{BOLD}Network Activity:{RESET} {RED}[Failed to collect]{RESET}")
    
    print("─" * 52)
    print(f"{YELLOW}Press Ctrl+C to exit. Refreshing every {interval}s.{RESET}")


def main() -> None:
    """Main execution loop for CLI dashboard."""
    parser = argparse.ArgumentParser(
        description="cli_monitor.py — Real-time Terminal Dashboard for System Monitoring Agent."
    )
    parser.add_argument(
        "-i", "--interval",
        type=float,
        default=1.0,
        help="Refresh interval in seconds (default: 1.0)"
    )
    parser.add_argument(
        "--no-clear",
        action="store_true",
        help="Do not clear the screen before printing (scroll/log mode)"
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Print raw JSON metrics snapshots directly to stdout"
    )
    args = parser.parse_args()

    mounts = get_default_mounts()
    collector = SystemMetricsCollector(disk_mount_points=mounts)
    
    try:
        if args.raw:
            import json
            while True:
                snapshot = collector.collect_all()
                print(json.dumps(snapshot))
                time.sleep(args.interval)

        while True:
            # Buffer the dashboard rendering to prevent visual flickering
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                render_dashboard(collector, mounts, args.interval)
            
            if not args.no_clear:
                # Clear terminal and move cursor to home position, then write all output in one system call
                sys.stdout.write("\033[H\033[J" + buffer.getvalue())
            else:
                sys.stdout.write(buffer.getvalue())
            
            sys.stdout.flush()
            time.sleep(args.interval)
            
    except KeyboardInterrupt:
        print("\n\nExiting CLI Monitor. Goodbye!")
        sys.exit(0)


if __name__ == "__main__":
    main()
