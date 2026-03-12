# Victron Cerbo GX BMS Integration

## Overview

This directory contains the BMS battery driver for the Victron Cerbo GX.
The driver runs on the Cerbo GX itself and reads battery data from the BMS
Modbus TCP server on the Raspberry Pi, publishing it as a
`com.victronenergy.battery` service on Venus OS D-Bus.

## Why a D-Bus Driver (not direct Modbus TCP)?

The Cerbo GX's built-in Modbus TCP scanner (`dbus-modbus-client`) only supports
energy meters, gensets, and EV chargers — it has **no battery handler**. So even
though the BMS serves data on port 502, the Cerbo would never recognize it as a
battery. This driver bridges that gap by acting as a Modbus TCP client on the
Cerbo side and publishing the data on D-Bus.

## What Data is Published

| D-Bus Path | BMS Register | Description |
|---|---|---|
| /Dc/0/Voltage | 259 (centivolts) | Total battery voltage |
| /Dc/0/Temperature | 262 (decicelsius) | Average temperature |
| /State | 1282 | 9=Running, 10=Error, 14=Standby |
| /System/NrOfBatteries | 1286 | Number of series banks |
| /System/BatteriesParallel | 1287 | Parallel battery count |
| /System/MinCellVoltage | 1290 | Lowest bank voltage |
| /System/MaxCellVoltage | 1291 | Highest bank voltage |
| /System/MinVoltageCellId | 1306–1308 | Which bank has min voltage |
| /System/MaxVoltageCellId | 1306–1308 | Which bank has max voltage |
| /System/MinCellTemperature | 318 | Minimum cell temperature |
| /System/MaxCellTemperature | 319 | Maximum cell temperature |
| /Temperatures/Cell1–3 | 320–322 | Per-bank median temperatures |
| /Voltages/Cell1–3 | 1306–1308 | Individual bank voltages |
| /Voltages/Sum | Calculated | Sum of bank voltages |
| /Voltages/Diff | Calculated | Spread between min/max bank |
| /Alarms/LowVoltage | 268 | Low voltage alarm |
| /Alarms/HighVoltage | 269 | High voltage alarm |
| /Alarms/LowTemperature | 273 | Low temp alarm |
| /Alarms/HighTemperature | 274 | High temp alarm |
| /Alarms/CellImbalance | Calculated | Alarm if spread >0.3V |
| /Info/MaxChargeVoltage | 305 (decivolts) | DVCC max charge voltage |
| /Info/BatteryLowVoltage | 306 (decivolts) | DVCC min discharge voltage |
| /Info/MaxChargeCurrent | 307 (deciamps, temp-adjusted) | DVCC charge current limit |
| /Info/MaxDischargeCurrent | 308 (deciamps) | DVCC discharge current limit |
| /Io/AllowToCharge | BMS state | 0 when BMS in error state |
| /Io/AllowToDischarge | BMS state | 0 when BMS in error state |

## What is NOT Published (SmartShunt handles)

- **SOC** — requires coulomb counting
- **Current** — BMS has no current sensor
- **Power** — derived from current
- **Consumed Amphours / Capacity**

## Files

| File | Description |
|---|---|
| dbus-bms-battery.py | Main driver script (runs on Cerbo GX) |
| install_cerbo_driver.sh | Install/reinstall script (run from BMS Pi) |
| PageBattery.qml.modified | Patched Cerbo GX GUI for cell voltage display |
| PageBattery.qml.original | Original GUI file (backup) |
| README.md | This file |

---

## Installation

### Prerequisites

1. SSH enabled on Cerbo GX: **Settings → General → Set Root Password**
2. `sshpass` installed on the BMS Pi: `apt install sshpass`
3. BMS running on Raspberry Pi with Modbus TCP server on port 502
   (check `[ModbusServer] enabled = true` in `battery_monitor.ini`)

### Install from BMS Pi

