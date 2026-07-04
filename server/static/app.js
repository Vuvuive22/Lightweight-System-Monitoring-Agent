let selectedHostname = null;
let nodesData = [];
let alertsData = [];
let fileMonitorData = [];

let cpuRamChart = null;
let networkChart = null;
let diskIoChart = null;

let fetchInterval = null;

// ==================== Initialize Chart.js Instances ====================
function initCharts() {
    // 1. CPU & RAM Chart
    const ctxCpu = document.getElementById('chart-cpu-ram').getContext('2d');
    cpuRamChart = new Chart(ctxCpu, {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                {
                    label: 'CPU (%)',
                    borderColor: '#3b82f6',
                    backgroundColor: 'rgba(59, 130, 246, 0.05)',
                    borderWidth: 2,
                    tension: 0.2,
                    fill: true,
                    data: []
                },
                {
                    label: 'RAM (%)',
                    borderColor: '#a855f7',
                    backgroundColor: 'rgba(168, 85, 247, 0.05)',
                    borderWidth: 2,
                    tension: 0.2,
                    fill: true,
                    data: []
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: { grid: { display: false }, ticks: { color: '#9ca3af', font: { size: 10 } } },
                y: { min: 0, max: 100, grid: { color: 'rgba(255, 255, 255, 0.04)' }, ticks: { color: '#9ca3af' } }
            },
            plugins: { legend: { labels: { color: '#f3f4f6', boxWidth: 10 } } }
        }
    });

    // 2. Network throughput chart
    const ctxNet = document.getElementById('chart-network').getContext('2d');
    networkChart = new Chart(ctxNet, {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                {
                    label: 'Tải xuống (MB/s)',
                    borderColor: '#ec4899',
                    backgroundColor: 'rgba(236, 72, 153, 0.03)',
                    borderWidth: 1.5,
                    tension: 0.2,
                    data: []
                },
                {
                    label: 'Tải lên (MB/s)',
                    borderColor: '#f59e0b',
                    backgroundColor: 'rgba(245, 158, 11, 0.03)',
                    borderWidth: 1.5,
                    tension: 0.2,
                    data: []
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: { grid: { display: false }, ticks: { color: '#9ca3af', font: { size: 10 } } },
                y: { grid: { color: 'rgba(255, 255, 255, 0.04)' }, ticks: { color: '#9ca3af' } }
            },
            plugins: { legend: { labels: { color: '#f3f4f6', boxWidth: 10 } } }
        }
    });

    // 3. Disk IO chart
    const ctxDisk = document.getElementById('chart-disk-io').getContext('2d');
    diskIoChart = new Chart(ctxDisk, {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                {
                    label: 'Đọc (MB/s)',
                    borderColor: '#10b981',
                    backgroundColor: 'rgba(16, 185, 129, 0.03)',
                    borderWidth: 1.5,
                    tension: 0.2,
                    data: []
                },
                {
                    label: 'Ghi (MB/s)',
                    borderColor: '#60a5fa',
                    backgroundColor: 'rgba(96, 165, 250, 0.03)',
                    borderWidth: 1.5,
                    tension: 0.2,
                    data: []
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: { grid: { display: false }, ticks: { color: '#9ca3af', font: { size: 10 } } },
                y: { grid: { color: 'rgba(255, 255, 255, 0.04)' }, ticks: { color: '#9ca3af' } }
            },
            plugins: { legend: { labels: { color: '#f3f4f6', boxWidth: 10 } } }
        }
    });
}

