# Battery Management System (BMS)

A Python-based Battery Management System for monitoring and managing large lithium battery packs on Raspberry Pi, with Victron Cerbo GX integration via Modbus TCP.

## Hardware

- **Raspberry Pi 2B** — runs `bms.py` at `192.168.15.137`
- **ADS1115 ADC** — per-bank voltage measurement
- **TCA9548A I2C multiplexer** — multiple I2C channels
- **Lantronix EDS4100** — RS485-to-TCP gateway for NTC temperature sensors (Modbus RTU slaves)
- **M5Stack 4Relay** — relay control for balancing DC-DC converter
- **Victron Cerbo GX** — energy management system, reads BMS limits via Modbus TCP

## Architecture

The pack is 3 banks wired in series. Each bank has 8 parallel batteries and 8 temperature sensors (192 sensors total). The Pi pushes DVCC charge limits directly to the Cerbo GX via Modbus TCP writes every 30 seconds. No custom driver runs on the Cerbo.

```
[Solar MPPT] <-- DVCC <-- [Cerbo GX] <-- Modbus TCP writes (CVL/CCL) -- [Raspberry Pi BMS]
                              |  |                                               |
                              |  +-- EM24 polls (bank voltages) ----------------+
                              |
                         [SmartShunt]  -- SOC / current tracking
                         [Node-RED]    -- virtual battery D-Bus service (Venus OS Large)
```

See [cerbo_gx/README.md](cerbo_gx/README.md) for full Cerbo GX integration details.

## Running

```bash
cd /projects/battery_balancer
python3 bms.py
```

The BMS runs two servers:
- **Web dashboard**: `http://192.168.15.137:8080` — fully self-hosted, no internet required
- **Modbus TCP server**: port 502, unit ID 225

## Web Dashboard

All controls are available via browser. No CDN dependencies — all assets served from the Pi.

### Charge Voltage (`/api/charge_voltage`)
Set the target voltage at the battery terminals. The BMS automatically compensates for cable voltage drop between the MPPT charger and battery.

| Field | Description |
|-------|-------------|
| `charge_voltage` | Target voltage at battery terminals (V), max 63V |
| `cable_drop_compensation` | Auto-learned cable resistance drop (V), read-only |
| `cerbo_voltage` | Actual setpoint sent to Cerbo/MPPT (= target + compensation) |

**Cable drop compensation** only learns when the charger is in CV mode (voltage stable, `|trend| < 0.15V/5-cycles`). It automatically decays if the battery exceeds the target. Not persisted across restarts — relearned each session.

### DVCC Settings (`/api/dvcc_settings`)

| Setting | Description | Default |
|---------|-------------|---------|
| `max_charge_current` | Base charge current limit (A) | 200 |
| `max_discharge_current` | Discharge current limit (A) | 200 |
| `min_discharge_voltage` | Minimum pack discharge voltage (V) | 49.5 |
| `discharge_cable_drop` | Manual cable drop offset for discharge (V) | 0.0 |
| `temp_derate_start` | Temperature where charge current starts reducing (°C) | 35 |
| `temp_derate_end` | Temperature where charge current reaches zero (°C) | 45 |
| `cold_charge_cutoff` | Temperature where cold derating starts (°C) | 5 |
| `cold_charge_min` | Temperature below which charging stops entirely (°C) | 0 |

`cold_charge_cutoff` must be greater than `cold_charge_min`.

### Modbus Registers (Victron DVCC standard)

| Register | Value | Notes |
|----------|-------|-------|
| 305 | Max charge voltage × 10 | Cerbo setpoint = target + cable drop compensation; capped at 63V; overridden by HV clamp |
| 306 | Min discharge voltage × 10 | = `min_discharge_voltage - discharge_cable_drop` |
| 307 | Max charge current × 10 | Temperature-derated effective current |
| 308 | Max discharge current × 10 | |
| 318 | Min cell temperature × 10 (deci°C) | |
| 319 | Max cell temperature × 10 (deci°C) | |
| 320–322 | Per-bank median temperatures × 10 | |

## Configuration (`battery_monitor.ini`)

### `[DVCC]` section

```ini
[DVCC]
max_charge_voltage = 60.3        # Target voltage at battery terminals (V)
max_charge_current = 200.0       # Base charge current limit (A)
max_discharge_current = 200.0    # Discharge current limit (A)
min_discharge_voltage = 49.5     # Low voltage cutoff (V)
discharge_cable_drop = 0.0       # Manual cable drop offset for discharge (V)
temp_derate_start = 35.0         # Hot derating start temperature (°C)
temp_derate_end = 45.0           # Hot derating end temperature (°C)
cold_charge_cutoff = 5.0         # Cold derating start temperature (°C)
cold_charge_min = 0.0            # Cold hard-stop temperature (°C)
```

DVCC settings can be hot-reloaded without restart:
```bash
kill -HUP $(pgrep -f bms.py)
```

## Features

### Temperature-Derated Charging
Charge current is linearly reduced between `temp_derate_start` and `temp_derate_end`. Above `temp_derate_end`, charging stops. Uses the **maximum** temperature across all sensors.

### Cold Charge Limiting
Charge current is linearly reduced between `cold_charge_min` and `cold_charge_cutoff`. Below `cold_charge_min`, charging stops. Uses the **minimum** temperature across all sensors.

### Per-Bank High-Voltage Cutoff
If any bank reaches `HighVoltageThresholdPerBattery`, the charge voltage limit sent to the Cerbo is clamped to the current pack voltage (stopping further charge). Releases automatically when all banks drop below the threshold.

### Auto Cable Drop Compensation
The BMS measures the gap between the commanded Cerbo CVL and the actual battery terminal voltage. This gap equals the cable resistance voltage drop when the charger is in CV mode. An EMA (α=0.1) tracks this and adjusts the Cerbo setpoint upward to compensate.

**Limits:**
- Maximum compensatable drop: `63V − target` (e.g. 2.7V for a 60.3V target)
- Bootstrap limit from zero: ~2V (battery must reach within 2V of target for learning to start)
- Does not learn in CC mode (gated by voltage stability check)

### Discharge Voltage Cable Drop
A fixed manual offset (`discharge_cable_drop`) is subtracted from the minimum discharge voltage sent to the Cerbo. Use this to compensate for cable drop on the discharge path.

### Charge State Indicator
Real-time state displayed in the web dashboard:

| State | Condition |
|-------|-----------|
| Bulk | Voltage rising >0.1V/5-cycles AND more than 2V below target |
| Absorption | Within 2V of target |
| Float | Within 0.5V of target AND voltage stable |
| Discharging | Voltage dropping >0.1V/5-cycles |
| Idle | All other cases |

### Web Server Watchdog
A background thread monitors the Flask server thread every 30 seconds and restarts it automatically if it dies.

### Hardware Watchdog
If the main polling loop hangs for >60 seconds, the hardware watchdog (`/dev/watchdog`) triggers a system reboot.

## Logs

```bash
tail -f /projects/battery_balancer/battery_monitor.log
```

Rotated: 10MB per file, 10 files max (~100MB total).

## Known Issues / Limitations

- **Cable drop maximum**: For >2.7V drop (60.3V target), consider lower target voltage or thicker cable.
- **Cerbo IP changes**: The Cerbo GX uses DHCP. If its IP changes after a reflash, update `[CerboGX] ip` in `battery_monitor.ini` on the Pi.
- **RRD history**: Requires `rrdtool` installed for chart history.