```bash
cd /projects/battery_balancer/cerbo_gx
./install_cerbo_driver.sh 192.168.15.67 <cerbo_root_password>
```

The script will:
1. Stop any existing service on the Cerbo
2. Copy `dbus-bms-battery.py` to `/data/dbus-bms-battery/` on the Cerbo
3. Create the runit service under `/data/dbus-bms-battery/service/`
4. Create `/data/rc.local` to recreate the service symlink on every boot
5. Symlink the service into `/service/` and start it
6. Apply the `PageBattery.qml` GUI patch for cell voltage display
7. Print a verification summary with live D-Bus values

### Manual Install (if the script isn't available)

SSH into the Cerbo GX as root, then:

```bash
mkdir -p /data/dbus-bms-battery/service/log

# Copy the driver file from the Pi
scp root@192.168.15.137:/projects/battery_balancer/cerbo_gx/dbus-bms-battery.py \
    /data/dbus-bms-battery/

# Create service run script
printf '#!/bin/sh\nexec 2>&1\nexec python3 /data/dbus-bms-battery/dbus-bms-battery.py\n' \
    > /data/dbus-bms-battery/service/run
chmod +x /data/dbus-bms-battery/service/run

# Boot persistence (recreates /service/ symlink after firmware updates)
printf '#!/bin/sh\nln -sf /data/dbus-bms-battery/service /service/dbus-bms-battery\n' \
    > /data/rc.local
chmod +x /data/rc.local

# Start service
ln -sf /data/dbus-bms-battery/service /service/dbus-bms-battery
```

---

## Updating the Driver

After changes to `dbus-bms-battery.py` on the Pi, redeploy by re-running the installer:

```bash
cd /projects/battery_balancer/cerbo_gx
./install_cerbo_driver.sh 192.168.15.67 <cerbo_root_password>
```

Or push the file directly and restart:

```bash
scp /projects/battery_balancer/cerbo_gx/dbus-bms-battery.py \
    root@192.168.15.67:/data/dbus-bms-battery/
ssh root@192.168.15.67 'svc -t /service/dbus-bms-battery'
```

---

## Post-Install Configuration

### Set SmartShunt as Active Battery Monitor

The BMS driver provides voltage, temperature, and DVCC limits.
SOC tracking is handled by a SmartShunt:

**Cerbo GUI**: Settings → System Setup → Battery Monitor → SmartShunt 500A/50mV

---

## Checking Status

```bash
# Service status
ssh root@192.168.15.67 'svstat /service/dbus-bms-battery'

# Quick voltage check
ssh root@192.168.15.67 \
  'dbus-send --system --print-reply \
   --dest=com.victronenergy.battery.modbus_tcp_bms \
   /Dc/0/Voltage com.victronenergy.BusItem.GetValue'

# View logs
ssh root@192.168.15.67 'cat /var/log/dbus-bms-battery/current'
```

---

## Troubleshooting

### Voltage not updating / stale readings

**Cause:** If the BMS Pi reboots or the Modbus server restarts, the driver's TCP
connection breaks. Before v1.2, a pymodbus error response left `_connected = True`
while reads silently failed — the driver would never reconnect without a manual
service restart.

**Fix (v1.2):** `_read_register` now sets `_connected = False` on **any** failure
(exception or `isError()`), forcing a reconnect on the next 2-second poll cycle.
A service restart is no longer required after a BMS reboot.

If stale readings still occur, restart the service:
```bash
ssh root@192.168.15.67 'svc -t /service/dbus-bms-battery'
```

### D-Bus service not appearing

```bash
ssh root@192.168.15.67 'svstat /service/dbus-bms-battery'
ssh root@192.168.15.67 'cat /var/log/dbus-bms-battery/current'
```

### Service not starting after firmware update

