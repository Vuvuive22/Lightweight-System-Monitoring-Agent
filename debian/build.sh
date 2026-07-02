#!/usr/bin/env bash
# =========================================================================
#  build.sh — Build a .deb package for sysmon-agent
#
#  Usage:
#    chmod +x debian/build.sh
#    ./debian/build.sh [version]
#
#  Example:
#    ./debian/build.sh 1.0.0
# =========================================================================

set -euo pipefail

VERSION="${1:-1.0.0}"
PKG_NAME="sysmon-agent"
ARCH="all"                         # Pure Python — architecture-independent.
BUILD_ROOT="$(mktemp -d)"
PKG_DIR="${BUILD_ROOT}/${PKG_NAME}_${VERSION}_${ARCH}"

echo "==> Building ${PKG_NAME} ${VERSION} …"

# -------------------------------------------------------------------
# 1. Create FHS-compliant directory layout
# -------------------------------------------------------------------
mkdir -p "${PKG_DIR}/DEBIAN"
mkdir -p "${PKG_DIR}/usr/share/${PKG_NAME}/agent/collectors"
mkdir -p "${PKG_DIR}/usr/share/${PKG_NAME}/agent/utils"
mkdir -p "${PKG_DIR}/etc/${PKG_NAME}"
mkdir -p "${PKG_DIR}/lib/systemd/system"
mkdir -p "${PKG_DIR}/var/log/${PKG_NAME}"

# -------------------------------------------------------------------
# 2. Copy project files into the staging tree
# -------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Source code
cp "${SCRIPT_DIR}/agent/__init__.py"              "${PKG_DIR}/usr/share/${PKG_NAME}/agent/"
cp "${SCRIPT_DIR}/agent/main.py"                  "${PKG_DIR}/usr/share/${PKG_NAME}/agent/"
cp "${SCRIPT_DIR}/agent/collectors/__init__.py"    "${PKG_DIR}/usr/share/${PKG_NAME}/agent/collectors/"
cp "${SCRIPT_DIR}/agent/collectors/metrics.py"     "${PKG_DIR}/usr/share/${PKG_NAME}/agent/collectors/"
cp "${SCRIPT_DIR}/agent/collectors/watcher.py"     "${PKG_DIR}/usr/share/${PKG_NAME}/agent/collectors/"
cp "${SCRIPT_DIR}/agent/utils/__init__.py"         "${PKG_DIR}/usr/share/${PKG_NAME}/agent/utils/"
cp "${SCRIPT_DIR}/agent/utils/config.py"           "${PKG_DIR}/usr/share/${PKG_NAME}/agent/utils/"
cp "${SCRIPT_DIR}/agent/utils/logger.py"           "${PKG_DIR}/usr/share/${PKG_NAME}/agent/utils/"
cp "${SCRIPT_DIR}/agent/utils/dashboard.py"        "${PKG_DIR}/usr/share/${PKG_NAME}/agent/utils/"
cp "${SCRIPT_DIR}/agent/utils/dashboard.html"      "${PKG_DIR}/usr/share/${PKG_NAME}/agent/utils/"

# CLI monitor utility and launcher
mkdir -p "${PKG_DIR}/usr/bin"
cp "${SCRIPT_DIR}/cli_monitor.py"                  "${PKG_DIR}/usr/share/${PKG_NAME}/cli_monitor.py"
chmod +x "${PKG_DIR}/usr/share/${PKG_NAME}/cli_monitor.py"

cat > "${PKG_DIR}/usr/bin/sysmon-monitor" <<'EOF'
#!/usr/bin/env bash
python3 /usr/share/sysmon-agent/cli_monitor.py "$@"
EOF
chmod +x "${PKG_DIR}/usr/bin/sysmon-monitor"

# Configuration (mark as conffile — see below)
cp "${SCRIPT_DIR}/config/config.json"              "${PKG_DIR}/etc/${PKG_NAME}/config.json"

# systemd service
cp "${SCRIPT_DIR}/deployment/sysmon-agent.service" "${PKG_DIR}/lib/systemd/system/${PKG_NAME}.service"

# requirements.txt (for reference)
cp "${SCRIPT_DIR}/requirements.txt"                "${PKG_DIR}/usr/share/${PKG_NAME}/requirements.txt"

# -------------------------------------------------------------------
# 3. DEBIAN/control
# -------------------------------------------------------------------
cat > "${PKG_DIR}/DEBIAN/control" <<EOF
Package: ${PKG_NAME}
Version: ${VERSION}
Section: admin
Priority: optional
Architecture: ${ARCH}
Depends: python3 (>= 3.10), python3-psutil (>= 5.8), python3-watchdog (>= 2.1)
Maintainer: SysAdmin Team <admin@example.com>
Description: Lightweight System Monitoring Agent
 A Python-based daemon that collects system metrics (CPU, RAM, disk,
 network) and monitors filesystem changes in real-time. Logs can be
 routed to local files (with rotation) or to a Syslog server.
EOF

# -------------------------------------------------------------------
# 4. DEBIAN/conffiles — prevent dpkg from overwriting user edits
# -------------------------------------------------------------------
cat > "${PKG_DIR}/DEBIAN/conffiles" <<EOF
/etc/${PKG_NAME}/config.json
EOF

# -------------------------------------------------------------------
# 5. DEBIAN/postinst — run after installation
# -------------------------------------------------------------------
cat > "${PKG_DIR}/DEBIAN/postinst" <<'POSTINST'
#!/bin/bash
set -e

# Reload systemd to pick up the new unit file.
systemctl daemon-reload

# Enable the service so it starts on boot.
systemctl enable sysmon-agent.service

# Start (or restart) the service immediately.
systemctl restart sysmon-agent.service

echo "sysmon-agent installed and started successfully."
POSTINST
chmod 0755 "${PKG_DIR}/DEBIAN/postinst"

# -------------------------------------------------------------------
# 6. DEBIAN/prerm — run before removal
# -------------------------------------------------------------------
cat > "${PKG_DIR}/DEBIAN/prerm" <<'PRERM'
#!/bin/bash
set -e

# Stop the service before removing files.
systemctl stop sysmon-agent.service 2>/dev/null || true
systemctl disable sysmon-agent.service 2>/dev/null || true

echo "sysmon-agent service stopped and disabled."
PRERM
chmod 0755 "${PKG_DIR}/DEBIAN/prerm"

# -------------------------------------------------------------------
# 7. DEBIAN/postrm — clean up after removal
# -------------------------------------------------------------------
cat > "${PKG_DIR}/DEBIAN/postrm" <<'POSTRM'
#!/bin/bash
set -e

if [ "$1" = "purge" ]; then
    rm -rf /var/log/sysmon-agent
    rm -rf /etc/sysmon-agent
fi

systemctl daemon-reload
POSTRM
chmod 0755 "${PKG_DIR}/DEBIAN/postrm"

# -------------------------------------------------------------------
# 8. Build the .deb
# -------------------------------------------------------------------
OUTPUT_DIR="${SCRIPT_DIR}/dist"
mkdir -p "${OUTPUT_DIR}"

dpkg-deb --build --root-owner-group "${PKG_DIR}" "${OUTPUT_DIR}/${PKG_NAME}_${VERSION}_${ARCH}.deb"

echo "==> Package built: ${OUTPUT_DIR}/${PKG_NAME}_${VERSION}_${ARCH}.deb"

# Clean up staging area.
rm -rf "${BUILD_ROOT}"
