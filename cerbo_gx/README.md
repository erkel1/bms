# Victron Cerbo GX Integration

## Overview

The Cerbo GX is integrated via **direct Modbus TCP writes from the Pi** — no custom driver runs on the Cerbo. The Pi pushes DVCC charge limits directly to the Cerbo's built-in Modbus TCP server every 30 seconds.

Additionally, the Pi's bms.py Modbus TCP server emulates a Carlo Gavazzi EM24 energy meter so the Cerbo can display individual bank voltages. A Node-RED flow on the Cerbo publishes a virtual battery service with BMS data on Venus OS D-Bus.

## Architecture

```
BMS Pi (192.168.15.137)                     Cerbo GX (192.168.15.178)
┌────────────────────────────┐             ┌──────────────────────────────────────┐
│  bms.py                    │  Modbus TCP │                                      │
│  ├─ Reads temps/voltages   │  writes ──► │  Settings/SystemSetup/               │
│  ├─ Manages balancing      │  reg 2710   │    MaxChargeVoltage  ──► DVCC ──► MPPT│
│  ├─ write_cerbo_dvcc_direct│  reg 2705   │    MaxChargeCurrent  ──► DVCC        │
│  │   (every 30s)           │             │                                      │
│  └─ Modbus TCP server      │  Modbus TCP │  dbus-modbus-client polls Pi:        │
│      port 502, unit 1      │  polls ◄─── │    EM24 regs 0–5 (bank voltages)     │
│      (EM24 emulation)      │             │    shown as "Bank Voltages" (grid)    │
│                            │             │                                      │
│  /api/status HTTP          │  HTTP poll  │  Node-RED (Venus OS Large):          │
│    (port 8080)             │  ◄─────────  │    polls /api/status every 5s        │
└────────────────────────────┘             │    virtual battery D-Bus service      │
                                           │    com.victronenergy.battery          │
                                           │      .virtual_vv_bat (instance 100)  │
                                           │                                      │
                                           │  SmartShunt (ttyUSB0) = active       │
                                           │    battery monitor (SOC/current)     │
                                           └──────────────────────────────────────┘
```

## DVCC Integration (Direct Modbus TCP Writes)

The Pi calls `write_cerbo_dvcc_direct()` every 30 seconds (or immediately on significant change) to write charge limits directly to the Cerbo settings:

| Register | Unit | D-Bus path | Scale | Description |
|----------|------|------------|-------|-------------|
| 2710 | 100 | `Settings/SystemSetup/MaxChargeVoltage` | ×10 | e.g. 60.3V → 603 |
| 2705 | 100 | `Settings/SystemSetup/MaxChargeCurrent` | ×1A | e.g. 200A → 200 |

On a high-voltage alarm, CVL is dropped to 49.5V to immediately stop charging.

**Rate limiting:** Writes are throttled to 30s minimum interval. Faster polling (e.g. 5s) caused Venus OS crashes. Do not reduce below 30s.

### Prerequisites on Cerbo

- **Modbus TCP enabled with write access**: Settings → Services → Modbus TCP → Enable, set to read/write

## Bank Voltage Display (Carlo Gavazzi EM24 Emulation)

`bms.py`'s Modbus TCP server (port 502, unit 1) identifies itself as a Carlo Gavazzi EM24 3-phase energy meter. The Cerbo's `dbus-modbus-client` polls it and shows bank voltages as L1/L2/L3 AC voltages on the dashboard.

The device appears as `com.victronenergy.grid.cg_` on D-Bus, renamed to **Bank Voltages** via:
```bash
dbus -y com.victronenergy.settings /Settings/Devices/cg_/CustomName SetValue Bank Voltages
```