Venus OS updates may remove the `/service/` symlink. The `/data/rc.local` script
recreates it on boot automatically. If it doesn't:
```bash
ssh root@192.168.15.67 'ln -sf /data/dbus-bms-battery/service /service/dbus-bms-battery'
```
Or re-run the install script.

### Test Modbus connectivity from Cerbo

```bash
ssh root@192.168.15.67 python3 - << 'EOF'
from pymodbus.client.sync import ModbusTcpClient
c = ModbusTcpClient('192.168.15.137', port=502, timeout=3)
print('Connected:', c.connect())
r = c.read_holding_registers(259, count=1, unit=1)
print('Voltage:', r.registers[0]/100.0 if not r.isError() else r)
EOF
```

---

## Uninstall

```bash
ssh root@192.168.15.67 \
  'svc -d /service/dbus-bms-battery; \
   rm /service/dbus-bms-battery; \
   rm -rf /data/dbus-bms-battery; \
   rm -f /data/rc.local'
```

---

## Configuration

Key parameters at the top of `dbus-bms-battery.py`:

| Parameter | Default | Description |
|---|---|---|
| BMS_HOST | 192.168.15.137 | BMS Raspberry Pi IP |
| BMS_PORT | 502 | Modbus TCP port |
| BMS_UNIT_ID | 1 | Modbus unit ID (single=True so any ID works) |
| POLL_INTERVAL_MS | 2000 | Poll every 2 seconds |
| NUM_SERIES_BANKS | 3 | Number of series battery banks |
| DVCC_FALLBACK_MAX_CHARGE_VOLTAGE | 61.0 | Fallback if register 305 unreadable |
| DVCC_FALLBACK_MIN_DISCHARGE_VOLTAGE | 49.5 | Fallback if register 306 unreadable |
| DVCC_FALLBACK_MAX_CHARGE_CURRENT | 200.0 | Fallback if register 307 unreadable |
| DVCC_FALLBACK_MAX_DISCHARGE_CURRENT | 200.0 | Fallback if register 308 unreadable |

DVCC limits are normally read live from the BMS (registers 305–308), set in
`battery_monitor.ini` under `[DVCC]`. Fallback values only apply if those
registers can't be read.

---

## Venus OS Firmware Updates

The driver lives in `/data/` which persists across Venus OS updates.
`/data/rc.local` recreates the `/service/` symlink on every boot.
If a firmware update breaks something, re-run the install script from the Pi.

---

## Architecture

```
BMS Pi (192.168.15.137)              Cerbo GX (192.168.15.67)
┌──────────────────────┐            ┌───────────────────────────────┐
│  bms.py              │            │  dbus-bms-battery.py (v1.2)   │
│  ├─ Reads temps      │  Modbus    │  ├─ ModbusTcpClient            │
│  ├─ Reads voltages   │◄───TCP────►│  ├─ Reads regs 259–1308       │
│  ├─ Manages balance  │  :502      │  ├─ Auto-reconnects on failure │
│  └─ Modbus TCP srv   │            │  └─ Publishes on D-Bus         │
└──────────────────────┘            │           │                    │
                                    │     D-Bus │                    │
                                    │           ▼                    │
                                    │  com.victronenergy.            │
                                    │    battery.modbus_tcp_bms      │
                                    │           │                    │
                                    │  Venus OS System Calc          │
                                    │  ├─ DVCC limits from BMS       │
                                    │  └─ SOC/Current from SmartShunt│
                                    └───────────────────────────────┘
```

---

## Changelog

| Version | Date | Changes |
|---|---|---|
| v1.2 | 2026-03-13 | Fix reconnect bug: `_read_register` now sets `_connected=False` on `isError()` responses, not just exceptions. Prevents permanent stale reads after BMS reboot without requiring a driver restart. |
| v1.1 | 2026-03-12 | Add per-bank temperature registers (318–328), DVCC limits read live from BMS, individual bank voltages on Cerbo GUI |
| v1.0 | Initial | Basic voltage, temperature, alarms, DVCC from config |
