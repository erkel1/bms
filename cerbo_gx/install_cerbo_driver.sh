#!/bin/bash
# =============================================================================
# BMS Battery Driver Installer for Victron Cerbo GX
# =============================================================================
# This script installs the BMS battery D-Bus driver on the Cerbo GX.
# Run this from the BMS Raspberry Pi (192.168.15.137).
#
# Prerequisites:
#   - SSH must be enabled on the Cerbo GX (Settings > General > Set Root Password)
#   - The BMS Modbus TCP server must be running on port 502
#
# Usage:
#   ./install_cerbo_driver.sh [cerbo_ip] [cerbo_password]
#
# Example:
#   ./install_cerbo_driver.sh 192.168.15.67 cerbo123
# =============================================================================

set -e

CERBO_IP="${1:-192.168.15.67}"
CERBO_PASS="${2:-cerbo123}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DRIVER_FILE="${SCRIPT_DIR}/dbus-bms-battery.py"

echo "=== BMS Battery Driver Installer for Cerbo GX ==="
echo "Cerbo GX IP: ${CERBO_IP}"
echo "Driver file: ${DRIVER_FILE}"
echo ""

# --check: only deploy if the deployed file differs from repo
if [ "${1}" = "--check" ] || [ "${2}" = "--check" ]; then
    REMOTE_MD5=$(sshpass -p "${CERBO_PASS}" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 root@${CERBO_IP} \
        "md5sum /data/dbus-bms-battery/dbus-bms-battery.py 2>/dev/null | cut -d' ' -f1" 2>/dev/null)
    LOCAL_MD5=$(md5sum "${DRIVER_FILE}" | cut -d' ' -f1)
    if [ "${REMOTE_MD5}" = "${LOCAL_MD5}" ]; then
        echo "Cerbo driver up to date (${LOCAL_MD5})"
        exit 0
    fi
    echo "Driver differs (local=${LOCAL_MD5} remote=${REMOTE_MD5}) — deploying..."
fi

# Check driver file exists
if [ ! -f "${DRIVER_FILE}" ]; then
    echo "ERROR: Driver file not found: ${DRIVER_FILE}"
    exit 1
fi

# Test SSH connectivity
echo "Testing SSH connectivity to Cerbo GX..."
if ! sshpass -p "${CERBO_PASS}" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 root@${CERBO_IP} "echo ok" >/dev/null 2>&1; then
    echo "ERROR: Cannot connect to Cerbo GX at ${CERBO_IP}"
    echo "Make sure SSH is enabled (Settings > General > Set Root Password)"
    exit 1
fi
echo "SSH connection OK"

# Stop existing service if running
echo ""
echo "Stopping existing service (if any)..."
sshpass -p "${CERBO_PASS}" ssh -o StrictHostKeyChecking=no root@${CERBO_IP} "svc -d /service/dbus-bms-battery 2>/dev/null; sleep 1" || true

# Create directories on Cerbo
echo "Creating directories..."
sshpass -p "${CERBO_PASS}" ssh -o StrictHostKeyChecking=no root@${CERBO_IP} "mkdir -p /data/dbus-bms-battery/service/log; mkdir -p /var/log/dbus-bms-battery"

# Copy driver script
echo "Copying driver script..."
sshpass -p "${CERBO_PASS}" scp -o StrictHostKeyChecking=no "${DRIVER_FILE}" root@${CERBO_IP}:/data/dbus-bms-battery/dbus-bms-battery.py

# Make executable
sshpass -p "${CERBO_PASS}" ssh -o StrictHostKeyChecking=no root@${CERBO_IP} "chmod +x /data/dbus-bms-battery/dbus-bms-battery.py"

# Create service run script
echo "Creating service scripts..."
sshpass -p "${CERBO_PASS}" ssh -o StrictHostKeyChecking=no root@${CERBO_IP} "printf '#!/bin/sh\nexec 2>&1\nexport DBUS_SYSTEM_BUS_ADDRESS=unix:path=/var/run/dbus/system_bus_socket\nexec python3 /data/dbus-bms-battery/dbus-bms-battery.py\n' > /data/dbus-bms-battery/service/run && chmod +x /data/dbus-bms-battery/service/run"

# Create log run script
sshpass -p "${CERBO_PASS}" ssh -o StrictHostKeyChecking=no root@${CERBO_IP} "printf '#!/bin/sh\nexec multilog t s25000 n4 /var/log/dbus-bms-battery\n' > /data/dbus-bms-battery/service/log/run && chmod +x /data/dbus-bms-battery/service/log/run"

