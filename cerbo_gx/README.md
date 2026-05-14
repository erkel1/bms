# Victron Cerbo GX Integration

## Overview

The Cerbo GX is integrated via **two parallel mechanisms**:

1. **A "thin driver" on the Cerbo** (`dbus-bms-battery.py`) that polls the Pi's Modbus TCP server every 2 s and republishes pack state to D-Bus as a virtual battery service. This is how the Cerbo *sees* the pack — voltage, per-bank voltages, temperatures, alarms, AllowToCharge/Discharge.
2. **Direct Modbus TCP writes from the Pi to the Cerbo** that push DVCC charge limits into the Cerbo's settings service. This is how the Pi *controls* the charger.

The driver is intentionally dumb — all resilience logic (reconnect, staleness detection, safe fallbacks on Pi failure) lives on the Pi side. The Cerbo driver just translates Modbus reads into D-Bus path writes.

## Architecture

```
BMS Pi (192.168.15.202)                       Cerbo GX (192.168.15.203)
┌───────────────────────────────┐             ┌──────────────────────────────────────────┐
│  bms.py                       │             │                                          │
│  ├─ Temp+voltage poll loop    │             │  /service/dbus-bms-battery               │
│  ├─ Balancer + DVCC logic     │             │   ├─ dbus-bms-battery.py (v2.2)          │
│  │                            │  Modbus TCP │   ├─ Polls Pi every 2s                   │
│  ├─ Modbus TCP server         │  port 502   │   ├─ Reads regs 259, 262, 305-331,       │
│  │   port 502, single=True    │  ◄────────  │   │     1282, 1286-1291, 1306+, 318-328  │
│  │   (exposes pack state)     │   reads     │   └─ Publishes com.victronenergy.battery │
│  │                            │             │         .modbus_tcp_bms (instance 1)     │
│  ├─ write_cerbo_dvcc_direct() │             │                                          │
│  │   (raw socket Modbus TCP)  │  Modbus TCP │  com.victronenergy.settings (unit 100)   │
│  │   rate-limited ≥30s        │  writes ──► │   ├─ Settings/SystemSetup/MaxChargeV     │
│  │                            │   regs      │   └─ Settings/SystemSetup/MaxChargeI     │
│  │                            │  2710,2705  │           │                              │
│  │                            │  unit 100   │           ▼                              │
│  │                            │             │   DVCC ──► MultiPlus / MPPT              │
│  │                            │             │                                          │
│  └─ read_cerbo_dc_voltage()   │  Modbus TCP │  com.victronenergy.vebus (unit 227)      │
│      (charge-state detection, │  reads ◄──  │   └─ /Dc/0/Voltage (reg 26)              │
│       cable-drop comp)        │   reg 26    │                                          │
│                               │  unit 227   │  SmartShunt (ttyS7) = battery monitor    │
└───────────────────────────────┘             │   (SoC, current — set in Settings →      │
                                              │    System Setup → Battery monitor)       │
                                              └──────────────────────────────────────────┘
```

## The Thin Driver (`dbus-bms-battery.py`)

**On the Cerbo:**
- Lives at `/data/dbus-bms-battery/dbus-bms-battery.py`
- Supervised by runit at `/service/dbus-bms-battery`
- Logs to `/var/log/dbus-bms-battery/current` (multilog with timestamps)
- D-Bus service: `com.victronenergy.battery.modbus_tcp_bms`, device instance 1

**Pi config in the driver** (top of file):
```python
BMS_HOST             = '192.168.15.202'
BMS_PORT             = 502
BMS_UNIT_ID          = 1
POLL_INTERVAL_MS     = 2000      # GLib timeout
HARD_DISCONNECT_S    = 1800      # 30 min silence threshold
RECONNECT_READS      = 5         # successful reads required to flip Connected back to 1
```

**What it pulls from the Pi each poll** (in order):