**EM24 register map (Pi's Modbus server, port 502 unit 1):**

| Registers | Type | Value | Description |
|-----------|------|-------|-------------|
| 0–1 | s32l ×10 | e.g. 197 = 19.7V | Bank 1 voltage |
| 2–3 | s32l ×10 | | Bank 2 voltage |
| 4–5 | s32l ×10 | | Bank 3 voltage |
| 11 | uint16 | 1648 | Carlo Gavazzi model ID — do NOT overwrite |
| 4098 | uint16 | 4 | 3-phase configuration |
| 40960 | uint16 | 7 | Application H — do NOT overwrite |

**To connect the Cerbo to the Pi's EM24 server**, set the Modbus client device spec via MQTT:
```
Topic: W/48e7da8a03c5/settings/0/Settings/ModbusClient/tcp/Devices
Value: {value:tcp:192.168.15.137:502:1}
```
Or set it at: Settings → Services → Modbus client.

## Node-RED Virtual Battery (Venus OS Large)

A Node-RED flow on the Cerbo polls the Pi's `/api/status` endpoint every 5 seconds and publishes data to a virtual D-Bus battery service.

**Service:** `com.victronenergy.battery.virtual_vv_bat` (device instance 100)

**Published paths:**

| D-Bus path | Source field | Notes |
|------------|--------------|-------|
| `Dc/0/Voltage` | `total_voltage` | Pack voltage |
| `Dc/0/Temperature` | `temperatures` | Max of all 192 sensor readings |
| `Info/MaxChargeVoltage` | `charge_voltage` | Active CVL |
| `Info/MaxChargeCurrent` | `dvcc_max_charge_current` | Temperature-derated limit |
| `Info/MaxDischargeCurrent` | `dvcc_max_discharge_current` | |
| `Alarms/HighVoltage` | `alerts` | 2 if high-voltage alert present |
| `Alarms/LowVoltage` | `alerts` | 2 if low-voltage alert present |
| `Alarms/HighTemperature` | `alerts` | 2 if high-temp alert present |
| `Connected` | `system_status` | 1 if `system_status == "Running"` |

**Flow file:** `/cerbo/node-red-bms-flow.json` in this repo (also at `/tmp/bms_flow.json` on Cerbo while running).

Node-RED UI: `https://192.168.15.178:1881/`

### Critical Node-RED gotchas

1. The `victron-virtual` battery node requires `include_battery_temperature: true` in the node config — otherwise the code silently removes `Dc/0/Temperature` from the D-Bus interface, and ALL `setValuesLocally` calls that include temperature throw an exception that blocks all other values from being set.
2. Send payload directly to the `victron-virtual` node INPUT (not via `victron-output-custom` nodes). Output-custom uses D-Bus `SetValue` per-path which fails for Temperature.
3. Must include a `victron-client` config node with `id: victron-client-id` (hardcoded lookup) in the flow.
4. `/api/status` → `alerts` field is a list `[]`, not a dict. `temperatures` is a flat array of all 192 sensor readings.

## After Cerbo Reflash Recovery

After any Cerbo firmware reflash, perform these steps:

1. **Enable Modbus TCP with write access**: Settings → Services → Modbus TCP → Enable (read/write)

2. **Connect Cerbo to Pi's EM24 server** (MQTT or GUI):
   ```
   W/48e7da8a03c5/settings/0/Settings/ModbusClient/tcp/Devices = {value:tcp:192.168.15.137:502:1}
   ```

3. **Rename Bank Voltages EM24 device**:
   ```bash
   ssh root@192.168.15.178 'dbus -y com.victronenergy.settings /Settings/Devices/cg_/CustomName SetValue Bank Voltages'
   ```

4. **Enable Node-RED**: Settings → Services → Node-RED

5. **Redeploy the Node-RED flow** (copy flow file to Cerbo first):
   ```bash
   scp /projects/battery_balancer/cerbo/node-red-bms-flow.json root@192.168.15.178:/tmp/bms_flow.json
   curl -sk -o /dev/null -w %{http_code} -X POST https://192.168.15.178:1881/flows \
     -H Content-Type: application/json \
     -H Node-RED-Deployment-Type: full \
     -d @/tmp/bms_flow.json
   # Returns 204 on success
   ```

No code installation on the Cerbo is needed.

## Files in This Directory

| File | Description |
|------|-------------|
| `README.md` | This file |

> **Historical note:** Earlier versions of this integration ran a custom `dbus-bms-battery.py` driver directly on the Cerbo GX. That approach was replaced in March 2026 with direct Modbus TCP writes (no Cerbo code required). The old driver files (`dbus-bms-battery.py`, `install_cerbo_driver.sh`, `PageBattery.qml.*`) have been removed.

## Cerbo GX Info

- **IP**: 192.168.15.178 (DHCP — may change after reflash)
- **Password**: 555555
- **Venus OS**: Large image (required for Node-RED), v6.12.23-venus-8