# Create rc.local for boot persistence
echo "Setting up boot persistence..."
sshpass -p "${CERBO_PASS}" ssh -o StrictHostKeyChecking=no root@${CERBO_IP} "printf '#!/bin/sh\n# Start BMS Battery D-Bus service\nln -sf /data/dbus-bms-battery/service /service/dbus-bms-battery\n' > /data/rc.local && chmod +x /data/rc.local"

# Create service symlink and start
echo "Starting service..."
sshpass -p "${CERBO_PASS}" ssh -o StrictHostKeyChecking=no root@${CERBO_IP} "ln -sf /data/dbus-bms-battery/service /service/dbus-bms-battery"

# Wait for service to start
sleep 5

# Verify
echo ""
echo "=== Verification ==="
sshpass -p "${CERBO_PASS}" ssh -o StrictHostKeyChecking=no root@${CERBO_IP} "svstat /service/dbus-bms-battery"
echo ""

# Check D-Bus service
DBUS_CHECK=$(sshpass -p "${CERBO_PASS}" ssh -o StrictHostKeyChecking=no root@${CERBO_IP} "dbus -y 2>/dev/null | grep modbus_tcp_bms" 2>/dev/null)
if [ -n "${DBUS_CHECK}" ]; then
    echo "D-Bus service: ${DBUS_CHECK}"
    # Read key values
    echo ""
    sshpass -p "${CERBO_PASS}" ssh -o StrictHostKeyChecking=no root@${CERBO_IP} "
        echo 'Voltage:'; dbus -y com.victronenergy.battery.modbus_tcp_bms /Dc/0/Voltage GetValue
        echo 'Temperature:'; dbus -y com.victronenergy.battery.modbus_tcp_bms /Dc/0/Temperature GetValue
        echo 'Max Charge Voltage:'; dbus -y com.victronenergy.battery.modbus_tcp_bms /Info/MaxChargeVoltage GetValue
        echo 'Max Charge Current:'; dbus -y com.victronenergy.battery.modbus_tcp_bms /Info/MaxChargeCurrent GetValue
        echo 'Max Discharge Current:'; dbus -y com.victronenergy.battery.modbus_tcp_bms /Info/MaxDischargeCurrent GetValue
        echo 'Connected:'; dbus -y com.victronenergy.battery.modbus_tcp_bms /Connected GetValue
    "
    echo ""
    echo "=== Installation SUCCESSFUL ==="
else
    echo "WARNING: D-Bus service not found. Check logs:"
    echo "  ssh root@${CERBO_IP} 'cat /var/log/dbus-bms-battery/current'"
    echo "=== Installation may have issues ==="
fi

# Apply QML modification for cell voltage display
echo ""
echo "Applying GUI modification for cell voltage display..."
SCRIPT_DIR_QML="$(cd "$(dirname "$0")" && pwd)"
if [ -f "${SCRIPT_DIR_QML}/PageBattery.qml.modified" ]; then
    sshpass -p "${CERBO_PASS}" scp -o StrictHostKeyChecking=no "${SCRIPT_DIR_QML}/PageBattery.qml.modified" root@${CERBO_IP}:/data/dbus-bms-battery/PageBattery.qml.modified
    sshpass -p "${CERBO_PASS}" ssh -o StrictHostKeyChecking=no root@${CERBO_IP} "cp /data/dbus-bms-battery/PageBattery.qml.modified /opt/victronenergy/gui/qml/PageBattery.qml; svc -t /service/start-gui 2>/dev/null"
    echo "GUI modified - Battery row now shows individual cell voltages"
else
    echo "WARNING: PageBattery.qml.modified not found - skipping GUI modification"
fi

echo ""
echo "Post-install steps:"
echo "  1. Set the SmartShunt as active battery monitor on the Cerbo GX:"
echo "     Settings > System Setup > Battery Monitor > SmartShunt 500A/50mV"
echo "  2. The BMS will still provide DVCC limits (charge voltage/current)"
echo ""
echo "To check status:"
echo "  ssh root@${CERBO_IP} 'svstat /service/dbus-bms-battery'"
echo "  ssh root@${CERBO_IP} 'dbus -y com.victronenergy.battery.modbus_tcp_bms / GetValue'"
echo ""
echo "To uninstall:"
echo "  ssh root@${CERBO_IP} 'svc -d /service/dbus-bms-battery; rm /service/dbus-bms-battery; rm -rf /data/dbus-bms-battery; rm /data/rc.local'"
