# Battery Management System (BMS)

Battery monitoring and balancing system for Raspberry Pi 2B.

## Required Files

The following files are required for the BMS to run:

| File | Purpose |
|------|---------|
| `bms.py` | Main BMS script |
| `battery_monitor.ini` | Configuration (temps, voltages, GPIO, email, web) |
| `relaymap.txt` | Relay mapping for balancing between banks |
| `offsets.txt` | Temperature calibration offsets (auto-generated) |
| `modbus/modbus_tool.py` | Modbus TCP communication module |
| `bms.rrd` | RRDTool database for time-series data (auto-generated) |
| `run_bms.sh` | Console auto-start script (for tty1) |

## Startup Methods

### Systemd Services (Auto-start on Boot)

```bash
systemctl status bms.service        # Background service
systemctl status bms-console.service # Console service with auto-restart
systemctl enable bms.service
systemctl enable bms-console.service
```

### Console Auto-Start (tty1/VNC)

The script auto-starts on tty1 console via:
- `/etc/profile.d/run_bms.sh` - sources the run_bms.sh script
- `/root/.bashrc` - inline startup for tty1 login

### Manual Start

```bash
cd /projects/battery_balancer
python3 bms.py
```

## Configuration

Edit `battery_monitor.ini` to configure:
- Temperature monitoring (Modbus TCP sensors)
- Voltage monitoring (ADS1115 ADC)
- GPIO pins for relays
- Email alerts
- Web dashboard settings

## Web Interface

Access the BMS web dashboard at `http://<raspberry-pi-ip>:8080`

Default credentials (if auth enabled): `admin` / `admin123`

## GitHub Sync

```bash
git add . && git commit -m "message" && git push  # Push to GitHub
git pull                                          # Pull from GitHub
```

Remote: `git@github.com:erkel1/bms.git`