// ==================== Formatting Helpers ====================
function formatBytes(bytes) {
    if (bytes === null || bytes === undefined || isNaN(bytes)) return '0 B';
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

function formatUptime(seconds) {
    if (!seconds) return '-';
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    if (h > 0) return `${h} giờ ${m} phút`;
    if (m > 0) return `${m} phút`;
    return `${s} giây`;
}

// ==================== API Interaction ====================

// 1. Fetch system nodes
async function fetchNodes() {
    try {
        const response = await fetch('/api/nodes');
        if (!response.ok) throw new Error("Nodes fetch failed");
        
        document.getElementById('disconnect-banner').classList.remove('visible');
        nodesData = await response.json();
        updateNodesSidebar();
        updateGlobalStats();
    } catch (err) {
        console.error("Failed to connect to central server:", err);
        document.getElementById('disconnect-banner').classList.add('visible');
    }
}

// 2. Fetch alerts history
async function fetchAlerts() {
    try {
        const response = await fetch('/api/alerts');
        if (!response.ok) return;
        alertsData = await response.json();
        
        // Update dashboard-wide alerts count in header
        document.getElementById('global-alerts').innerText = alertsData.length;
        
        // If a node is selected, update its alert table
        if (selectedHostname) {
            updateAlertsTable();
        }
    } catch (err) {
        console.warn("Failed to fetch alerts:", err);
    }
}

// 3. Fetch specific node metrics
async function fetchSelectedNodeMetrics() {
    if (!selectedHostname) return;
    try {
        const response = await fetch(`/api/nodes/${selectedHostname}/metrics?limit=30`);
        if (!response.ok) return;
        const metrics = await response.json();
        
        updateMetricsDashboard(metrics);
    } catch (err) {
        console.warn("Failed to fetch node metrics:", err);
    }
}

// ==================== UI Rendering & Dom Updates ====================

function updateNodesSidebar() {
    const listContainer = document.getElementById('node-list');
    if (nodesData.length === 0) {
        listContainer.innerHTML = `<div style="text-align: center; color: var(--text-secondary); padding: 20px; font-size: 13px;">Không tìm thấy máy ảo nào đang kết nối.</div>`;
        return;
    }

    listContainer.innerHTML = nodesData.map(node => {
        const isSelected = node.hostname === selectedHostname ? 'active' : '';
        const statusClass = node.online ? 'online' : 'offline';
        const ramGb = (node.ram_total / (1024 ** 3)).toFixed(1);
        
        return `
            <div class="node-item ${isSelected}" onclick="selectNode('${node.hostname}')">
                <div class="node-info-left">
                    <div class="node-name">${node.hostname}</div>
                    <div class="node-meta">${node.os} • ${ramGb} GB RAM</div>
                </div>
                <div class="status-dot ${statusClass}"></div>
            </div>
        `;
    }).join('');
}

function updateGlobalStats() {
    const online = nodesData.filter(n => n.online).length;
    const offline = nodesData.length - online;
    document.getElementById('global-online').innerText = online;
    document.getElementById('global-offline').innerText = offline;
}

function selectNode(hostname) {
    if (selectedHostname === hostname) return;
    selectedHostname = hostname;
    
    // Hide empty state and show details
    document.getElementById('empty-selection-state').style.display = 'none';
    document.getElementById('details-workspace').style.display = 'flex';
    
    // Reset charts
    if (cpuRamChart) {
        cpuRamChart.data.labels = [];
        cpuRamChart.data.datasets[0].data = [];
        cpuRamChart.data.datasets[1].data = [];
        cpuRamChart.update();
    }
    if (networkChart) {
        networkChart.data.labels = [];
        networkChart.data.datasets[0].data = [];
        networkChart.data.datasets[1].data = [];
        networkChart.update();
    }
    if (diskIoChart) {
        diskIoChart.data.labels = [];
        diskIoChart.data.datasets[0].data = [];
        diskIoChart.data.datasets[1].data = [];
        diskIoChart.update();
    }
    
    // Redraw nodes list to update active class
    updateNodesSidebar();
    
    // Immediately fetch details
    fetchSelectedNodeMetrics();
    fetchFileMonitors();
    updateNodeHeader();
}

function updateNodeHeader() {
    const node = nodesData.find(n => n.hostname === selectedHostname);
    if (!node) return;

    document.getElementById('node-name-label').innerText = node.hostname;
    document.getElementById('node-os').innerText = node.os;
    document.getElementById('node-ip').innerText = node.ip_address || '-';
    document.getElementById('node-cores').innerText = node.cpu_cores || '-';
    
    const statusDot = document.getElementById('node-status-dot');
    statusDot.className = 'status-dot ' + (node.online ? 'online' : 'offline');

    // Render services
    const servicesGrid = document.getElementById('services-grid');
    const serviceKeys = Object.keys(node.services || {});
    if (serviceKeys.length === 0) {
        servicesGrid.innerHTML = `<span style="color: var(--text-secondary); font-size: 13px;">Không có dịch vụ nào đang được giám sát trên agent này.</span>`;
    } else {
        servicesGrid.innerHTML = serviceKeys.map(svc => {
            const status = node.services[svc];
            return `
                <div class="service-badge ${status}">
                    <span class="indicator-dot"></span>
                    <span>${svc}</span>
                </div>
            `;
        }).join('');
    }
}

function updateMetricsDashboard(history) {
    if (history.length === 0) return;
    const latest = history[history.length - 1];

    // 1. Load Uptime
    if (latest.timestamp) {
        // Simple uptime display based on node properties
        const node = nodesData.find(n => n.hostname === selectedHostname);
        if (node) {
            // Compute uptime since last report if agent provides it, or approximate
            // For now, let's look for load averages in CPU metrics
        }
    }

    // 2. Gauges
    document.getElementById('val-cpu').innerText = `${latest.cpu_percent.toFixed(1)}%`;
    document.getElementById('bar-cpu').style.width = `${latest.cpu_percent}%`;
    document.getElementById('val-load').innerText = latest.load_1m ? latest.load_1m.toFixed(2) : '-';

    document.getElementById('val-ram').innerText = `${latest.ram_percent.toFixed(1)}%`;
    document.getElementById('bar-ram').style.width = `${latest.ram_percent}%`;
    document.getElementById('val-ram-usage').innerText = `${formatBytes(latest.ram_used)} / ${formatBytes(latest.ram_total)}`;

    document.getElementById('val-disk').innerText = `${latest.disk_percent.toFixed(1)}%`;
    document.getElementById('bar-disk').style.width = `${latest.disk_percent}%`;
    document.getElementById('val-disk-usage').innerText = `${formatBytes(latest.disk_used)} / ${formatBytes(latest.disk_total)}`;

    // 3. Line charts update
    const labels = history.map(h => {
        const timePart = h.timestamp.split('T')[1] || '';
        return timePart.substring(0, 5); // hh:mm
    });

    // CPU/RAM Line
    cpuRamChart.data.labels = labels;
    cpuRamChart.data.datasets[0].data = history.map(h => h.cpu_percent);
    cpuRamChart.data.datasets[1].data = history.map(h => h.ram_percent);
    cpuRamChart.update();

    // Network throughput calculations (Delta Rx / Tx bytes mapped to MB/s)
    networkChart.data.labels = labels;
    networkChart.data.datasets[0].data = history.map((h, index) => {
        if (index === 0) return 0;
        const prev = history[index - 1];
        const bytes = Math.max(0, h.net_rx - prev.net_rx);
        return (bytes / (1024 ** 2)).toFixed(2); // Convert to MB/s
    });
    networkChart.data.datasets[1].data = history.map((h, index) => {
        if (index === 0) return 0;
        const prev = history[index - 1];
        const bytes = Math.max(0, h.net_tx - prev.net_tx);
        return (bytes / (1024 ** 2)).toFixed(2);
    });
    networkChart.update();

    // Disk IO rates (Delta Disk Read / Write bytes mapped to MB/s)
    diskIoChart.data.labels = labels;
    diskIoChart.data.datasets[0].data = history.map((h, index) => {
        if (index === 0) return 0;
        const prev = history[index - 1];
        const bytes = Math.max(0, h.disk_io_read - prev.disk_io_read);
        return (bytes / (1024 ** 2)).toFixed(2);
    });
    diskIoChart.data.datasets[1].data = history.map((h, index) => {
        if (index === 0) return 0;
        const prev = history[index - 1];
        const bytes = Math.max(0, h.disk_io_write - prev.disk_io_write);
        return (bytes / (1024 ** 2)).toFixed(2);
    });
    diskIoChart.update();
}

function updateAlertsTable() {
    const tbody = document.getElementById('alerts-table-body');
    // Filter alerts belonging to the selected node
    const nodeAlerts = alertsData.filter(alert => alert.hostname === selectedHostname);
    
    if (nodeAlerts.length === 0) {
        tbody.innerHTML = `<tr><td colspan="4" style="text-align: center; color: var(--text-secondary); padding: 16px;">Không có cảnh báo nào được ghi nhận cho máy ảo này.</td></tr>`;
        return;
    }

    tbody.innerHTML = nodeAlerts.map(alert => {
        const ts = alert.timestamp.replace('T', ' ').substring(0, 19);
        const severityClass = alert.severity.toLowerCase();
        
        return `
            <tr>
                <td style="color: var(--text-secondary); white-space: nowrap;">${ts}</td>
                <td style="font-family: monospace;">${alert.alert_type}</td>
                <td><span class="badge ${severityClass}">${alert.severity}</span></td>
                <td>${alert.message}</td>
            </tr>
        `;
    }).join('');
}

// ==================== Node Management ====================

async function deleteSelectedNode() {
    if (!selectedHostname) return;
    
    const confirmMsg = `Bạn có chắc chắn muốn xóa node "${selectedHostname}" và toàn bộ dữ liệu lịch sử của nó?`;
    if (!confirm(confirmMsg)) return;

    try {
        const response = await fetch(`/api/nodes/${selectedHostname}`, { method: 'DELETE' });
        if (!response.ok) throw new Error("Delete failed");
        
        // Reset UI
        selectedHostname = null;
        document.getElementById('empty-selection-state').style.display = 'flex';
        document.getElementById('details-workspace').style.display = 'none';
        
        // Refresh node list
        fetchNodes();
        fetchAlerts();
    } catch (err) {
        console.error("Failed to delete node:", err);
        alert("Không thể xóa node. Vui lòng thử lại.");
    }
}

// ==================== File Monitoring ====================

async function fetchFileMonitors() {
    if (!selectedHostname) return;
    try {
        const response = await fetch(`/api/nodes/${selectedHostname}/files`);
        if (!response.ok) return;
        fileMonitorData = await response.json();
        updateFileMonitorsTable();
    } catch (err) {
        console.warn("Failed to fetch file monitors:", err);
    }
}

function updateFileMonitorsTable() {
    const tbody = document.getElementById('file-monitor-body');
    if (!tbody) return;

    if (fileMonitorData.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--text-secondary); padding: 16px;">Chưa có file/thư mục nào được cấu hình giám sát trên agent này.</td></tr>`;
        return;
    }

    tbody.innerHTML = fileMonitorData.map(item => {
        const typeIcon = item.is_directory ? '📁' : '📄';
        const typeLabel = item.is_directory ? 'Thư mục' : 'File';
        const existsClass = item.exists_flag ? 'exists' : 'deleted';
        const existsLabel = item.exists_flag ? '✓ Tồn tại' : '✗ Đã xóa';
        const sizeStr = item.exists_flag ? formatBytes(item.size_bytes) : '-';

        let hashStr = '-';
        if (item.hash && item.hash.length > 0) {
            hashStr = `<span class="hash-text" title="${item.hash}">${item.hash}</span>`;
        }
        if (item.is_directory) {
            hashStr = `${item.file_count} files`;
        }

        let mtimeStr = '-';
        if (item.modified_time && item.modified_time > 0) {
            const d = new Date(item.modified_time * 1000);
            mtimeStr = d.toISOString().replace('T', ' ').substring(0, 19);
        }

        return `
            <tr>
                <td style="font-family: monospace; font-size: 12px;">${typeIcon} ${item.filepath}</td>
                <td>${typeLabel}</td>
                <td><span class="file-status-badge ${existsClass}">${existsLabel}</span></td>
                <td>${sizeStr}</td>
                <td>${hashStr}</td>
                <td style="color: var(--text-secondary); white-space: nowrap;">${mtimeStr}</td>
            </tr>
        `;
    }).join('');
}

// ==================== Application Loop ====================

// Kick off cycles
initCharts();
fetchNodes();
fetchAlerts();

// Set interval loops
fetchInterval = setInterval(() => {
    fetchNodes();
    fetchAlerts();
    if (selectedHostname) {
        fetchSelectedNodeMetrics();
        fetchFileMonitors();
        updateNodeHeader(); // update online/offline and services status
    }
}, 5000);