| Register | Count | Function | Sample D-Bus path |
|---|---|---|---|
| 259 | 16 | Pack voltage, data-valid flag, temperature, alarms | `/Dc/0/Voltage`, `/Dc/0/Temperature`, `/Alarms/*` |
| 305 | 27 | DVCC limits + AllowToCharge/Discharge | `/Info/MaxChargeVoltage`, `/Io/AllowToCharge` |
| 1282 | 1 | State (9 = Running, 10 = Error, 14 = Standby) | `/State` |
| 1286 | 6 | Topology + bank min/max voltage | `/System/NrOfBatteries`, `/System/Min/MaxCellVoltage` |
| 1306 | 3 | Per-bank voltages | `/Voltages/Cell1`..`Cell3`, `/Voltages/Sum`, `/Voltages/Diff` |
| 318 | 5 | Temperature min/max + per-bank median | `/System/Min/MaxCellTemperature`, `/Temperatures/Cell1`..`Cell3` |

**Data-valid gate:** register 260 must be 1 before the driver publishes anything. While the Pi is booting (no first poll yet), the driver holds D-Bus values unchanged.

**Reconnect / failure behaviour:**
- Each poll uses one persistent `ModbusTcpClient` (3 s connect timeout). On any error: `_mod_ok = False`, reconnect on next attempt.
- Error counter: 30 errors = first log line. 60 errors = next log line. Every 60 thereafter.
- At ~900 errors (≈30 min): one-shot `_safe_fallback()` writes safe values — `AllowToCharge=1`, `AllowToDischarge=1`, all alarms cleared, `/Connected=0`, **and `/Dc/0/Voltage`/`/Dc/0/Temperature` set to None** (so the Cerbo doesn't continue reporting stale values).
- Recovery: when reads succeed again, need 5 consecutive good polls before `/Connected` flips back to 1.

## DVCC Direct Writes (Pi → Cerbo)

The Pi sends charge limits directly to the Cerbo's `com.victronenergy.settings` service via Modbus TCP:

| Register | Unit | D-Bus path | Scale | Description |
|----------|------|------------|-------|-------------|
| 2710 | 100 | `Settings/SystemSetup/MaxChargeVoltage` | ×10 | e.g. 60.3 V → 603 |
| 2705 | 100 | `Settings/SystemSetup/MaxChargeCurrent` | ×1 A | e.g. 200 A → 200 |

The values come from `bms.py`'s DVCC pipeline (hot derate → cold derate → HV clamp → cable-drop comp). On a high-voltage alarm, CVL is forced to 49.5 V to stop charging.

**Rate limiting:** Writes only happen if any of:
- ≥30 s since last write, OR
- CVL changed by ≥0.05 V, OR
- HV alarm state flipped

**Don't lower the 30 s minimum** — earlier 5 s polling caused Venus OS crashes.

**Prerequisites on Cerbo:** Settings → Services → Modbus TCP → Enable, set to read/write.

## Files in This Directory

| File | Description |
|------|-------------|
| `dbus-bms-battery.py` | The thin driver. Deployed to `/data/dbus-bms-battery/` on the Cerbo |
| `install_cerbo_driver.sh` | Deploys driver + runit service + GUI mod. Run from Pi |
| `PageBattery.qml.modified` | Classic GUI QML patched to show all 3 bank voltages |
| `PageBattery.qml.original` | Pristine Cerbo QML, kept for diff/recovery |
| `README.md` | This file |

## Classic-GUI Cell Voltage Display (QML mod)

`PageBattery.qml.modified` replaces the standard `Voltage | Current | Power` row on the BMS battery page with `Cell1 | Cell2 | Cell3` when `/Voltages/Cell1` is valid. This only affects the **classic GUI** (touchscreen + Remote Console via NeatVNC on port 5901).

**The new GUI v2 (`https://<cerbo>/gui-v2/`) does NOT render per-cell voltages** — the WASM binary has no references to `/Voltages/Cell*`. Only `/System/MinCellVoltage` and `/System/MaxCellVoltage` show up there (under the Details page as "Lowest/Highest cell voltage"). This is a Victron limitation, not something this driver can work around.

The QML mod is installed by `install_cerbo_driver.sh` and survives until the next firmware reflash.

## Initial Install (or after reflash)

On the Cerbo, first enable SSH access:
- Settings → General → set Root Password (and/or Access level → Superuser)
- Then enable Modbus TCP (read/write)

Then from the Pi:

```bash
cd /projects/battery_balancer/cerbo_gx
./install_cerbo_driver.sh 192.168.15.203 778394
```

The installer:
1. Copies `dbus-bms-battery.py` to `/data/dbus-bms-battery/` on the Cerbo
2. Creates the runit service scripts at `/data/dbus-bms-battery/service/` and symlinks to `/service/dbus-bms-battery`
3. Backs up the original `PageBattery.qml` and installs the modified version
4. Starts the service

Verify after install:
```bash
sshpass -p 778394 ssh root@192.168.15.203 'svstat /service/dbus-bms-battery'
# Should show: /service/dbus-bms-battery: up (pid ...)
sshpass -p 778394 ssh root@192.168.15.203 'dbus -y com.victronenergy.battery.modbus_tcp_bms /Voltages/Cell1 GetValue'
# Should return a number around 18-21 V
```

## Toggling the Driver via Web API

The Pi exposes `/api/cerbo_integration` (GET/POST `{enabled: true/false}`) which uses `sshpass` to run `svc -u` / `svc -d /service/dbus-bms-battery` on the Cerbo. State persists in `cerbo_integration_state` in `data_dir`. This relies on the `[CerboGX] password` value in `battery_monitor.ini`.

## Cerbo Settings That Need To Be Right

| Setting | Value | Why |
|---|---|---|
| Services → Modbus TCP | Enabled, read/write | For DVCC writes from Pi |
| System Setup → Battery monitor | **SmartShunt** (NOT "Default" or BMS) | The BMS driver doesn't report current/SoC. The SmartShunt does. Default auto-pick will choose the BMS and you lose current/SoC display |
| System Setup → DVCC → SVS / DVCC | as desired | DVCC must be on for `Settings/SystemSetup/MaxChargeVoltage` writes to actually limit charging |

## Cerbo GX Info

- **IP**: 192.168.15.203 (whatever mechanism — Cerbo static config or DHCP reservation — the Pi just reads it from `battery_monitor.ini` `[CerboGX] ip`)
- **Root password**: 778394 (also in `battery_monitor.ini` `[CerboGX] password`)
- **Node-RED admin**: admin / 778394 (not currently used — Node-RED can be disabled)
- **Venus OS**: Large image, v6.12.23-venus-8 or later

## Troubleshooting

**Driver shows no data on D-Bus:**
- Pi's Modbus server reachable? `nc -zv 192.168.15.202 502` from the Cerbo
- Pi BMS has done a valid poll? Register 260 (data-valid) must be 1 — check `bms.py` log for "Poll cycle complete"
- Check driver log: `tail /var/log/dbus-bms-battery/current` on the Cerbo

**Driver constantly logs "Connection refused":**
- Pi BMS process not running, or Pi IP changed. Check `pgrep -af bms.py` on the Pi
- If Pi IP changed, update `BMS_HOST` in `dbus-bms-battery.py` and redeploy

**CVL not updating on the Cerbo:**
- Modbus TCP "read/write" enabled? Settings → Services → Modbus TCP
- Check Pi log for `Cerbo Modbus exception` lines — register 2710 writes can fail with function-code error if write access is off
- DVCC enabled on the Cerbo? Without DVCC, the MaxChargeVoltage setting is ignored by the charger

**Two BMS battery devices showing up:**
- Old Node-RED virtual battery flow may still be deployed. The current flow at `/projects/battery_balancer/cerbo/node-red-bms-flow.json` has the virtual-battery node removed; if you see `com.victronenergy.battery.virtual_vv_bat` on D-Bus, the flow was reverted

**Battery monitor showing wrong SoC / current:**
- System Setup → Battery monitor must be set to the SmartShunt explicitly, not "Default" (Default auto-picks the BMS, which has no current measurement)

## History

- **March 2026 — initial integration:** Custom `dbus-bms-battery.py` driver on Cerbo. Pi exposed Modbus TCP server, driver bridged to D-Bus
- **March 28 2026 — switched to "no driver on Cerbo":** Direct Modbus writes from Pi, Carlo Gavazzi EM24 emulation for bank-voltage display, Node-RED virtual battery for the rest
- **May 14 2026 — reverted to the thin driver:** GUI v2 doesn't render the EM24 grid device meaningfully on the battery page, and the Node-RED virtual battery couldn't be coaxed into showing per-cell voltages. The thin-driver approach gives the cleanest per-bank display on the classic GUI via the QML mod, and works around GUI v2's limitations as best as possible. EM24 emulation and the Node-RED battery node were removed at the same time
