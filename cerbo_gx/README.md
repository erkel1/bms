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

| D-Bus Path | Source | Description |
|---|---|---|
| /Dc/0/Voltage | BMS reg 259 | Total battery voltage |
| /Dc/0/Temperature | BMS reg 262 | Average temperature |
| /System/MinCellVoltage | BMS reg 1290 | Lowest bank voltage |
| /System/MaxCellVoltage | BMS reg 1291 | Highest bank voltage |
| /System/MinVoltageCellId | BMS reg 1306-1308 | Which bank has min |
| /System/MaxVoltageCellId | BMS reg 1306-1308 | Which bank has max |
| /Alarms/LowVoltage | BMS reg 268 | Low voltage alarm |
| /Alarms/HighVoltage | BMS reg 269 | High voltage alarm |
| /Alarms/LowTemperature | BMS reg 273 | Low temp alarm |
| /Alarms/HighTemperature | BMS reg 274 | High temp alarm |
| /Alarms/CellImbalance | Calculated | >0.3V spread |
| /Info/MaxChargeVoltage | Config: 61V | DVCC charge limit |
| /Info/MaxChargeCurrent | Config: 200A | DVCC charge current (temp-adjusted) |
| /Info/MaxDischargeCurrent | Config: 200A | DVCC discharge limit |
| /Io/AllowToCharge | BMS state | 0 when BMS in error |
| /Io/AllowToDischarge | BMS state | 0 when BMS in error |

## What is NOT Published (SmartShunt handles)

- SOC (State of Charge) - requires coulomb counting
- Current - BMS has no current sensor
- Power - derived from current
- Consumed Amphours / Capacity

## Files

| File | Description |
|---|---|
| dbus-bms-battery.py | Main driver script (runs on Cerbo GX) |
| install_cerbo_driver.sh | Install/reinstall script (run from BMS Pi) |
| README.md | This file |

## Installation

### Prerequisites
1. SSH enabled on Cerbo GX (Settings > General > Set Root Password)
2. BMS running on Raspberry Pi with Modbus TCP server on port 502

### Install from BMS Pi
```bash
cd /projects/battery_balancer/cerbo_gx
./install_cerbo_driver.sh 192.168.15.67 cerbo123
```

### Post-Install
Set the SmartShunt as the active battery monitor on the Cerbo GX:
- **Cerbo GUI**: Settings > System Setup > Battery Monitor > SmartShunt 500A/50mV
- **Or via D-Bus**: `dbus -y com.victronenergy.settings /Settings/SystemSetup/BatteryService SetValue 'com.victronenergy.battery/288'`

## Checking Status

```bash
# Service status
ssh root@192.168.15.67 'svstat /service/dbus-bms-battery'

# All D-Bus values
ssh root@192.168.15.67 'dbus -y com.victronenergy.battery.modbus_tcp_bms / GetValue'

# Quick voltage check
ssh root@192.168.15.67 'dbus -y com.victronenergy.battery.modbus_tcp_bms /Dc/0/Voltage GetValue'

# View logs
ssh root@192.168.15.67 'cat /var/log/dbus-bms-battery/current'
```

## Uninstall

```bash
ssh root@192.168.15.67 'svc -d /service/dbus-bms-battery; rm /service/dbus-bms-battery; rm -rf /data/dbus-bms-battery; rm /data/rc.local'
```

## Configuration

Key parameters in `dbus-bms-battery.py` (lines 60-70):

| Parameter | Value | Description |
|---|---|---|
| BMS_HOST | 192.168.15.137 | BMS Raspberry Pi IP |
| BMS_PORT | 502 | Modbus TCP port |
| POLL_INTERVAL_MS | 2000 | Poll every 2 seconds |
| MAX_CHARGE_VOLTAGE | 61.0 | Max charge voltage (V) |
| MAX_CHARGE_CURRENT | 200.0 | Max charge current (A) |
| MAX_DISCHARGE_CURRENT | 200.0 | Max discharge current (A) |
| MIN_DISCHARGE_VOLTAGE | 49.5 | Min discharge voltage (V) |

## Venus OS Updates

The driver is stored in `/data/` on the Cerbo which persists across Venus OS
firmware updates. The `/data/rc.local` script recreates the service symlink on
boot. If a firmware update breaks the driver, re-run the install script from
the BMS Pi.

## Architecture

```
BMS Pi (192.168.15.137)          Cerbo GX (192.168.15.67)
┌─────────────────────┐         ┌──────────────────────────┐
│   bms.py            │         │  dbus-bms-battery.py     │
│   ├─ Reads temps    │  TCP    │  ├─ Modbus TCP client    │
│   ├─ Reads voltages │◄────────│  ├─ Reads BMS data       │
│   ├─ Manages balance│  :502   │  └─ Publishes on D-Bus   │
│   └─ Modbus TCP srv │         │         │                │
└─────────────────────┘         │    D-Bus│                │
                                │         ▼                │
                                │  com.victronenergy.      │
                                │    battery.modbus_tcp_bms│
                                │         │                │
                                │  Venus OS System Calc    │
                                │  ├─ DVCC limits from BMS │
                                │  └─ SOC/I from SmartShunt│
                                └──────────────────────────┘
```
