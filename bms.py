#!/usr/bin/env python3


def get_port_for_slave(slave_addr, slave_addresses, slave_ports, default_port):
    """Get the Modbus port for a given slave address."""
    try:
        idx = slave_addresses.index(slave_addr)
        return slave_ports[idx] if idx < len(slave_ports) else default_port
    except ValueError:
        return default_port

def get_ip_for_slave(slave_addr, slave_addresses, slave_ips, default_ip):
    """Get the IP address for a given slave address."""
    try:
        idx = slave_addresses.index(slave_addr)
        return slave_ips[idx] if idx < len(slave_ips) else default_ip
    except ValueError:
        return default_ip
# --------------------------------------------------------------------------------
# Battery Management System (BMS) Script Documentation
# --------------------------------------------------------------------------------
#
# **Script Name:** bms.py
# **Version:** 1.10 (As of September 07, 2025) - Added thread safety with locks for web_data to prevent race conditions. Enhanced error handling in API routes with try/except and JSON error responses.
# **Author:** [Your Name or Original Developer] - Built for Raspberry Pi-based battery monitoring and balancing.
# **Purpose:** This script acts as a complete Battery Management System (BMS) for a configurable NsXp battery configuration (N series banks, X parallel cells per bank, where X = sensors_per_bank * number_of_parallel_batteries). It monitors temperatures from multiple Modbus slaves and voltages, balances charge between banks, detects issues, logs events, sends alerts, and provides user interfaces via terminal (TUI) and web dashboard. Includes time-series logging using RRDTool, ASCII line charts in TUI, and interactive charts in web via Chart.js.
#
# **Detailed Overview:**
# - **Temperature Monitoring:** Connects to NTC thermistors via Lantronix EDS4100 using Modbus TCP in multidrop RS485 configuration. Supports multiple slaves (one per parallel battery), each with num_series_banks * sensors_per_bank channels. Aggregates readings into global channels, groups by series bank for analysis. Applies calibration offsets, checks anomalies (high/low, deviations, rises, lags, disconnections). Handles per-slave errors gracefully.
# - Calibration: On first valid read (all sensors > valid_min across all slaves), computes overall median and offsets. Saves to 'offsets.txt' for future runs.
# - Anomalies Checked:
# - Invalid/Disconnected: Reading <= valid_min (e.g., 0.0°C).
# - High: > high_threshold (e.g., 42.0°C).
# - Low: < low_threshold (e.g., 0.0°C).
# - Deviation: Absolute > abs_deviation_threshold (e.g., 2.0°C) or relative > deviation_threshold (e.g., 10%) from bank median.
# - Abnormal Rise: Increase > rise_threshold (e.g., 2.0°C) since last poll.
# - Group Lag: Change differs from bank median change by > disconnection_lag_threshold (e.g., 0.5°C).
# - Sudden Disconnection: Was valid, now invalid.
# - **Voltage Monitoring & Balancing:** Uses ADS1115 ADC over I2C to measure voltages of num_series_banks banks. Balances if difference > VoltageDifferenceToBalance (e.g., 0.1V) by connecting high to low bank via relays and DC-DC converter (relay logic configurable via INI).
# - Heating Mode: If any temperature < 10°C, balances regardless of voltage difference to generate heat.
# - Safety: Skips balancing if alerts active (e.g., anomalies) or if balancer_failed flag is set. Rests for BalanceRestPeriodSeconds (e.g., 60s) after balancing.
# - Balancing Verification: During startup and regular balancing, monitors voltage trends. If no expected decrease in source or increase in destination (min_delta, e.g., 0.01V), raises alert and sets balancer_failed=True to prevent future balancing until restart or manual reset.
# - Voltage Checks: Alerts if < LowVoltageThresholdPerBattery (e.g., 18.5V), > HighVoltageThresholdPerBattery (e.g., 21.0V), or zero.
# - **Alerts & Notifications:** Logs to 'battery_monitor.log'. Activates alarm relay on issues. Sends throttled emails (e.g., every 3600s) via SMTP.
# - **Watchdog:** If enabled, pets hardware watchdog via dedicated thread (every 5s with aliveness check via timestamp) to prevent resets on hangs. Uses /dev/watchdog with 15s timeout (Pi max).
# - **User Interfaces:**
# - **TUI (Terminal UI):** Uses curses for real-time display: ASCII art batteries (dynamic for num_series_banks) with voltages/temps, alerts, balancing progress bar/animation, last 20 events. Now includes ASCII line charts for voltage history per bank and median temperature, placed in the top-right section for visualization of trends over time.
# - **Web Dashboard:** HTTP server on port 8080 (configurable). Shows voltages, temps, alerts, balancing status. Supports API for status/balance/history. Optional auth/CORS. Now includes interactive time-series charts using Chart.js for voltages per bank and median temperature, placed at the top of the page after the header for easy viewing.
# - **Time-Series Logging:** Uses RRDTool for persistent storage of bank voltages and overall median temperature. Data is updated every poll interval (e.g., 10s), but RRD is configured with 1min steps for aggregation. History is limited to ~480 entries (e.g., 8 hours). Fetch functions retrieve data for TUI and web rendering.
# - **Startup Self-Test:** Validates config, hardware connections (I2C/Modbus per slave), initial reads, balancer (tests all pairs for voltage changes).
# - Retries on failure after 2min. After max retries, proceeds to main loop with startup_failed reset to False to allow balancing, avoiding perpetual blocking. Logs warnings.
# - **Error Handling:** Retries reads (exponential backoff), handles missing hardware (test mode), logs tracebacks, graceful shutdown on Ctrl+C. Per-slave Modbus errors handled with alerts and fallback values.
# - **Configuration:** From 'battery_monitor.ini'. Defaults if missing keys. See INI documentation below.
# - **Logging:** Configurable level (e.g., INFO). Timestamps events.
# - **Shutdown:** Cleans GPIO, web server, watchdog on exit.
# **Key Features Explained for Non-Programmers:**
# - Imagine this script as a vigilant guardian for your battery pack. It constantly checks the "health" (temperature and voltage) of each part of the battery.
# - Temperatures: Like checking body temperature with 96 thermometers (for 4 batteries). If one is too hot/cold or acting weird, it raises an alarm.
# - Voltages: Measures "energy level" in each bank. If one has more energy than another, it transfers some to balance them, like pouring water between buckets.
# - Heating: In cold weather (<10°C), it deliberately transfers energy to create warmth inside the battery cabinet.
# - Alerts: If something's wrong, it logs it, turns on a buzzer/light (alarm relay), and emails you (but not too often to avoid spam).
# - Interfaces: Terminal shows a fancy text-based dashboard with ASCII charts for trends and lists all temps; web page lets you view from browser with interactive charts and full temp lists.
# - Startup Check: Like a self-diagnostic when your car starts – ensures everything's connected and working before running. Proceeds after retries with flags reset for operation.
# - Time-Series: Tracks history of voltages and temps, shows trends in charts to spot patterns over time.
# - Balancing Fail-Safe: Verifies energy transfer by checking voltage changes; disables balancing if hardware issue detected (e.g., relays not switching).
# **How It Works (Step-by-Step for Non-Programmers):**
# 1. **Start:** Loads settings from INI file (like a recipe book).
# 2. **Setup:** Connects to hardware (sensors, relays) – if missing, runs in "pretend" mode. Creates/loads RRD database for history.
# 3. **Self-Test:** Checks if config makes sense, hardware responds (per Modbus slave), sensors give good readings, aggregated. Balancing actually changes voltages (verifies relay switching via voltage deltas). If fail, alerts and retries. After max retries, proceeds with flags reset.
# 4. **Main Loop (Repeats Forever):**
# - Read temperatures from all slaves, aggregate.
# - Calibrate them (adjust based on startup values for accuracy).
# - Check for temperature problems (too hot, too cold, etc.).
# - Read voltages from configured banks.
# - Check for voltage problems (too high, too low, zero).
# - Update RRD database with voltages and median temp.
# - If cold (<10°C anywhere), balance to heat up (with verification).
# - Else, if voltages differ too much, balance normally (with verification).
# - Skip if alerts active or balancer failed.
# - Fetch history from RRD for charts.
# - Update terminal (with ASCII charts and full temp lists)/web displays (with Chart.js and full lists).
# - Log events, send emails if issues.
# - Update alive timestamp for watchdog.
# - Wait a bit (e.g., 10s), repeat.
# 5. **Balancing Process:** Connects high to low bank with relays, turns on converter to transfer charge, monitors voltages for changes, shows progress, turns off after time. Verifies deltas; alerts/disables if failed.
# 6. **Shutdown:** If you press Ctrl+C, cleans up connections safely.
# **Updated Logic Flow Diagram (ASCII - More Detailed):**
#
"""
+--------------------------------------+
| Load Config from INI |
| (Read settings file, incl. parallel) |
+--------------------------------------+
|
v
+--------------------------------------+
| Setup Hardware |
| (I2C bus, GPIO pins, RRD DB) |
| Compute sensor groupings |
+--------------------------------------+
|
v
+--------------------------------------+
| Startup Self-Test |
| (Config valid? |
| Hardware connected? Per slave? |
| Initial reads OK? Aggregated? |
| Balancer works? Verify deltas) |
| If fail: Alert, Retry |
| After max retries: Reset flags, Proceed |
+--------------------------------------+
|
v
+--------------------------------------+
| Start Watchdog Thread |
| (Pet every 5s if main alive) |
+--------------------------------------+
|
v
+--------------------------------------+ <---------------------+
| Main Loop (Repeat) | |
+--------------------------------------+ |
| |
v |
+--------------------------------------+ |
| Read Temps (Per Slave, Aggregate) | |
| (Handle errors per slave) | |
+--------------------------------------+ |
| |
v |
+--------------------------------------+ |
| Calibrate Temps | |
| (Apply offsets if set) | |
+--------------------------------------+ |
| |
v |
+--------------------------------------+ |
| Check Temp Issues | |
| (High/Low/Deviation/ | |
| Rise/Lag/Disconnect, with bat info) | |
+--------------------------------------+ |
| |
v |
+--------------------------------------+ |
| Read Voltages (ADC) | |
| (3 banks via I2C) | |
+--------------------------------------+ |
| |
v |
+--------------------------------------+ |
| Check Voltage Issues | |
| (High/Low/Zero) | |
+--------------------------------------+ |
| |
v |
+--------------------------------------+ |
| Update RRD with Data | |
| (Voltages, Median Temp) | |
+--------------------------------------+ |
| |
v |
+--------------------------------------+ |
| If Any Temp < 10°C: | |
| Balance for Heating (Verify Deltas) | |
| Else If Volt Diff > Th: | |
| Balance Normally (Verify Deltas) | |
| (High to Low Bank) | |
| Skip if Alerts/Balancer Failed | |
+--------------------------------------+ |
| |
v |
+--------------------------------------+ |
| Fetch RRD History | |
| (For Charts) | |
+--------------------------------------+ |
| |
v |
+--------------------------------------+ |
| Update TUI (Terminal) | |
| & Web Dashboard | |
| (Show status, alerts, | |
| ASCII/Chart.js Charts, full temps) | |
+--------------------------------------+ |
| |
v |
+--------------------------------------+ |
| Log Events, Send Email | |
| if Issues & Throttled | |
+--------------------------------------+ |
| |
v |
+--------------------------------------+
| Update Alive Timestamp |
+--------------------------------------+
| |
v |
+--------------------------------------+
| Sleep (Poll Interval) |
+--------------------------------------+
| |
+-------------------------------------------------------------+
"""
# **Dependencies (What the Script Needs to Run):**
# - **Python Version:** 3.11 or higher (core language for running the code).
# - **Hardware Libraries:** smbus (for I2C communication with sensors/relays), RPi.GPIO (for controlling Raspberry Pi pins). Install: sudo apt install python3-smbus python3-rpi.gpio.
# - **External Library:** art (for ASCII art in TUI). Install: pip install art.
# - **Time-Series Storage:** rrdtool (for RRD database). Install: sudo apt install rrdtool.
# - **Standard Python Libraries:** socket (networking), statistics (math like medians), time (timing/delays), configparser (read INI), logging (save logs), signal (handle shutdown), gc (memory cleanup), os (files), sys (exit), argparse (command-line), threading (web server and watchdog), json/http.server/urllib/base64 (web), traceback (errors), fcntl/struct (watchdog), subprocess (for rrdtool commands), xml.etree.ElementTree (for parsing RRD XML output).
# - **Hardware Requirements:** Raspberry Pi (any model, detects for watchdog), ADS1115 ADC (voltage), TCA9548A multiplexer (I2C channels), Relays (balancing), Lantronix EDS4100 (Modbus for temps), GPIO pins (e.g., 5 for DC-DC, 6 for alarm, 4 for fan).
# - **No Internet for Installs:** All libraries must be pre-installed; script can't download. For web charts, Chart.js is loaded via CDN (requires internet for dashboard users).
# **Installation Guide (Step-by-Step for Non-Programmers):**
# 1. **Install Python:** On Raspberry Pi, run in terminal: sudo apt update; sudo apt install python3.
# 2. **Install Hardware Libraries:** sudo apt install python3-smbus python3-rpi.gpio.
# 3. **Install Art Library:** pip install art (or sudo pip install art if needed).
# 4. **Install RRDTool for Time-Series:** sudo apt install rrdtool.
# 5. **Enable I2C:** Run sudo raspi-config, go to Interface Options > I2C > Enable, then reboot.
# 6. **Create/Edit INI File:** Make 'battery_monitor.ini' in same folder as script. Copy template below and fill in values (e.g., emails, IPs, slave addresses).
# 7. **Run Script:** sudo python bms.py (needs root for hardware access).
# **Validate Config:** python bms.py --validate-config [--data-dir /path/to/config]
# 8. **View Web Dashboard:** Open browser to http://<your-pi-ip>:8080. Charts will load via Chart.js CDN.
# 9. **Logs:** Check 'battery_monitor.log' for details. Set LoggingLevel=DEBUG in INI for more info.
# 10. **RRD Database:** Created automatically as 'bms.rrd' on first run. No manual setup needed.
# **Notes & Troubleshooting:**
# - **Hardware Matching:** Ensure INI addresses/pins match your setup. Wrong IP/port/slave = no temps.
# - **Email Setup:** Use Gmail app password (not regular password) for SMTP_Password.
# - **TUI Size:** Terminal should be wide (>80 columns) and tall for full display, including all temps and charts.
# - **Test Mode:** If no hardware, script runs without crashing but warns.
# - **Security:** For web, enable auth_required=True and set strong username/password.
# - **Offsets File:** 'offsets.txt' stores calibration – delete to recalibrate.
# - **RRD Issues:** If rrdtool commands fail, check installation and permissions. Database 'bms.rrd' stores aggregated data; use rrdtool info bms.rrd for details.
# - **Common Errors:** I2C errors = check wiring/connections. Modbus errors = check Lantronix IP/port/slave addresses/RS485 wiring. RRD errors = ensure rrdtool installed and path correct.
# - **Performance:** Poll interval ~10s; balancing ~5s. Adjust in INI. Charts fetch from RRD (~480 entries) won't impact performance.
# - **Customization:** Edit thresholds in INI for your battery specs (e.g., Li-ion safe ranges). For longer history, adjust RRA in RRD creation.
# - **Watchdog Note:** Dedicated thread ensures reliable petting; resets only on true main hangs.
# - **Balancing Failures:** If voltage doesn't change during balancing, script detects it (no silent fail), alerts, and disables future balancing to prevent hardware damage.
# --------------------------------------------------------------------------------
# Code Begins Below - With Line-by-Line Comments for Non-Programmers
# --------------------------------------------------------------------------------
# Import statements: These bring in tools and libraries that the script needs to work.
# Think of them as gathering the ingredients and tools before cooking.
import socket # Network communication tool - like a phone to call the temperature sensor device over the internet.
import statistics # Math helper for calculating averages and middle values of temperature readings.
import time # Time management - handles delays, waits, and records when things happen (like a clock).
import configparser # Settings reader - loads configuration from the INI file, like reading a recipe book.
import logging # Event recorder - writes messages about what's happening to a log file for later review.
import signal # Shutdown handler - catches when user presses Ctrl+C to stop the program nicely.
import gc # Memory cleaner - removes unused data from memory to keep the program running smoothly.
import datetime # Date and time utilities - used for timestamping RRD backups.
import os # File system manager - handles reading/writing files, like saving calibration data.
import sys # System controller - manages program exit and command-line arguments.
import argparse # Command-line argument parser - handles options like --validate-config.
import threading # Multi-tasking tool - runs the web server separately from the main program.
import json # Data formatter - converts data to/from a format that web browsers understand.
from urllib.parse import urlparse, parse_qs # Web request parser - breaks down web addresses and data.
import base64 # Secret code decoder - handles user login credentials for the web interface.
import traceback # Error detail recorder - captures full error information for debugging.
import subprocess # External program runner - executes other tools like the database updater.
import xml.etree.ElementTree as ET # XML data reader - parses database output files.
try:
    from flask import Flask, jsonify, request, make_response # Web server framework for reliable API handling.
except ImportError:
    print("Flask not available - web interface disabled") # Warn user if Flask library is missing.
    Flask = None # Set to none if missing, so web features are skipped.
try:
    import smbus # Communicates with I2C devices like the ADC and relays - hardware talker.
    import RPi.GPIO as GPIO # Controls Raspberry Pi GPIO pins for relays - pin controller.
except ImportError:
    print("Hardware libraries not available - running in test mode") # Warn user.
    smbus = None # Set to none if missing.
    GPIO = None # Set to none if missing.
from email.mime.text import MIMEText # Builds email messages - email builder.
import smtplib # Sends email alerts - email sender.
import curses # Creates the terminal-based Text User Interface (TUI) - terminal drawer.
from art import text2art # Generates ASCII art for the TUI display - art maker.
try:
    import fcntl # For watchdog ioctl - low-level control.
except ImportError:
    fcntl = None
import struct # For watchdog struct - data packer.
# Modbus TCP server for Victron Cerbo GX integration
try:
    from pymodbus.server import StartTcpServer
    from pymodbus.datastore import ModbusServerContext, ModbusDeviceContext, ModbusSequentialDataBlock
    from pymodbus.pdu.device import ModbusDeviceIdentification
    MODBUS_SERVER_AVAILABLE = True
except ImportError:
    MODBUS_SERVER_AVAILABLE = False
    print("pymodbus not available - Victron Cerbo GX integration disabled")

config_parser = configparser.ConfigParser(comment_prefixes=(';', '#')) # Object to read INI file - config reader, handles ; and # comments.
bus = None # I2C bus for communicating with hardware - hardware connection.
last_email_time = 0 # Tracks when the last email alert was sent - email timer.
balance_start_time = None # Tracks when balancing started - balance clock start.
last_balance_time = 0 # Tracks when the last balancing ended - balance clock end.
battery_voltages = [] # Stores current voltages for each bank - voltage list.
previous_temps = None # Stores previous temperature readings - old temps.
# Enhanced error handling for temperature readings
last_good_temps = {}  # Cache of last known good values per slave:channel
consecutive_failures = {}  # Track consecutive failures per slave:channel
CONSECUTIVE_FAILURE_THRESHOLD = 5  # Only report error after 5 consecutive failures
REASONABLE_TEMP_MIN = -10.0  # Minimum reasonable temperature in C
REASONABLE_TEMP_MAX = 60.0  # Maximum reasonable temperature in C

previous_bank_medians = None # Stores previous median temperatures per bank - old medians.
# ---------------------------------------------------------------------------
# Modbus Error Classification for better error handling
# ---------------------------------------------------------------------------
class ModbusError(Exception):
    """Enum-like class for Modbus error types."""
    TIMEOUT = "timeout"
    CRC_MISMATCH = "crc_mismatch"
    SHORT_RESPONSE = "short_response"
    CONNECTION_REFUSED = "connection_refused"
    SLAVE_NOT_RESPONDING = "slave_not_responding"
    ILLEGAL_FUNCTION = "illegal_function"
    UNKNOWN = "unknown"

# ---------------------------------------------------------------------------
# Communication Statistics Tracking (Global)
# ---------------------------------------------------------------------------
comm_stats = {
    'slave_addresses': [],
    'stats': {}
}

def init_comm_stats(slave_addresses):
    """Initialize communication statistics for all slaves."""
    global comm_stats
    comm_stats['slave_addresses'] = slave_addresses
    for addr in slave_addresses:
        comm_stats['stats'][addr] = {
            'success_count': 0,
            'fail_count': 0,
            'last_success': None,
            'last_error': None,
            'last_error_type': None,
            'avg_response_time': 0.0,
            'response_times': []
        }

def update_comm_stats(slave_addr, success, error_type=None, response_time=None):
    """Update communication statistics for a slave."""
    if slave_addr not in comm_stats['stats']:
        comm_stats['stats'][slave_addr] = {
            'success_count': 0,
            'fail_count': 0,
            'last_success': None,
            'last_error': None,
            'last_error_type': None,
            'avg_response_time': 0.0,
            'response_times': []
        }
    
    stats = comm_stats['stats'][slave_addr]
    if success:
        stats['success_count'] += 1
        stats['last_success'] = time.time()
        stats['last_error'] = None
        stats['last_error_type'] = None
        if response_time is not None:
            stats['response_times'].append(response_time)
            if len(stats['response_times']) > 100:
                stats['response_times'].pop(0)
            stats['avg_response_time'] = sum(stats['response_times']) / len(stats['response_times'])
    else:
        stats['fail_count'] += 1
        stats['last_error'] = time.time()
        stats['last_error_type'] = error_type

def get_comm_stats():
    """Get communication statistics summary."""
    result = {'slaves': [], 'total_success': 0, 'total_fail': 0}
    for addr in comm_stats.get('slave_addresses', []):
        stats = comm_stats['stats'].get(addr, {'success_count': 0, 'fail_count': 0, 'last_success': None, 'last_error': None, 'last_error_type': None, 'avg_response_time': 0.0})
        total = stats['success_count'] + stats['fail_count']
        success_rate = (stats['success_count'] / total * 100) if total > 0 else 0.0
        result['slaves'].append({
            'slave_addr': addr,
            'success_count': stats['success_count'],
            'fail_count': stats['fail_count'],
            'success_rate': round(success_rate, 1),
            'last_success': stats['last_success'],
            'last_error': stats['last_error'],
            'last_error_type': stats['last_error_type'],
            'avg_response_time': round(stats['avg_response_time'], 3)
        })
        result['total_success'] += stats['success_count']
        result['total_fail'] += stats['fail_count']
    return result

run_count = 0 # Counts how many times the main loop has run - cycle counter.
startup_offsets = None # Temperature calibration offsets from startup - adjustment numbers.
startup_median = None # Median temperature at startup - average at start.
startup_set = False # Indicates if temperature calibration is set - calibration flag.
_calibration_cache = None  # Cache for perform_calibration to avoid repeated disk reads
alert_states = {} # Tracks alerts for each temperature channel - alert memory.
balancing_active = False # Indicates if balancing is currently happening - balancing flag.
cerbo_integration_enabled = True  # Whether the Cerbo GX dbus-bms-battery service is active.
startup_failed = False # Indicates if startup tests failed - test fail flag.
startup_alerts = [] # Stores startup test failure messages - test error list.
balancer_failed = False # New: Indicates if balancer hardware failed verification - prevents future balancing.
balancer_failed_time = None # Timestamp when balancer_failed was set - for auto-recovery timing.
balancer_fail_count = 0 # Consecutive balance failure count - escalates recovery cooldown.
balancer_fail_reason = "" # Stores the specific reason for the last balance failure - shown in GUI.
web_server = None # Web server object - web host.
event_log = [] # Stores the last N events (configurable) - event history.
web_data = {
    'voltages': [], # Will be filled dynamically based on num_series_banks
    'temperatures': [], # Will be filled dynamically based on total_channels
    'bank_summaries': [], # Will be filled dynamically based on num_series_banks
    'alerts': [], # Current active alerts - alert list.
    'balancing': False, # Balancing status - balance flag.
    'last_update': time.time(), # Last data update timestamp - update time.
    'system_status': 'Initializing' # System status (e.g., Running, Alert) - status string.
}
BANK_SENSOR_INDICES = [] # Will be filled dynamically based on num_series_banks
NUM_BANKS = 3 # Will be overridden by config in main()
WATCHDOG_DEV = '/dev/watchdog' # Device file for watchdog - hardware reset preventer.
watchdog_fd = None # File handle for watchdog - open connection.
alive_timestamp = 0.0 # Shared timestamp updated by main to indicate aliveness - for watchdog thread.
RRD_FILE = 'bms.rrd' # RRD database file for storing time-series data - persistent storage.
HISTORY_LIMIT = 1440 # Number of historical entries to retain (e.g., ~24 hours at 1min steps) - limit for memory/efficiency.
data_lock = threading.Lock() # Lock for thread-safe access to web_data

# Modbus TCP Server globals for Victron Cerbo GX integration
modbus_server_running = False
modbus_datastore = None
modbus_registers = {}  # Cache of register values
mdns_process = None  # mDNS service advertisement process for Victron discovery

def check_dependencies():
    """
    Check for required and optional dependencies at startup.
    This function is like a pre-flight checklist for the script. It verifies if all the necessary software tools (libraries)
    are installed on the system. Critical ones (like hardware communication libraries) are mandatory—if missing, the script stops.
    Optional ones (like for web interface or charts) are noted but the script continues without them, using fallback modes.
    This prevents crashes later when the script tries to use missing tools. For non-programmers: Imagine checking if your toolbox
    has all hammers and screwdrivers before building something; if a hammer is missing, you can't proceed safely.
    
    Returns:
        None: Just logs messages and exits if critical issues found.
    """
    # Define lists of critical and optional dependencies.
    # Critical: Hardware-related libraries without which the script can't interact with physical devices.
    critical_deps = ['smbus', 'RPi.GPIO']
    # Optional: Features like time-series charts, ASCII art, or web server—nice to have but not essential.
    optional_deps = ['rrdtool', 'art', 'flask']
    # Lists to track missing items.
    missing_critical = []
    missing_optional = []
   
    # Loop through critical dependencies and try to import each one.
    # If import fails, add to missing list—like testing if a tool works by picking it up.
    for dep in critical_deps:
        try:
            __import__(dep)  # Attempt to load the library into memory.
        except ImportError:
            missing_critical.append(dep)  # Note it's missing if load fails.
   
    # Loop through optional dependencies and test them similarly.
    # For rrdtool, we run a command-line check instead of import, as it's an external tool.
    for dep in optional_deps:
        try:
            if dep == 'rrdtool':
                # For rrdtool, run a version check command silently (no output shown).
                subprocess.check_call(['rrdtool', '--version'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                __import__(dep)  # Standard import test for others.
        except (ImportError, subprocess.CalledProcessError, FileNotFoundError):
            missing_optional.append(dep)  # Note missing if test fails.
   
    # If any critical dependency is missing, log an error, print a message with install instructions, and exit the script.
    # This ensures the system is properly set up before proceeding.
    if missing_critical:
        msg = f"Critical dependencies missing: {', '.join(missing_critical)}. Install with: sudo apt install python3-{' python3-'.join(missing_critical)}. Exiting."
        logging.error(msg)  # Write error to log file.
        print(msg)  # Show on screen.
        sys.exit(1)  # Stop the script immediately.
   
    # For missing optional deps, log warnings and print helpful messages with install commands.
    # The script continues but with reduced features (e.g., no web dashboard if Flask missing).
    if missing_optional:
        for dep in missing_optional:
            if dep == 'rrdtool':
                msg = "Optional dependency 'rrdtool' missing. Time-series logging disabled. Install with: sudo apt install rrdtool."
            elif dep == 'art':
                msg = "Optional dependency 'art' missing. ASCII art disabled. Install with: pip install art."
            logging.warning(msg)  # Write to log.
            print(msg)  # Show on screen.
   
    # If all checks pass, log success.
    logging.info("Dependency check passed.")

def get_bank_for_channel(ch):
    """
    Find which battery bank a temperature sensor belongs to.
    This function is like a map or directory that tells you which "group" (battery bank) a specific sensor is monitoring.
    In a battery system, sensors are organized by banks (series groups). This helps analyze temperatures per bank.
    For example, if you have 3 banks with 8 sensors each, it figures out if channel 5 is in bank 1 or 2.
    Non-programmer analogy: Like finding which floor an apartment number belongs to in a building.
    
    Args:
        ch (int): Sensor channel number (1 to total_channels) - the sensor ID, starting from 1.
    
    Returns:
        int: Bank number (1 to num_series_banks) or None if the channel is invalid or out of range.
    """
    # Loop through each bank (starting from bank 1).
    # enumerate(BANK_SENSOR_INDICES, 1) gives bank_id (1,2,3...) and its list of sensor indices (0-based).
    for bank_id, indices in enumerate(BANK_SENSOR_INDICES, 1):
        # Check if the 0-based version of ch (ch-1) is in this bank's sensor list.
        if ch - 1 in indices:
            return bank_id  # Found it—return the bank number.
    # If not found in any bank, it's invalid.
    return None

def get_battery_and_local_ch(ch, num_series_banks=None, sensors_per_bank=None):
    """
    Find the parallel battery ID and local channel for a global channel.
    This function breaks down a global sensor ID into which parallel battery it's on and its local position within that battery.
    Batteries can be in parallel (multiple identical packs), each with their own sensors. Global channels are numbered across all.
    For example, with 4 parallel batteries and 24 sensors each, channel 25 would be battery 2, local channel 1.
    Non-programmer analogy: Like converting a full address (street number) to building number and room number.
    
    Args:
        ch (int): Global channel (1 to total_channels) - global ID, starting from 1.
        num_series_banks (int): Number of series banks (from config).
        sensors_per_bank (int): Sensors per bank (from config).
    
    Returns:
        tuple: (battery_id, local_ch) - battery number (1+), local channel (1 to sensors_per_battery).
    """
    # Calculate sensors_per_battery from config values
    if num_series_banks is None:
        num_series_banks = 3  # fallback
    if sensors_per_bank is None:
        sensors_per_bank = 8  # fallback
    sensors_per_battery = num_series_banks * sensors_per_bank
    # Calculate which battery: Divide global index (0-based) by sensors per battery, add 1 for 1-based.
    bat_id = ((ch - 1) // sensors_per_battery) + 1
    # Local channel: Remainder of division, add 1 for 1-based.
    local_ch = ((ch - 1) % sensors_per_battery) + 1
    # Return as a pair (tuple).
    return bat_id, local_ch

def test_network_connectivity(ip, port, timeout=2):
    """
    Test network connectivity to a Modbus device.
    
    Args:
        ip: IP address to check
        port: TCP port to check
        timeout: Connection timeout in seconds
    
    Returns:
        tuple: (reachable: bool, error_type: str or None)
               reachable=True means network is up
               reachable=False with error_type indicates the type of failure
    """
    import socket
    
    # First, try TCP connection (faster than ping)
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((ip, port))
        s.close()
        s = None
        return True, None  # Network is up
    except socket.error as e:
        if s:
            try: s.close()
            except: pass
        error_code = e.args[0] if e.args else None
        error_str = str(e)
        
        if error_code == 111:  # Connection refused
            return False, "connection_refused"
        elif error_code == 113:  # No route to host
            return False, "no_route"
        elif error_code == 110:  # Timed out
            return False, "timeout"
        elif "Connection refused" in error_str:
            return False, "connection_refused"
        elif "No route to host" in error_str or "Network is unreachable" in error_str:
            return False, "no_route"
        elif "Timed out" in error_str:
            return False, "timeout"
        else:
            # Other socket error - log the actual error
            logging.warning(f"Socket error checking {ip}:{port}: {error_str} (code={error_code})")
            return False, f"socket_error_{error_code}"

def modbus_crc(data):
    """
    Calculate a checksum (CRC) to ensure data integrity for Modbus communication.
    Modbus is a protocol for talking to industrial devices like temperature sensors. CRC is like a fingerprint
    that verifies the message wasn't garbled during transmission (e.g., by electrical noise on wires).
    This function computes the CRC-16 checksum using the Modbus polynomial (0xA001), which is standard for error checking.
    Non-programmer analogy: Like double-checking a phone number by repeating it—ensures no digits were misheard.

    Args:
        data (bytes): Data to calculate the CRC for - the message bytes to checksum.

    Returns:
        bytes: 2-byte CRC value in little-endian order - the check code appended to messages.
    """
    # Start with initial CRC value of 0xFFFF (standard for Modbus).
    crc = 0xFFFF
    # Process each byte in the data.
    for byte in data:
        # XOR the current CRC with the byte (combines them bitwise).
        crc ^= byte
        # For 8 bits in the byte, shift and possibly XOR with polynomial.
        for _ in range(8):
            # If least significant bit is 1, shift right and XOR with 0xA001 (Modbus poly).
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                # Just shift right if LSB is 0.
                crc >>= 1
    # Convert the 16-bit CRC to 2 bytes, little-endian (low byte first).
    return crc.to_bytes(2, 'little')

_cerbo_dc_cache = {'v': None, 'ts': 0.0}   # module-level cache for Cerbo DC voltage

def read_cerbo_dc_voltage(cerbo_ip, cache_ttl=5.0):
    """Read Cerbo GX VE.Bus DC bus voltage via raw Modbus TCP (unit 227, reg 26, scale 0.01V).

    This is the MultiPlus/Quattro's own measurement of its DC terminals — independent of
    any voltage we report back to the Cerbo.  Used to calculate the true cable-drop between
    the charger and the battery terminals measured by the BMS.

    Returns voltage in volts, or None if the Cerbo is unreachable / returns an error.
    Results are cached for cache_ttl seconds to avoid hammering the Cerbo every poll cycle.
    """
    global _cerbo_dc_cache
    now = time.time()
    if now - _cerbo_dc_cache['ts'] < cache_ttl:
        return _cerbo_dc_cache['v']  # negative caching: return None within TTL if last attempt failed
    try:
        req = struct.pack(">HHHBBHH", 1, 0, 6, 227, 3, 26, 1)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        s.connect((cerbo_ip, 502))
        s.sendall(req)
        resp = b""
        deadline = time.time() + 0.5
        while len(resp) < 11 and time.time() < deadline:
            chunk = s.recv(64)
            if not chunk:
                break
            resp += chunk
        s.close()
        if len(resp) >= 11 and not (resp[7] & 0x80):
            volts = struct.unpack(">H", resp[9:11])[0] * 0.01
            if 20.0 <= volts <= 100.0:  # reject 0xFFFF "not available" and other nonsense values
                _cerbo_dc_cache['v'] = volts
                _cerbo_dc_cache['ts'] = now
                return volts
    except Exception:
        pass
    _cerbo_dc_cache['v'] = None  # negative cache: failed attempt, wait cache_ttl before retry
    _cerbo_dc_cache['ts'] = now
    return None


def test_modbus_connectivity(ip, port):
    """
    Test network connectivity to the Modbus device.
    Attempts a socket connection with a short timeout to check if the device is reachable.
    Non-programmer analogy: Like knocking on a door to see if someone is home.

    Args:
        ip (str): IP address of the Modbus device.
        port (int): Port number.

    Returns:
        bool: True if connection succeeds, False otherwise.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)  # 1 second timeout
        s.connect((ip, port))
        s.close()
        return True
    except socket.error:
        return False

def read_ntc_sensors(ip, modbus_port, query_delay, num_channels, scaling_factor, max_retries, retry_backoff_base, slave_addr=1, slave_ports=None, slave_addresses=None, slave_ips=None):
    """
    Read temperature measurements from NTC thermistor sensors.
    Improved for 9600 baud half-duplex Modbus communication.
    
    Key improvements for reliable 9600 half-duplex:
    - Increased query_delay to allow device processing time at slow baud rate
    - Progressive receive timeout to detect end of response frame
    - Response validation with length and CRC checks
    - Better error handling with fallback to cached values
    
    Enhanced error handling features:
    - Uses last known good value when errors occur (if within reasonable range)
    - Only reports error after 5 consecutive failures per channel
    - Tracks consecutive failures for each sensor channel
    
    Args:
        ip (str): The IP address of the Modbus device.
        modbus_port (int): The Modbus TCP port (default, overridden by slave_ports if provided).
        query_delay (float): Delay after sending query (in seconds).
        num_channels (int): Number of temperature sensors to read.
        scaling_factor (float): Factor to convert raw to Celsius.
        max_retries (int): Number of retry attempts on failure.
        retry_backoff_base (int): Base for exponential backoff.
        slave_addr (int): Modbus slave address (default 1).
        slave_ports (list): List of per-slave ports (optional).
        slave_addresses (list): List of slave addresses for port mapping (optional).
    
    Returns:
        list or str: List of temperatures or error message string.
    """
    # Handle per-slave IP and port selection
    if slave_ips and slave_addresses:
        effective_ip = get_ip_for_slave(slave_addr, slave_addresses, slave_ips, ip)
    else:
        effective_ip = ip
    
    if slave_ports and slave_addresses:
        effective_port = get_port_for_slave(slave_addr, slave_addresses, slave_ports, modbus_port)
    else:
        effective_port = modbus_port
    
    # Log start of read.
    logging.info(f"Starting temp read for slave {slave_addr}.")
    
    # Build Modbus query packet: Slave addr + function code 3 + start addr + num registers.
    query_base = bytes([slave_addr, 3]) + (0).to_bytes(2, 'big') + (num_channels).to_bytes(2, 'big')
    crc = modbus_crc(query_base)
    query = query_base + crc
    
    # Calculate expected response length: 3 header bytes + byte_count (2 per channel) + 2 CRC
    expected_data_length = num_channels * 2
    expected_response_length = 3 + expected_data_length + 2
    
    network_retry_count = 0
    attempt = 0
    
    while attempt < max_retries:  # Use configured max_retries
        # First, check network connectivity
        network_ok, network_error = test_network_connectivity(effective_ip, effective_port)
        if not network_ok:
            network_retry_count += 1
            update_comm_stats(slave_addr, False, error_type=ModbusError.CONNECTION_REFUSED)
            if network_retry_count < 10:
                logging.warning(f"Network check failed for slave {slave_addr}: {network_error}, retry {network_retry_count}/10")
                time.sleep(5)  # Longer delay for network issues
                attempt += 1
                continue
            else:
                logging.error(f"Network still down after 10 retries for slave {slave_addr}")
                return f"Error: Network unreachable for slave {slave_addr}"
        
        s = None  # Initialize socket for cleanup in except blocks
        try:
            logging.debug(f"Temp read attempt {attempt+1} for slave {slave_addr}: {effective_ip}:{effective_port}")
            
            # Create socket with proper timeout for 9600 baud
            # At 9600 baud, 1 char takes ~1ms, so we need longer timeouts
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(5)  # 5 second timeout for slow devices
            
            # Connect
            s.connect((effective_ip, effective_port))
            
            # Drain any stale data the gateway may have buffered from a
            # previous slave response before sending this query.
            s.settimeout(0.05)
            try:
                while s.recv(256):
                    pass
            except (socket.timeout, OSError):
                pass
            s.settimeout(5)
            
            # Send query
            s.send(query)
            
            # For 9600 half-duplex: Wait longer for device to process
            # At 9600 baud, a ~19 byte request takes ~20ms to transmit
            # Plus device processing time (typically 50-100ms for RS485 turn-around)
            time.sleep(query_delay)
            
            # Read response with progressive timeout
            # For half-duplex, we need to wait for the complete frame
            response = b''
            chunk = s.recv(256)
            response += chunk
            
            # Progressive read: wait for more data if response is incomplete
            # This handles slow response times on 9600 baud
            max_wait_time = 2.0  # Max 2 seconds for full response
            wait_start = time.time()
            while len(response) < expected_response_length and (time.time() - wait_start) < max_wait_time:
                time.sleep(0.1)  # Short sleep between checks
                chunk = s.recv(256)
                if chunk:
                    response += chunk
                else:
                    break
            
            s.close()
            
            # Validate response length
            if len(response) < 5:
                logging.warning(f"Short response from slave {slave_addr}: {len(response)} bytes (expected {expected_response_length})")
                raise ValueError(f"Short response: {len(response)} bytes")
            
            # Validate response length matches expected
            if len(response) != expected_response_length:
                logging.warning(f"Response length mismatch for slave {slave_addr}: got {len(response)}, expected {expected_response_length}")
                if len(response) > expected_response_length:
                    # Shared RS485-TCP gateway sent extra bytes (next slave's response
                    # leaked into this read). Valid data is always the FIRST N bytes.
                    candidate = response[:expected_response_length]
                    if modbus_crc(candidate[:-2]) == candidate[-2:]:
                        logging.debug(f"Trimmed {len(response) - expected_response_length} trailing bytes from slave {slave_addr} response")
                        response = candidate
            
            # Validate CRC
            calc_crc = modbus_crc(response[:-2])
            if calc_crc != response[-2:]:
                logging.warning(f"CRC mismatch for slave {slave_addr}")
                raise ValueError("CRC mismatch")
            
            # Validate header
            slave, func, byte_count = response[0:3]
            if slave != slave_addr:
                logging.warning(f"Slave address mismatch for slave {slave_addr}: got {slave}")
                raise ValueError("Slave address mismatch")
            
            if func != 3:
                if func & 0x80:
                    update_comm_stats(slave_addr, False, error_type=ModbusError.ILLEGAL_FUNCTION)
                    return f"Error: Modbus exception code {response[2]} for slave {slave_addr}"
                logging.warning(f"Invalid function code for slave {slave_addr}: {func}")
                raise ValueError("Invalid function code")
            
            if byte_count != expected_data_length:
                logging.warning(f"Byte count mismatch for slave {slave_addr}: got {byte_count}, expected {expected_data_length}")
                raise ValueError("Byte count mismatch")
            
            # Extract temperature data (2 bytes per channel, big-endian signed)
            data = response[3:3 + byte_count]
            raw_temperatures = []
            for i in range(0, len(data), 2):
                val = int.from_bytes(data[i:i+2], 'big', signed=True) / scaling_factor
                raw_temperatures.append(val)
            
            logging.info(f"Temp read successful for slave {slave_addr}: {len(raw_temperatures)} values")
            update_comm_stats(slave_addr, True)
            update_temp_cache(slave_addr, raw_temperatures)
            return raw_temperatures
            
        except socket.error as e:
            if s:
                try: s.close()
                except: pass
            logging.warning(f"Temp read attempt {attempt+1} for slave {slave_addr} failed: {str(e)}")
            time.sleep(3)
            
            # Socket error even after network check - might be Modbus protocol issue
            attempt += 1
            if attempt < 10:
                time.sleep(1)  # Shorter delay for protocol issues
                continue
            else:
                logging.error(f"Temp read failed after {attempt} attempts for slave {slave_addr}")
                update_comm_stats(slave_addr, False, error_type=ModbusError.UNKNOWN)
                return f"Error: Failed after {attempt} attempts for slave {slave_addr}"
                    
        except ValueError as e:
            if s:
                try: s.close()
                except: pass
            logging.warning(f"Temp read validation failed for slave {slave_addr}: {str(e)}")
            time.sleep(3)
            
            # Validation error - might be Modbus protocol issue
            attempt += 1
            if attempt < 10:
                time.sleep(1)  # Shorter delay for protocol issues
                continue
            else:
                logging.error(f"Temp read failed after {attempt} attempts for slave {slave_addr}")
                update_comm_stats(slave_addr, False, error_type=ModbusError.UNKNOWN)
                return f"Error: Failed after {attempt} attempts for slave {slave_addr}"
                    
        except Exception as e:
            if s:
                try: s.close()
                except: pass
            logging.warning(f"Temp read unexpected error for slave {slave_addr}: {str(e)}")
            attempt += 1
            if attempt < 10:
                time.sleep(1)
                continue
            else:
                logging.error(f"Temp read failed after {attempt} attempts for slave {slave_addr}: {str(e)}")
                update_comm_stats(slave_addr, False, error_type=ModbusError.UNKNOWN)
                return f"Error: Failed after {attempt} attempts for slave {slave_addr}"
    
    # All retries exhausted - use cached values with enhanced error handling
    logging.warning(f"All retries exhausted for slave {slave_addr}, using cached values")
    temperatures = []
    errors = []
    
    for ch in range(num_channels):
        cache_key = f"{slave_addr}:{ch+1}"
        last_good = last_good_temps.get(cache_key)
        
        if last_good is not None and REASONABLE_TEMP_MIN <= last_good <= REASONABLE_TEMP_MAX:
            temperatures.append(last_good)
            consecutive_failures[cache_key] = consecutive_failures.get(cache_key, 0) + 1
            failures = consecutive_failures[cache_key]
            
            if failures >= CONSECUTIVE_FAILURE_THRESHOLD:
                if not errors:
                    errors.append(f"Error: Slave {slave_addr} had {CONSECUTIVE_FAILURE_THRESHOLD} consecutive failures")
                logging.error(f"Sensor {ch+1} on slave {slave_addr}: {CONSECUTIVE_FAILURE_THRESHOLD} consecutive failures, using cached value")
        else:
            temperatures.append(None)
            consecutive_failures[cache_key] = 0
            if cache_key not in errors:
                errors.append(f"Error: No valid cached value for sensor {ch+1} on slave {slave_addr}")
    
    if errors:
        logging.error(f"Temp read errors for slave {slave_addr}: {len(errors)} sensors failed")
    
    return temperatures



def update_temp_cache(slave_addr, temperatures):
    """
    Update the cache of last known good temperatures.
    Called after successful sensor reads to store valid values.
    
    Args:
        slave_addr (int): The Modbus slave address.
        temperatures (list): List of temperature values from sensors.
    """
    for ch, temp in enumerate(temperatures):
        cache_key = f"{slave_addr}:{ch+1}"
        if temp is not None and REASONABLE_TEMP_MIN <= temp <= REASONABLE_TEMP_MAX:
            last_good_temps[cache_key] = temp
            consecutive_failures[cache_key] = 0  # Reset failure count on success

def weighted_average(trend):
    """
    Calculate weighted average of voltage trend readings.
    Uses exponential decay weighting to emphasize recent readings.
    This smooths out DC-DC converter pulsing/inrush current variations.
    
    Args:
        trend (list): List of voltage readings over time.
    
    Returns:
        float: Weighted average voltage, or None if trend is empty.
    """
    if not trend:
        return None
    if len(trend) == 1:
        return trend[0]
    # Exponential weighting: recent values weighted more heavily
    # Using decay factor that gives ~70% weight to last 50% of readings
    decay = 0.5  # Controls how much recent readings are emphasized
    weights = []
    for i in range(len(trend)):
        # Weight increases towards the end of the list
        weight = decay ** (len(trend) - 1 - i)
        weights.append(weight)
    total_weight = sum(weights)
    weighted_sum = sum(v * w for v, w in zip(trend, weights))
    return weighted_sum / total_weight


def load_config(data_dir):
    """
    Load and parse the configuration from the 'battery_monitor.ini' file.
    This function reads the settings file (like a customizable recipe) and extracts all parameters into a dictionary.
    It provides defaults for missing values to ensure the script always has something to use. Sections like [Temp], [General]
    organize settings (e.g., sensor IPs, thresholds). It also computes derived values like total channels and loads/saves
    calibration offsets. For non-programmers: Think of it as reading a form filled with your preferences and filling in blanks
    with safe defaults if something's missing. Validates and logs the loaded config.
    
    Args:
        data_dir (str): Directory path where the INI file and data files (like offsets.txt) are located.
    
    Returns:
        dict: A comprehensive dictionary with all settings, including computed values like total_channels and relay_mapping.
    """
    # Log the start of config loading.
    logging.info("Loading configuration from 'battery_monitor.ini'.")
    # Global: Reset alert states dictionary.
    global alert_states
    # Check if config has been read; if empty sections, file is missing or invalid.
    if not config_parser.sections():
        logging.error("Config file 'battery_monitor.ini' not found or empty.")
        raise FileNotFoundError("Config file 'battery_monitor.ini' not found.")
    # Temperature settings section: Extract with fallbacks (defaults if key missing).
    temp_settings = {
        'ip': config_parser.get('Temp', 'ip', fallback='192.168.15.240'),  # Device IP address.
        'modbus_port': config_parser.getint('Temp', 'modbus_port', fallback=10001),  # Modbus TCP port.
        'poll_interval': config_parser.getfloat('Temp', 'poll_interval', fallback=10.0),  # Seconds between checks.
        'rise_threshold': config_parser.getfloat('Temp', 'rise_threshold', fallback=2.0),  # Max temp rise per poll.
        'deviation_threshold': config_parser.getfloat('Temp', 'deviation_threshold', fallback=0.1),  # Relative deviation %.
        'disconnection_lag_threshold': config_parser.getfloat('Temp', 'disconnection_lag_threshold', fallback=0.5),  # Lag from group change.
        'high_threshold': config_parser.getfloat('Temp', 'high_threshold', fallback=42.0),  # Max safe temp °C.
        'low_threshold': config_parser.getfloat('Temp', 'low_threshold', fallback=0.0),  # Min safe temp °C.
        'scaling_factor': config_parser.getfloat('Temp', 'scaling_factor', fallback=100.0),  # Raw to °C conversion.
        'valid_min': config_parser.getfloat('Temp', 'valid_min', fallback=0.0),  # Minimum valid reading (below = disconnected).
        'heating_threshold': config_parser.getfloat('Temp', 'heating_threshold', fallback=10.0),  # Temp below which balancer runs to generate heat.
        'max_retries': config_parser.getint('Temp', 'max_retries', fallback=3),  # Read retries.
        'retry_backoff_base': config_parser.getint('Temp', 'retry_backoff_base', fallback=1),  # Backoff multiplier.
        'query_delay': config_parser.getfloat('Temp', 'query_delay', fallback=0.25),  # Modbus response wait.
        'abs_deviation_threshold': config_parser.getfloat('Temp', 'abs_deviation_threshold', fallback=2.0),  # Absolute deviation °C.
        'cabinet_over_temp_threshold': config_parser.getfloat('Temp', 'cabinet_over_temp_threshold', fallback=35.0),  # Fan trigger temp.
        'number_of_parallel_batteries': config_parser.getint('Temp', 'number_of_parallel_batteries', fallback=1),  # Number of parallel packs.
        'modbus_slave_addresses': [int(x.strip()) for x in config_parser.get('Temp', 'modbus_slave_addresses', fallback='1').split(',')],  # List of slave IDs.
        'sensors_per_bank': config_parser.getint('Temp', 'sensors_per_bank', fallback=8), # New: sensors per bank per battery.
        'num_series_banks': config_parser.getint('General', 'num_series_banks', fallback=3) # New: number of series banks.
    }
    # Temperature sanity bounds
    temp_settings['reasonable_temp_min'] = config_parser.getfloat('Temp', 'reasonable_temp_min', fallback=-10.0)
    temp_settings['reasonable_temp_max'] = config_parser.getfloat('Temp', 'reasonable_temp_max', fallback=60.0)
    temp_settings['consecutive_failure_threshold'] = config_parser.getint('Temp', 'consecutive_failure_threshold', fallback=5)
    # Parse modbus_slave_ports for per-slave port configuration
    # This allows each slave to use a different Modbus port (e.g., slaves 1-4 on 10003, 5-8 on 10001)
    modbus_slave_ports_str = config_parser.get('Temp', 'modbus_slave_ports', fallback='')
    if modbus_slave_ports_str:
        temp_settings['modbus_slave_ports'] = [int(x.strip()) for x in modbus_slave_ports_str.split(',')]
    else:
        # Default to modbus_port for all slaves
        temp_settings['modbus_slave_ports'] = [temp_settings['modbus_port']] * len(temp_settings['modbus_slave_addresses'])
    # Log configuration for debugging
    # Parse modbus_slave_ips for per-slave IP configuration
    modbus_slave_ips_str = config_parser.get('Temp', 'modbus_slave_ips', fallback='')
    if modbus_slave_ips_str:
        temp_settings['modbus_slave_ips'] = [x.strip() for x in modbus_slave_ips_str.split(',')]
    else:
        # Default to ip for all slaves
        temp_settings['modbus_slave_ips'] = [temp_settings['ip']] * len(temp_settings['modbus_slave_addresses'])
    logging.info(f"modbus_slave_ips configured: {temp_settings['modbus_slave_ips']}")
    logging.info(f"modbus_slave_ports configured: {temp_settings['modbus_slave_ports']}")
    logging.info(f"modbus_slave_addresses: {temp_settings['modbus_slave_addresses']}")
    # Validate num_series_banks: Ensure it's reasonable (1-20).
    if temp_settings['num_series_banks'] < 1:
        logging.warning(f"num_series_banks={temp_settings['num_series_banks']} invalid. Setting to 1.")
        temp_settings['num_series_banks'] = 1
    elif temp_settings['num_series_banks'] > 20:
        logging.warning(f"num_series_banks={temp_settings['num_series_banks']} very high. Ensure hardware supports this.")
    # Compute derived: Sensors per battery = series banks * sensors per bank.
    temp_settings['sensors_per_battery'] = temp_settings['num_series_banks'] * temp_settings['sensors_per_bank'] # Calc per battery.
    # Total sensors across all parallel batteries.
    temp_settings['total_channels'] = temp_settings['number_of_parallel_batteries'] * temp_settings['sensors_per_battery'] # Total sensors.
    # Load existing calibration median and offsets from file.
    startup_median, startup_offsets = load_offsets(temp_settings['total_channels'], data_dir)
    # Voltage and general settings from [General] section.
    voltage_settings = {
        'VoltageDifferenceToBalance': config_parser.getfloat('General', 'VoltageDifferenceToBalance', fallback=0.1),  # Min diff to trigger balance V.
        'BalanceDurationSeconds': config_parser.getint('General', 'BalanceDurationSeconds', fallback=5),  # How long to balance s.
        'SleepTimeBetweenChecks': config_parser.getfloat('General', 'SleepTimeBetweenChecks', fallback=0.1),  # Delay between voltage reads.
        'BalanceRestPeriodSeconds': config_parser.getint('General', 'BalanceRestPeriodSeconds', fallback=60),  # Cooldown after balance s.
        'LowVoltageThresholdPerBattery': config_parser.getfloat('General', 'LowVoltageThresholdPerBattery', fallback=18.5),  # Low V alert per bank.
        'HighVoltageThresholdPerBattery': config_parser.getfloat('General', 'HighVoltageThresholdPerBattery', fallback=21.0),  # High V alert per bank.
        'EmailAlertIntervalSeconds': config_parser.getint('General', 'EmailAlertIntervalSeconds', fallback=3600),  # Min time between emails s.
        'I2C_BusNumber': config_parser.getint('General', 'I2C_BusNumber', fallback=1),  # I2C bus ID on Pi.
        'VoltageDividerRatio': config_parser.getfloat('General', 'VoltageDividerRatio', fallback=0.01592),  # ADC voltage scaling.
        'LoggingLevel': config_parser.get('General', 'LoggingLevel', fallback='INFO')  # Log verbosity (INFO, DEBUG, etc.).
    }
    # Boolean flags for features.
    general_flags = {
        'WebInterfaceEnabled': config_parser.getboolean('General', 'WebInterfaceEnabled', fallback=True),  # Enable web dashboard.
        'StartupSelfTestEnabled': config_parser.getboolean('General', 'StartupSelfTestEnabled', fallback=True),  # Run startup checks.
        'WatchdogEnabled': config_parser.getboolean('General', 'WatchdogEnabled', fallback=True),  # Use hardware watchdog.
        'EventLogSize': config_parser.getint('General', 'EventLogSize', fallback=20)  # Max events to keep in memory.
    }
    # I2C device addresses (hex).
    i2c_settings = {
        'MultiplexerAddress': int(config_parser.get('I2C', 'MultiplexerAddress', fallback='0x70'), 16),  # TCA9548A mux addr.
        'VoltageMeterAddress': int(config_parser.get('I2C', 'VoltageMeterAddress', fallback='0x49'), 16),  # ADS1115 ADC addr.
    }
    # GPIO pin assignments.
    gpio_settings = {
        'DC_DC_RelayPin': config_parser.getint('GPIO', 'DC_DC_RelayPin', fallback=5),  # Pin for DC-DC converter.
        'AlarmRelayPin': config_parser.getint('GPIO', 'AlarmRelayPin', fallback=6),  # Pin for alarm buzzer/light.
        'FanRelayPin': config_parser.getint('GPIO', 'FanRelayPin', fallback=4)  # Pin for cooling fan.
    }
    # Email SMTP settings.
    email_settings = {
        'SMTP_Server': config_parser.get('Email', 'SMTP_Server', fallback='smtp.gmail.com'),  # Mail server.
        'SMTP_Port': config_parser.getint('Email', 'SMTP_Port', fallback=587),  # Port (587 for TLS).
        'SenderEmail': config_parser.get('Email', 'SenderEmail', fallback='your_email@gmail.com'),  # From address.
        'RecipientEmail': config_parser.get('Email', 'RecipientEmail', fallback='recipient@example.com'),  # To address.
        'SMTP_Username': config_parser.get('Email', 'SMTP_Username', fallback='your_email@gmail.com'),  # Login user.
        'SMTP_Password': config_parser.get('Email', 'SMTP_Password', fallback='your_app_password')  # App password.
    }
    # ADC configuration registers (hex values).
    adc_settings = {
        'ConfigRegister': int(config_parser.get('ADC', 'ConfigRegister', fallback='0x01'), 16),  # Config reg addr.
        'ConversionRegister': int(config_parser.get('ADC', 'ConversionRegister', fallback='0x00'), 16),  # Conversion reg addr.
        'ContinuousModeConfig': int(config_parser.get('ADC', 'ContinuousModeConfig', fallback='0x0100'), 16),  # Mode bits.
        'SampleRateConfig': int(config_parser.get('ADC', 'SampleRateConfig', fallback='0x0080'), 16),  # Rate bits.
        'GainConfig': int(config_parser.get('ADC', 'GainConfig', fallback='0x0400'), 16)  # Gain bits.
    }
    # Per-bank calibration factors (multipliers for voltage accuracy).
    calibration_settings = {}
    for i in range(1, temp_settings['num_series_banks'] + 1):
        key = f'Sensor{i}_Calibration'
        calibration_settings[key] = config_parser.getfloat('Calibration', key, fallback=1.0)
    # Startup test parameters.
    startup_settings = {
        'test_balance_duration': config_parser.getint('Startup', 'test_balance_duration', fallback=15),  # Test balance time s.
        'min_voltage_delta': config_parser.getfloat('Startup', 'min_voltage_delta', fallback=0.01),  # Min change to verify V.
        'test_read_interval': config_parser.getfloat('Startup', 'test_read_interval', fallback=2.0),  # Read freq during test s.
        'min_balance_source_voltage': config_parser.getfloat('Startup', 'min_balance_source_voltage', fallback=17.0)  # Min source V for DC-DC.
    }
    # Web server settings.
    web_settings = {
        'host': config_parser.get('Web', 'host', fallback='0.0.0.0'),  # Bind address (0.0.0.0 = all interfaces).
        'web_port': config_parser.getint('Web', 'web_port', fallback=8080),  # Port for web access.
        'auth_required': config_parser.getboolean('Web', 'auth_required', fallback=False),  # Enable login.
        'username': config_parser.get('Web', 'username', fallback='admin'),  # Web login user.
        'password': config_parser.get('Web', 'password', fallback='admin123'),  # Web login pass.
        'api_enabled': config_parser.getboolean('Web', 'api_enabled', fallback=True),  # Enable API endpoints.
        'cors_enabled': config_parser.getboolean('Web', 'cors_enabled', fallback=False),  # Enable CORS for web.
        'cors_origins': config_parser.get('Web', 'cors_origins', fallback='*')  # Allowed origins.
    }
    # Modbus TCP server settings for Victron Cerbo GX integration.
    modbus_server_settings = {
        'enabled': config_parser.getboolean('ModbusServer', 'enabled', fallback=True),  # Enable Modbus TCP server.
        'port': config_parser.getint('ModbusServer', 'port', fallback=5020),  # Port for Modbus TCP (5020 to avoid root requirement).
        'unit_id': config_parser.getint('ModbusServer', 'unit_id', fallback=1),  # Modbus unit/slave ID.
        'update_interval': config_parser.getfloat('ModbusServer', 'update_interval', fallback=1.0)  # Register update interval.
    }
    # DVCC (Distributed Voltage and Current Control) limits for Victron Cerbo GX.
    dvcc_settings = {
        'dvcc_max_charge_voltage': config_parser.getfloat('DVCC', 'max_charge_voltage', fallback=61.0),  # Max charge voltage (V).
        'dvcc_max_charge_current': config_parser.getfloat('DVCC', 'max_charge_current', fallback=200.0),  # Max charge current (A).
        'dvcc_max_discharge_current': config_parser.getfloat('DVCC', 'max_discharge_current', fallback=200.0),  # Max discharge current (A).
        'dvcc_min_discharge_voltage': config_parser.getfloat('DVCC', 'min_discharge_voltage', fallback=49.5),  # Min discharge voltage (V).
        'cable_drop_compensation': 0.0,  # Always start at 0; auto-relearned each session
        'discharge_cable_drop': config_parser.getfloat('DVCC', 'discharge_cable_drop', fallback=0.0),
        'temp_derate_start': config_parser.getfloat('DVCC', 'temp_derate_start', fallback=38.0),
        'temp_derate_end': config_parser.getfloat('DVCC', 'temp_derate_end', fallback=45.0),
        'cold_charge_cutoff': config_parser.getfloat('DVCC', 'cold_charge_cutoff', fallback=5.0),
        'cold_charge_min': config_parser.getfloat('DVCC', 'cold_charge_min', fallback=0.0),
    }
    # Relay mapping for balancing pairs (e.g., bank1-bank2 uses certain relay bits).
    relay_mapping = {}
    if config_parser.has_section('RelayMapping'):
        # Parse each key-value in section: Key like '1-2' maps to list of relay numbers.
        for key in config_parser['RelayMapping']:
            try:
                relays = [int(x.strip()) for x in config_parser['RelayMapping'][key].split(',')]  # Split comma-separated ints.
                relay_mapping[key] = relays
            except ValueError:
                logging.warning(f"Invalid relay mapping for {key}: {config_parser['RelayMapping'][key]}")  # Log bad format.
    # Set global logging level based on config (e.g., INFO shows normal events, DEBUG shows everything).
    log_level = getattr(logging, voltage_settings['LoggingLevel'].upper(), logging.INFO)
    logging.getLogger().setLevel(log_level)
    # Initialize alert states for each channel: Track last alert type and count to avoid spam.
    alert_states = {ch: {'last_type': None, 'count': 0} for ch in range(1, temp_settings['total_channels'] + 1)}
    # Log success.
    logging.info("Configuration loaded successfully.")
    # Combine all settings into one big dictionary.
    relay_pins = {
        f'Relay{i}_Pin': config_parser.getint('GPIO', f'Relay{i}_Pin', fallback=[17,18,27,22][i]) for i in range(4)
    }
    # Cerbo GX SSH control settings.
    cerbo_settings = {
        'cerbo_ip': config_parser.get('CerboGX', 'ip', fallback='192.168.15.67'),
        'cerbo_pass': config_parser.get('CerboGX', 'password', fallback='555555'),
        'cerbo_ssh_timeout': config_parser.getint('CerboGX', 'ssh_timeout', fallback=8),
    }
    return {**temp_settings, **voltage_settings, **general_flags, **i2c_settings,
            **gpio_settings, **email_settings, **adc_settings, **calibration_settings,
            **startup_settings, **web_settings, **modbus_server_settings, **dvcc_settings, **cerbo_settings, 'relay_mapping': relay_mapping, **relay_pins}

def validate_config(settings):
    """
    Validate configuration settings for consistency and required values.
    This function double-checks the loaded settings for sanity: Ensures numbers are positive, counts match (e.g., slave addresses = parallel batteries),
    and required mappings exist (e.g., relays for every bank pair). If issues found, raises an error to stop the script early.
    Non-programmer analogy: Like proofreading a form for typos or missing info before submitting—catches problems before they cause failures later.
    
    Args:
        settings (dict): The loaded configuration dictionary to validate.
    
    Raises:
        ValueError: If any validation fails, with a message listing all errors.
    
    Returns:
        None
    """
    # List to collect error messages.
    errors = []
   
    # Check num_series_banks is at least 1 (can't have 0 banks).
    if settings['num_series_banks'] < 1:
        errors.append("num_series_banks must be at least 1.")
    # Warn if too many banks (hardware limit).
    if settings['num_series_banks'] > 20:
        errors.append("num_series_banks > 20 may cause issues.")
   
    # Sensors per bank must be positive.
    if settings['sensors_per_bank'] < 1:
        errors.append("sensors_per_bank must be at least 1.")
   
    # Parallel batteries must be at least 1.
    if settings['number_of_parallel_batteries'] < 1:
        errors.append("number_of_parallel_batteries must be at least 1.")
   
    # Number of slave addresses must match parallel batteries (one slave per battery).
    if len(settings['modbus_slave_addresses']) != settings['number_of_parallel_batteries']:
        errors.append("modbus_slave_addresses count must match number_of_parallel_batteries.")
   
    # For relay mapping, ensure every possible pair (high-low) has a mapping.
    if settings.get('relay_mapping'):
        expected_pairs = []
        for i in range(1, settings['num_series_banks'] + 1):
            for j in range(1, settings['num_series_banks'] + 1):
                if i != j:  # No self-balancing.
                    expected_pairs.append(f"{i}-{j}")
        for pair in expected_pairs:
            if pair not in settings['relay_mapping']:
                errors.append(f"Relay mapping missing for {pair}.")
   
    # If errors found, log them and raise exception with combined message.
    if errors:
        msg = "Configuration errors: " + "; ".join(errors)
        logging.error(msg)
        raise ValueError(msg)
   
    # All good—log success.
    logging.info("Configuration validation passed.")

def detect_hardware(settings):
    """
    Detect and log hardware connectivity at startup.
    This function pings the connected devices to see if they're responding, like knocking on doors to check if rooms are accessible.
    It tests I2C devices (voltage meter, relays, multiplexer) by trying to read a byte, and Modbus slaves by a simple query.
    Logs OK or warnings for each—helps diagnose wiring/network issues early. Non-programmer: Like a system scan in your computer
    to see if peripherals (printer, mouse) are plugged in right.
    
    Args:
        settings (dict): Configuration with addresses, IPs, etc., for testing.
    
    Returns:
        None: Just logs results.
    """
    # Log start of detection.
    logging.info("Detecting hardware connectivity.")
    # If I2C bus is available, test each device.
    if bus:
        try:
            # Select channel 0 on multiplexer (default) and read from it.
            choose_channel(0, settings['MultiplexerAddress'])
            logging.info("I2C multiplexer detected.")  # Success.
        except IOError as e:
            logging.warning(f"I2C multiplexer not accessible: {e}")  # Failure log.
       
        try:
            # Try reading a byte from voltage meter address.
            bus.read_byte(settings['VoltageMeterAddress'])
            logging.info("I2C voltage meter detected.")
        except IOError as e:
            logging.warning(f"I2C voltage meter not accessible: {e}")
    else:
        # No I2C—skip, likely test mode.
        logging.warning("I2C bus not available - hardware detection skipped.")
   
    # Test each Modbus slave individually.
    # Test Modbus slaves
    for addr in settings['modbus_slave_addresses']:
        try:
            # Try a minimal read (1 channel) to test connectivity.
            test_result = read_ntc_sensors(settings['ip'], settings['modbus_port'], settings['query_delay'], 1, settings['scaling_factor'], 1, 1, slave_addr=addr, slave_ips=settings.get('modbus_slave_ips', []), slave_addresses=settings.get('modbus_slave_addresses', []))
            if isinstance(test_result, str):
                # If error string, log warning.
                logging.warning(f"Modbus slave {addr} not accessible: {test_result}")
            else:
                # List of values means success.
                logging.info(f"Modbus slave {addr} detected.")
        except Exception as e:
            # Catch any unexpected issues.
            logging.warning(f"Modbus slave {addr} detection failed: {e}")
   
    # Log completion.
    logging.info("Hardware detection complete.")

def setup_hardware(settings):
    """
    Prepare the hardware connections for monitoring and controlling the batteries.
    This function sets up the communication channels to the physical devices:
    - I2C bus for talking to voltage sensors and relays (like a data highway)
    - GPIO pins for controlling switches and alarms (like light switches)
    - Time-series database for storing historical data
    If hardware libraries aren't available, it switches to "test mode" where
    everything works but uses fake data instead of real sensors. It also creates or validates the RRD database for logging trends.
    Non-programmer analogy: Like plugging in all cables, turning on switches, and setting up a logbook before starting work.
    
    Args:
        settings (dict): Configuration with bus numbers, pins, addresses, etc.
    
    Returns:
        None
    """
    # Global: Set up I2C bus.
    global bus
    # Log start.
    logging.info("Setting up hardware connections.")
    # Set up I2C communication (for voltage sensors and relays)
    if smbus:
        # Create SMBus object for the specified I2C bus number (usually 1 on Pi).
        bus = smbus.SMBus(settings['I2C_BusNumber']) # Create connection to I2C bus
    else:
        # No library—test mode with simulated data.
        logging.warning("I2C library not available - running in test mode with fake data")
        bus = None
    # Set up GPIO pins (for controlling relays and alarms)
    if GPIO:
        # Disable warnings for GPIO setup (pins may already be configured from previous run)
        GPIO.setwarnings(False)
        # Use BCM pin numbering (GPIO numbers, not physical pins).
        GPIO.setmode(GPIO.BCM) # Use Broadcom pin numbering
        # Set DC-DC relay pin as output, start low (off).
        GPIO.setup(settings['DC_DC_RelayPin'], GPIO.OUT, initial=GPIO.LOW) # DC-DC converter control
        # Alarm pin low (no alarm).
        GPIO.setup(settings['AlarmRelayPin'], GPIO.OUT, initial=GPIO.LOW) # Alarm buzzer/light
        # Fan pin low (off).
        GPIO.setup(settings['FanRelayPin'], GPIO.OUT, initial=GPIO.LOW) # Cooling fan control
        # Set up relay pins
        for i in range(4):
            pin = settings[f'Relay{i}_Pin']
            GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)
            logging.info(f"Relay {i} GPIO pin {pin} set up.")
    else:
        logging.warning("GPIO library not available - running in test mode")
    # Nested function to create RRD database if needed.
    def create_rrd():
        # Build list of data sources (DS): One for median temp, one per bank voltage.
        ds_list = ['DS:medtemp:GAUGE:120:-20:100']  # Median temp: Gauge type (current value), 120s heartbeat, range -20 to 100°C.
        for i in range(1, settings['num_series_banks'] + 1):
            ds_list.append(f'DS:volt{i}:GAUGE:120:0:25')  # Voltage per bank: 0-25V range.
        # Run rrdtool create command: File, step 60s, DS list, Round-Robin Archives (RRA) for storage.
        # RRA: LAST consolidation, 0% XFF (no nulls tolerated), step 1 for 1440 points (~1 day), step 5 for 288 points (longer term).
        subprocess.check_call(['rrdtool', 'create', RRD_FILE,
                               '--step', '60'] + ds_list +
                               ['RRA:LAST:0.0:1:1440',
                                'RRA:LAST:0.0:5:288'])
        logging.info("Created RRD database for time-series logging.")
    # Try to set up RRD: Create if missing, or validate existing.
    try:
        if not os.path.exists(RRD_FILE):
            # No file—create new.
            create_rrd()
        else:
            # File exists—check schema with rrdtool info.
            try:
                output = subprocess.check_output(['rrdtool', 'info', RRD_FILE])
                # Count DS lines in output.
                ds_count = len([line for line in output.decode().split('\n') if line.startswith('ds[') and '.index ' in line])
                # Expected: 1 medtemp + num banks.
                expected_ds = 1 + settings['num_series_banks']
                if ds_count != expected_ds:
                    # Mismatch (e.g., config changed)—back up then recreate.
                    backup = RRD_FILE + '.bak.' + datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
                    os.rename(RRD_FILE, backup)
                    logging.warning(f"RRD database schema mismatch: {ds_count} DS vs expected {expected_ds}. Old database backed up to {backup}. Recreating.")
                    create_rrd()
                else:
                    logging.info("Using existing RRD database with matching schema.")
            except subprocess.CalledProcessError as e:
                # Info command failed—back up then recreate.
                backup = RRD_FILE + '.bak.' + datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
                os.rename(RRD_FILE, backup)
                logging.error(f"RRD info failed: {e}. Old database backed up to {backup}. Recreating.")
                create_rrd()
    except subprocess.CalledProcessError as e:
        logging.error(f"RRD creation failed: {e}")
    except FileNotFoundError:
        logging.error("rrdtool not found. Please install rrdtool (sudo apt install rrdtool).")
    except OSError as e:
        logging.error(f"RRD file operation failed: {e}")
    # Log completion.
    logging.info("Hardware setup complete, including RRD initialization.")
    # Give Modbus slaves time to initialize before detection
    logging.info("Waiting 10 seconds for Modbus slaves to initialize...")
    time.sleep(10)
    # Run detection after setup.
    detect_hardware(settings)

def signal_handler(sig, frame):
    """
    Handle shutdown signals gracefully (e.g., Ctrl+C).
    This function is the "emergency exit" handler. When the user presses Ctrl+C (SIGINT) or another signal,
    it cleans up resources: Stops web server, resets GPIO pins, disables watchdog, and exits cleanly.
    Prevents hardware from being left in unsafe states (e.g., relays on). Non-programmer: Like turning off lights and locking doors
    before leaving a room, instead of just walking out.
    
    Args:
        sig (int): The signal number (e.g., signal.SIGINT for Ctrl+C).
        frame: The current stack frame (not used here).
    
    Returns:
        None: Just performs cleanup and exits.
    """
    # Log the shutdown reason.
    logging.info("Script stopped by user or signal.")
    # Global: Stop web server if running.
    global web_server
    if web_server:
        web_server.shutdown()  # Gracefully shut down Flask server.
    # Clean up GPIO: Reset all pins to default (input/low).
    if GPIO:
        GPIO.cleanup()
    # Disable watchdog to prevent accidental reset during shutdown.
    close_watchdog()
    # Exit with success code 0.
    sys.exit(0)

def load_offsets(num_channels, data_dir):
    """
    Load temperature calibration offsets from 'offsets.txt'.
    Offsets adjust sensor readings so all sensors agree on the same temperature (calibration).
    File format: First line = startup median, next lines = offsets per channel.
    If file missing/corrupt/wrong size, returns None to trigger recalibration.
    Non-programmer: Like loading saved eyeglass prescription adjustments for each eye's lens.
    
    Args:
        num_channels (int): Total number of sensors (for validation).
        data_dir (str): Directory where offsets.txt is stored.
    
    Returns:
        tuple: (startup_median float or None, list of offsets or None)
    """
    # Build file path.
    offsets_path = os.path.join(data_dir, 'offsets.txt')
    # Log attempt.
    logging.info(f"Loading startup offsets from '{offsets_path}'.")
    # Check if file exists.
    if os.path.exists(offsets_path):
        try:
            # Read all lines from file.
            with open(offsets_path, 'r') as f:
                lines = f.readlines()
            # Must have at least median line.
            if len(lines) < 1:
                logging.warning("Invalid offsets.txt; using none.")
                return None, None
            # Parse median (first line).
            startup_median = float(lines[0].strip())
            # Parse offsets (rest of lines).
            offsets = [float(line.strip()) for line in lines[1:]]
            # Validate count matches channels.
            if len(offsets) != num_channels:
                logging.warning(f"Invalid offsets count; expected {num_channels}, got {len(offsets)}. Using none.")
                return None, None
            # Log loaded values (debug level).
            logging.debug(f"Loaded median {startup_median} and {len(offsets)} offsets.")
            return startup_median, offsets
        except (ValueError, IndexError):
            # Parse errors (bad numbers).
            logging.warning("Corrupt offsets.txt; using none.")
            return None, None
    # No file—log and return None.
    logging.warning("No 'offsets.txt' found; using none.")
    return None, None

def save_offsets(startup_median, startup_offsets, data_dir):
    """
    Save temperature calibration offsets to 'offsets.txt'.
    Writes the median and list of offsets to file for persistence across restarts.
    Only called when new calibration is computed (all sensors valid on first run).
    Non-programmer: Like saving your custom settings to a file so next time you open the app, they're remembered.
    
    Args:
        startup_median (float): The overall median temperature at calibration time.
        startup_offsets (list): List of offset values per channel.
        data_dir (str): Directory to save the file in.
    
    Returns:
        None
    """
    # Build path.
    offsets_path = os.path.join(data_dir, 'offsets.txt')
    # Log attempt.
    logging.info(f"Saving startup offsets to '{offsets_path}'.")
    try:
        # Open file for writing (overwrites existing).
        with open(offsets_path, 'w') as f:
            # Write median first.
            f.write(f"{startup_median}\n")
            # Write each offset on a line.
            for offset in startup_offsets:
                f.write(f"{offset}\n")
        # Log success.
        logging.debug("Offsets saved.")
    except IOError as e:
        # File write error (e.g., permissions).
        logging.error(f"Failed to save offsets: {e}")


def perform_calibration(settings, raw_temps, data_dir):
    """
    Perform temperature sensor calibration.
    
    IMPORTANT: Calibration should only be done during initial commissioning when all
    batteries are at the SAME temperature (thermal equilibrium). The offsets.txt file
    should be created once and preserved - never auto-recalculated during normal operation.
    
    To recalibrate (e.g., after sensor replacement):
    1. Set recalibrate_offsets = true in battery_monitor.ini
    2. Restart the BMS script
    3. Delete offsets.txt to force new calibration
    
    Args:
        settings (dict): Configuration settings.
        raw_temps (list): Raw temperature readings from all sensors.
        data_dir (str): Directory containing offsets.txt.
    
    Returns:
        tuple: (startup_median, startup_offsets)
    """
    global _calibration_cache
    
    # Return cached calibration if available (avoid repeated disk reads)
    if _calibration_cache is not None:
        return _calibration_cache
    
    offsets_file = os.path.join(data_dir, 'offsets.txt')
    
    # Check if offsets already exist
    if os.path.exists(offsets_file):
        # Load existing offsets - NEVER auto-recalculate
        _saved_median, startup_offsets = load_offsets(settings['total_channels'], data_dir)
        if startup_offsets is not None:
            # Calculate fresh startup_median from current readings for display
            # Offsets are preserved from original calibration for accuracy
            valid_temps = [t for t in raw_temps if t is not None and t > settings['valid_min']]
            if valid_temps:
                startup_median = statistics.median(valid_temps)
                logging.info(f"Loaded calibration offsets (cal median={_saved_median:.1f}C). Current startup median={startup_median:.1f}C")
            else:
                startup_median = _saved_median
                logging.info(f"Loaded existing calibration: Median={startup_median:.1f}C (no valid current readings)")
            _calibration_cache = (startup_median, startup_offsets)
            return startup_median, startup_offsets
    
    # First time calibration (commissioning) or forced recalibration
    # Only calculate if we have valid readings from all sensors
    valid_count = sum(1 for t in raw_temps if t is not None and t > settings['valid_min'])
    if valid_count < settings['total_channels']:
        logging.warning(f"Incomplete sensor readings ({valid_count}/{settings['total_channels']}). Cannot calibrate.")
        return None, None
    
    # Calculate offsets from median
    startup_median = statistics.median(raw_temps)
    startup_offsets = [startup_median - t for t in raw_temps]
    
    # Save to file
    save_offsets(startup_median, startup_offsets, data_dir)
    logging.info(f"Calibration complete: Median={startup_median:.1f}C (offsets.txt saved)")
    
    _calibration_cache = (startup_median, startup_offsets)
    return startup_median, startup_offsets


def check_invalid_reading(raw, ch, alerts, valid_min, settings):
    """
    Check if a raw temperature reading is invalid (disconnected sensor).
    If reading <= valid_min (e.g., 0°C), it's likely a disconnected or failed sensor.
    Adds alert message with battery/bank details and logs warning. Non-programmer: Like checking if a thermometer shows
    an impossible value (e.g., -100°C) and flagging it as broken.
    
    Args:
        raw (float): Raw temperature value from sensor.
        ch (int): Global channel number (1-based).
        alerts (list): List to append alert strings to.
        valid_min (float): Minimum valid temperature threshold.
        settings (dict): Config for event logging size.
    
    Returns:
        bool: True if invalid (alert added), False otherwise.
    """
    # Check if raw is invalid (None means read failed).
    if raw is None or raw <= valid_min:
        # Get bank and battery/local details for descriptive alert.
        bank = get_bank_for_channel(ch)
        bat_id, local_ch = get_battery_and_local_ch(ch, settings["num_series_banks"], settings["sensors_per_bank"])
        # Build alert message with details.
        alert = f"Battery {bat_id} Bank {bank} Local Ch {local_ch}: Invalid reading (≤ {valid_min})."
        # Add to alerts list.
        alerts.append(alert)
        # Add to event log with timestamp.
        event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
        # Trim log if too long.
        if len(event_log) > settings.get('EventLogSize', 20):
            event_log.pop(0)
        # Log warning.
        logging.warning(f"Invalid reading on Battery {bat_id} Bank {bank} Local Ch {local_ch}: {raw} ≤ {valid_min}.")
        return True  # Invalid.
    return False  # Valid.

def check_high_temp(calibrated, ch, alerts, high_threshold, settings):
    """
    Check if calibrated temperature exceeds high threshold.
    If temp > high_threshold (e.g., 42°C), it's overheating—add alert and log.
    Non-programmer: Like a fire alarm going off if room gets too hot.
    
    Args:
        calibrated (float): Adjusted temperature value.
        ch (int): Channel number.
        alerts (list): List for alert messages.
        high_threshold (float): Max safe temperature.
        settings (dict): For event log size.
    
    Returns:
        None
    """
    # Check condition.
    if calibrated is not None and calibrated > high_threshold:
        # Get details.
        bank = get_bank_for_channel(ch)
        bat_id, local_ch = get_battery_and_local_ch(ch, settings["num_series_banks"], settings["sensors_per_bank"])
        # Alert with value.
        alert = f"Battery {bat_id} Bank {bank} Local Ch {local_ch}: High temp ({calibrated:.1f}°C > {high_threshold}°C)."
        alerts.append(alert)
        event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
        if len(event_log) > settings.get('EventLogSize', 20):
            event_log.pop(0)
        logging.warning(f"High temp alert on Battery {bat_id} Bank {bank} Local Ch {local_ch}: {calibrated:.1f} > {high_threshold}.")

def check_low_temp(calibrated, ch, alerts, low_threshold, settings):
    """
    Check if calibrated temperature is below low threshold.
    If temp < low_threshold (e.g., 0°C), it's too cold—add alert and log.
    Non-programmer: Like a frost warning if temperature drops too low.
    
    Args:
        calibrated (float): Adjusted temperature.
        ch (int): Channel.
        alerts (list): Alert list.
        low_threshold (float): Min safe temperature.
        settings (dict): Event log size.
    
    Returns:
        None
    """
    if calibrated is not None and calibrated < low_threshold:
        bank = get_bank_for_channel(ch)
        bat_id, local_ch = get_battery_and_local_ch(ch, settings["num_series_banks"], settings["sensors_per_bank"])
        alert = f"Battery {bat_id} Bank {bank} Local Ch {local_ch}: Low temp ({calibrated:.1f}°C < {low_threshold}°C)."
        alerts.append(alert)
        event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
        if len(event_log) > settings.get('EventLogSize', 20):
            event_log.pop(0)
        logging.warning(f"Low temp alert on Battery {bat_id} Bank {bank} Local Ch {local_ch}: {calibrated:.1f} < {low_threshold}.")

def check_deviation(calibrated, bank_median, ch, alerts, abs_deviation_threshold, deviation_threshold, settings):
    """
    Check if sensor temperature deviates too much from its bank's median.
    Deviation can be absolute (e.g., >2°C diff) or relative (e.g., >10% diff)—flags faulty sensor.
    Non-programmer: Like spotting one person in a group who's way off the average height—might be measurement error.
    
    Args:
        calibrated (float): Sensor temp.
        bank_median (float): Median of bank's sensors.
        ch (int): Channel.
        alerts (list): Alert list.
        abs_deviation_threshold (float): Absolute diff threshold °C.
        deviation_threshold (float): Relative diff threshold (fraction).
        settings (dict): Event log.
    
    Returns:
        None
    """
    # Check for None values first.
    if calibrated is None or bank_median is None:
        return
    # Calculate absolute deviation.
    abs_dev = abs(calibrated - bank_median)
    # Relative: abs_dev / |median|, avoid divide by zero.
    rel_dev = abs_dev / abs(bank_median) if bank_median != 0 else 0
    # Check either threshold exceeded.
    if abs_dev > abs_deviation_threshold or rel_dev > deviation_threshold:
        bank = get_bank_for_channel(ch)
        bat_id, local_ch = get_battery_and_local_ch(ch, settings["num_series_banks"], settings["sensors_per_bank"])
        alert = f"Battery {bat_id} Bank {bank} Local Ch {local_ch}: Deviation from bank median (abs {abs_dev:.1f}°C or {rel_dev:.2%})."
        alerts.append(alert)
        event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
        if len(event_log) > settings.get('EventLogSize', 20):
            event_log.pop(0)
        logging.warning(f"Deviation alert on Battery {bat_id} Bank {bank} Local Ch {local_ch}: abs {abs_dev:.1f}, rel {rel_dev:.2%}.")

def check_abnormal_rise(current, previous_temps, ch, alerts, poll_interval, rise_threshold, settings):
    """
    Check for abnormal temperature rise since last poll.
    If increase > rise_threshold (e.g., 2°C in 10s), it might indicate a problem like short circuit.
    Only checks if previous reading exists. Non-programmer: Like detecting sudden fever spike—needs attention.
    
    Args:
        current (float): Current temp.
        previous_temps (list): List of previous temps.
        ch (int): Channel.
        alerts (list): Alerts.
        poll_interval (float): Time since last read s.
        rise_threshold (float): Max allowed rise °C.
        settings (dict): Event log.
    
    Returns:
        None
    """
    # Skip if current is None.
    if current is None:
        return
    # Get previous for this channel (0-based index).
    previous = previous_temps[ch-1]
    # Only if previous exists.
    if previous is not None:
        # Type check for safety (avoid comparing wrong types).
        if not isinstance(current, (int, float)) or not isinstance(previous, (int, float)):
            logging.warning(f"Type error in check_abnormal_rise for ch {ch}: current={type(current)} {current}, previous={type(previous)} {previous}")
            return
        # Calculate rise.
        rise = current - previous
        # Check threshold.
        if rise > rise_threshold:
            bank = get_bank_for_channel(ch)
            bat_id, local_ch = get_battery_and_local_ch(ch, settings["num_series_banks"], settings["sensors_per_bank"])
            alert = f"Battery {bat_id} Bank {bank} Local Ch {local_ch}: Abnormal rise ({rise:.1f}°C in {poll_interval}s)."
            alerts.append(alert)
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
            if len(event_log) > settings.get('EventLogSize', 20):
                event_log.pop(0)
            logging.warning(f"Abnormal rise alert on Battery {bat_id} Bank {bank} Local Ch {local_ch}: {rise:.1f}°C.")

def check_group_tracking_lag(current, previous_temps, bank_median_rise, ch, alerts, disconnection_lag_threshold, settings):
    """
    Check if sensor's change lags behind the bank's median change (possible disconnection).
    If diff in changes > threshold, sensor isn't tracking group—might be loose wire.
    Non-programmer: Like one runner in a team falling behind while others keep pace—straggler alert.
    
    Args:
        current (float): Current temp.
        previous_temps (list): Previous temps.
        bank_median_rise (float): Bank's median change.
        ch (int): Channel.
        alerts (list): Alerts.
        disconnection_lag_threshold (float): Max lag °C.
        settings (dict): Event log.
    
    Returns:
        None
    """
    # Skip if current is None.
    if current is None:
        return
    previous = previous_temps[ch-1]
    if previous is not None:
        if not isinstance(current, (int, float)) or not isinstance(previous, (int, float)):
            logging.warning(f"Type error in check_group_tracking_lag for ch {ch}: current={type(current)} {current}, previous={type(previous)} {previous}")
            return
        rise = current - previous
        if abs(rise - bank_median_rise) > disconnection_lag_threshold:
            bank = get_bank_for_channel(ch)
            bat_id, local_ch = get_battery_and_local_ch(ch, settings["num_series_banks"], settings["sensors_per_bank"])
            alert = f"Battery {bat_id} Bank {bank} Local Ch {local_ch}: Lag from bank group ({rise:.1f}°C vs {bank_median_rise:.1f}°C)."
            alerts.append(alert)
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
            if len(event_log) > settings.get('EventLogSize', 20):
                event_log.pop(0)
            logging.warning(f"Lag alert on Battery {bat_id} Bank {bank} Local Ch {local_ch}: rise {rise:.1f} vs median {bank_median_rise:.1f}.")

def check_sudden_disconnection(current, previous_temps, ch, alerts, settings):
    """
    Check for sudden sensor disconnection (was valid, now invalid).
    If previous was good but current is None/invalid, alert. Non-programmer: Like a light that was on suddenly going out—check the bulb.
    
    Args:
        current: Current temp (or None).
        previous_temps (list): Previous.
        ch (int): Channel.
        alerts (list): Alerts.
        settings (dict): Event log.
    
    Returns:
        None
    """
    previous = previous_temps[ch-1]
    # Type safety.
    if not isinstance(previous, (int, float, type(None))) or not isinstance(current, (int, float, type(None))):
        logging.warning(f"Type error in check_sudden_disconnection for ch {ch}: current={type(current)} {current}, previous={type(previous)} {previous}")
        return
    # Check transition to invalid.
    if previous is not None and current is None:
        bank = get_bank_for_channel(ch)
        bat_id, local_ch = get_battery_and_local_ch(ch, settings["num_series_banks"], settings["sensors_per_bank"])
        alert = f"Battery {bat_id} Bank {bank} Local Ch {local_ch}: Sudden disconnection."
        alerts.append(alert)
        event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
        if len(event_log) > settings.get('EventLogSize', 20):
            event_log.pop(0)
        logging.warning(f"Sudden disconnection alert on Battery {bat_id} Bank {bank} Local Ch {local_ch}.")

def choose_channel(channel, multiplexer_address):
    """
    Switch to a specific I2C channel using the TCA9548A multiplexer.
    The multiplexer allows accessing multiple I2C devices on different channels (like a switchboard).
    Writes a byte to the mux address with bit set for the channel (e.g., channel 0 = 0x01).
    Non-programmer: Like selecting which outlet to plug into on a power strip with switches.
    
    Args:
        channel (int): Channel number (0-7 typically).
        multiplexer_address (int): I2C address of the mux (e.g., 0x70).
    
    Returns:
        None
    """
    # Log for debug.
    logging.debug(f"Switching to I2C channel {channel}.")
    if bus:
        try:
            # Write byte: 1 shifted left by channel number (bitmask).
            bus.write_byte(multiplexer_address, 1 << channel)
        except IOError as e:
            logging.error(f"I2C error selecting channel {channel}: {str(e)}")

def setup_voltage_meter(settings):
    """
    Configure the ADS1115 ADC for voltage measurement.
    Sets continuous mode, sample rate, and gain via config register.
    Non-programmer: Like setting dials on a voltmeter for accurate reading (range, speed).
    
    Args:
        settings (dict): ADC config values.
    
    Returns:
        None
    """
    # Log.
    logging.debug("Configuring voltage meter ADC.")
    if bus:
        try:
            # Combine config bits: Continuous mode | sample rate | gain.
            config_value = (settings['ContinuousModeConfig'] |
                            settings['SampleRateConfig'] |
                            settings['GainConfig'])
            # Write to config register.
            bus.write_word_data(settings['VoltageMeterAddress'], settings['ConfigRegister'], config_value)
        except IOError as e:
            logging.error(f"I2C error configuring voltage meter: {str(e)}")

def read_voltage_with_retry(bank_id, settings):
    """
    Read voltage from a specific bank with retries and averaging.
    Selects I2C channel for the bank, configures ADC, reads raw ADC value twice, averages valid readings (filters outliers >5% diff).
    Converts raw to voltage using formula and calibration. Retries whole process up to 2 times on failure.
    Updates alive_timestamp during reads for watchdog. Non-programmer: Like measuring battery level with a multimeter,
    taking multiple samples and averaging to be sure.
    
    Args:
        bank_id (int): Bank number (1 to num_series_banks).
        settings (dict): Config for calibration, ratios, etc.
    
    Returns:
        tuple: (average_voltage float or None, list of valid readings, list of valid raw ADC)
    """
    # Global: Update timestamp.
    global alive_timestamp
    # Log start.
    logging.info(f"Starting voltage read for Bank {bank_id}.")
    # Validate bank_id.
    if bank_id > settings['num_series_banks'] or bank_id < 1:
        logging.warning(f"Bank {bank_id} exceeds configured num_series_banks ({settings['num_series_banks']}). Cannot read voltage.")
        return None, [], []
    # Get scaling and calibration.
    voltage_divider_ratio = settings['VoltageDividerRatio']
    sensor_id = bank_id
    calibration_factor = settings[f'Sensor{sensor_id}_Calibration']
    # Retry up to 2 times.
    for attempt in range(2):
        # Update timestamp.
        alive_timestamp = time.time()
        logging.debug(f"Voltage read attempt {attempt+1} for Bank {bank_id}.")
        # Lists for readings.
        readings = []
        raw_values = []
        # Take 2 samples.
        for _ in range(2):
            # Update timestamp.
            alive_timestamp = time.time()
            # Channel = bank-1 (0-based).
            meter_channel = bank_id - 1 # Direct mapping: Bank 1 = Channel 0, Bank 2 = Channel 1, etc.
            # Select channel on mux.
            choose_channel(meter_channel, settings['MultiplexerAddress'])
            # Configure ADC.
            setup_voltage_meter(settings)
            if bus:
                try:
                    # Start conversion (write 0x01?).
                    bus.write_byte(settings['VoltageMeterAddress'], 0x01)
                    # Short delay for conversion.
                    time.sleep(0.05)
                    # Update timestamp.
                    alive_timestamp = time.time()
                    # Read 16-bit word from conversion reg.
                    raw_adc = bus.read_word_data(settings["VoltageMeterAddress"], settings["ConversionRegister"])
                except IOError as e:
                    logging.error(f"I2C error in voltage read for Bank {bank_id}: {str(e)}")
                    raw_adc = 0
            else:
                # Test mode: Fake value.
                raw_adc = 16000 + bank_id * 100
            # Log raw.
            logging.debug(f"Raw ADC for Bank {bank_id} (Sensor {sensor_id}): {raw_adc}")
            # Convert if non-zero.
            if raw_adc != 0:
                # ADS1115 returns big-endian unsigned 16-bit values.
                # Byte swap to get little-endian unsigned value.
                # ADS1115 is a single-ended ADC, so values are always positive (0-32767).
                # Calculate voltage from unsigned ADC value.
                # Formula: Raw * (FullScale / 32767)
                # FullScale = 6.144V when PGA gain = 1 (±6.144V range)
                # FSR based on PGA gain:
                # 0x0000: 6.144V, 0x0200: 4.096V, 0x0400: 2.048V, 0x0600: 1.024V, 0x0800: 0.512V
                fsr = 6.144  # For gain 0x0000 (±6.144V range)
                
                # Byte swap ADS1115 big-endian to little-endian
                swapped_adc = ((raw_adc & 0xFF) << 8) | ((raw_adc >> 8) & 0xFF)
                
                # Convert to signed 16-bit (two's complement)
                if swapped_adc & 0x8000:
                    signed_adc = swapped_adc - 0x10000
                else:
                    signed_adc = swapped_adc
                
                # Use absolute value (voltage is always positive)
                abs_adc = abs(signed_adc)
                
                # Calculate voltage from ADC value
                measured_voltage = abs_adc * (fsr / 32767)
                
                # Apply voltage divider ratio and calibration factor.
                actual_voltage = (measured_voltage / voltage_divider_ratio) * calibration_factor
                readings.append(actual_voltage)
                raw_values.append(raw_adc)

            else:
                # Zero reading.
                readings.append(0.0)
                raw_values.append(0)
        # If readings, average.
        if readings:
            average = sum(readings) / len(readings)
            # If average is zero, all readings are zero — hardware failure, reject them all.
            if average == 0:
                logging.warning(f"All voltage readings for Bank {bank_id} are zero — possible hardware failure.")
            else:
                # Filter valid: Within 5% of average.
                valid_readings = [r for r in readings if abs(r - average) / average <= 0.05]
                valid_adc = [raw_values[i] for i, r in enumerate(readings) if abs(r - average) / average <= 0.05]
                if valid_readings:
                    # Success—average valids.
                    logging.info(f"Voltage read successful for Bank {bank_id}: {average:.2f}V.")
                    return sum(valid_readings) / len(valid_readings), valid_readings, valid_adc
        # Inconsistent—retry.
        logging.debug(f"Readings for Bank {bank_id} inconsistent, retrying.")
    # All retries failed.
    logging.error(f"Couldn't get good voltage reading for Bank {bank_id} after 2 tries.")
    return None, [], []

def set_relay_connection(high, low, settings):
    """
    Set relay connections for balancing between high and low banks using GPIO.
    Looks up the relays for the pair in relay_mapping, sets corresponding GPIO pins HIGH to activate.
    For reset (high=0, low=0), sets all relay pins LOW. Assumes active-high relays.
    
    Args:
        high (int): Source bank (higher voltage), or 0 for reset.
        low (int): Destination bank, or 0 for reset.
        settings (dict): Config with relay_mapping and Relay{i}_Pin.
    
    Returns:
        None
    """
    try:
        # Validate banks unless reset.
        if high != 0 and low != 0:
            if high < 1 or low < 1 or high > settings['num_series_banks'] or low > settings['num_series_banks']:
                logging.warning(f"Bank {high} or {low} is out of range (1-{settings['num_series_banks']}). Cannot balance.")
                return
            logging.info(f"Attempting to set GPIO relays for connection from Bank {high} to {low}")
        else:
            logging.info("Resetting all GPIO relays to off")
        
        # Reset: Set all relay pins LOW
        if high == 0 and low == 0:
            num_relays = sum(1 for k in settings if k.startswith('Relay') and k.endswith('_Pin'))
            for i in range(num_relays):
                pin = settings[f'Relay{i}_Pin']
                GPIO.output(pin, GPIO.LOW)
            logging.info("All relays deactivated")
            return
        
        # Key for mapping (e.g., '1-2')
        pair_key = f"{high}-{low}"
        if pair_key in settings.get('relay_mapping', {}):
            relays = settings['relay_mapping'][pair_key]
            logging.debug(f"Activating relays {relays} for {pair_key}")
            # First, deactivate all relays to ensure clean state
            num_relays = sum(1 for k in settings if k.startswith('Relay') and k.endswith('_Pin'))
            for i in range(num_relays):
                pin = settings[f'Relay{i}_Pin']
                GPIO.output(pin, GPIO.LOW)
            # Activate specific relays
            for relay in relays:
                if 0 <= relay < 4:  # Validate relay index
                    pin = settings[f'Relay{relay}_Pin']
                    GPIO.output(pin, GPIO.HIGH)
                    logging.debug(f"Relay {relay} on pin {pin} activated")
                else:
                    logging.warning(f"Invalid relay index {relay} for {pair_key}")
        else:
            logging.warning(f"No relay mapping found for {pair_key}. Cannot balance.")
            return
        
        logging.info(f"GPIO relay setup completed for balancing from Bank {high} to {low}")
    except Exception as e:
        logging.error(f"Error in set_relay_connection: {e}")

def control_dcdc_converter(turn_on, settings):
    """
    Turn DC-DC converter relay on/off via GPIO.
    The converter transfers power during balancing. Non-programmer: Like turning a pump on/off to move water.
    
    Args:
        turn_on (bool): True to activate, False to deactivate.
        settings (dict): GPIO pin for relay.
    
    Returns:
        None
    """
    try:
        if GPIO:
            # Set pin high (on) or low (off).
            GPIO.output(settings['DC_DC_RelayPin'], GPIO.HIGH if turn_on else GPIO.LOW)
        # Log status.
        logging.info(f"DC-DC Converter is now {'on' if turn_on else 'off'}")
    except Exception as e:
        logging.error(f"Problem controlling DC-DC converter: {e}")

def send_alert_email(message, settings):
    """
    Send an email alert if enough time has passed since last one (throttled).
    Builds MIME message, connects to SMTP, logs in, sends. Non-programmer: Like texting an alert but with spam control.
    
    Args:
        message (str): Alert text body.
        settings (dict): SMTP config.
    
    Returns:
        None
    """
    # Global: Check throttle.
    global last_email_time
    if time.time() - last_email_time < settings['EmailAlertIntervalSeconds']:
        logging.debug("Skipping alert email to avoid flooding.")
        return
    try:
        # Create text message.
        msg = MIMEText(message)
        msg['Subject'] = "Battery Monitor Alert"
        msg['From'] = settings['SenderEmail']
        msg['To'] = settings['RecipientEmail']
        # Connect to SMTP server.
        with smtplib.SMTP(settings['SMTP_Server'], settings['SMTP_Port']) as server:
            # Enable TLS encryption.
            server.starttls()
            # Login if credentials provided.
            if settings['SMTP_Username'] and settings['SMTP_Password']:
                server.login(settings['SMTP_Username'], settings['SMTP_Password'])
            # Send the message.
            server.send_message(msg)
        # Update timer.
        last_email_time = time.time()
        logging.info(f"Alert email sent: {message}")
    except Exception as e:
        logging.error(f"Failed to send alert email: {e}")

def check_for_issues(voltages, temps_alerts, settings):
    """
    Check voltages and combine with temp alerts; activate alarm if needed.
    Scans each bank's voltage for zero, high/low; adds alerts. If any issues or startup/balancer flags,
    turns on alarm relay and sends email (throttled). Non-programmer: Like a central alarm system checking all sensors.
    
    Args:
        voltages (list): List of bank voltages.
        temps_alerts (list): Existing temp alerts.
        settings (dict): Thresholds, GPIO, email.
    
    Returns:
        tuple: (alert_needed bool, list of all alerts)
    """
    # Global flags.
    global startup_failed, startup_alerts, balancer_failed, balancer_fail_reason
    # Log start.
    logging.info("Checking for voltage and temp issues.")
    # Initial: Check flags.
    alert_needed = startup_failed or balancer_failed
    # List for all alerts.
    alerts = []
    # Add startup alerts if failed.
    if startup_failed and startup_alerts:
        alerts.append("Startup failures: " + "; ".join(startup_alerts))
    # Add balancer flag alert with verbose reason.
    if balancer_failed:
        if balancer_fail_reason:
            alerts.append(f"Balancer FAILED: {balancer_fail_reason}")
        else:
            alerts.append("Balancer hardware failure detected - balancing disabled.")
    # Check each voltage.
    for i, v in enumerate(voltages, 1):
        if v is None or v == 0.0:
            # Zero/None: Disconnected or error.
            alert = f"Bank {i}: Zero voltage."
            alerts.append(alert)
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
            if len(event_log) > settings.get('EventLogSize', 20):
                event_log.pop(0)
            logging.warning(f"Zero voltage alert on Bank {i}.")
            alert_needed = True
        elif v > settings['HighVoltageThresholdPerBattery']:
            # Overvoltage.
            alert = f"Bank {i}: High voltage ({v:.2f}V)."
            alerts.append(alert)
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
            if len(event_log) > settings.get('EventLogSize', 20):
                event_log.pop(0)
            logging.warning(f"High voltage alert on Bank {i}: {v:.2f}V.")
            alert_needed = True
        elif v < settings['LowVoltageThresholdPerBattery']:
            # Undervoltage.
            alert = f"Bank {i}: Low voltage ({v:.2f}V)."
            alerts.append(alert)
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
            if len(event_log) > settings.get('EventLogSize', 20):
                event_log.pop(0)
            logging.warning(f"Low voltage alert on Bank {i}: {v:.2f}V.")
            alert_needed = True
    # Add temp alerts.
    if temps_alerts:
        alerts.extend(temps_alerts)
        alert_needed = True
    # If alerts needed, activate hardware alarm and send email.
    if alert_needed:
        if GPIO:
            GPIO.output(settings['AlarmRelayPin'], GPIO.HIGH)  # Turn on buzzer/light.
        logging.info("Alarm relay activated.")
        send_alert_email("\n".join(alerts), settings)
    else:
        # No issues—deactivate alarm.
        if GPIO:
            GPIO.output(settings['AlarmRelayPin'], GPIO.LOW)
        logging.info("No issues; alarm relay deactivated.")
    # Return status and full alerts.
    return alert_needed, alerts

def balance_battery_voltages(stdscr, high, low, settings, temps_alerts, is_heating=False):
    """
    Balance the charge between two battery banks by transferring energy from high to low voltage.
    This function is like a water leveler for batteries. When one battery bank has more "energy level"
    (higher voltage) than another, it connects them through special hardware to move some charge
    from the fuller one to the emptier one, making their voltages more equal.
    It's like pouring water from a full bucket to an empty one to balance them out. The process
    takes time and shows progress on the screen. Safety checks prevent balancing if there are
    temperature problems or if it's too soon after the last balance.
    Now with verification: Monitors voltage changes to detect if balancing actually occurred (e.g., relays switched).
    If not, sets balancer_failed flag and alerts. For heating, balances regardless of diff to generate heat.
    Non-programmer: Like equalizing water levels in connected tanks, with a progress bar and safety locks.
    
    Args:
        stdscr: Curses screen object for TUI progress display.
        high (int): Bank number with higher voltage (source).
        low (int): Bank number with lower voltage (dest).
        settings (dict): Timings, thresholds, etc.
        temps_alerts (list): Temp issues—skips if any.
        is_heating (bool): True if for heating (ignore voltage diff).
    
    Returns:
        None
    """
    # Globals for state.
    global balance_start_time, last_balance_time, balancing_active, web_data, alive_timestamp, balancer_failed, balancer_failed_time, balancer_fail_count, balancer_fail_reason
    # Skip if balancer hardware failed.
    if balancer_failed:
        logging.warning("Skipping balancing due to balancer_failed flag.")
        return
    # Skip if temp alerts.
    if temps_alerts:
        logging.warning("Skipping balancing due to temperature anomalies in banks.")
        return
    # Mode name.
    mode = "Heating" if is_heating else "Normal"
    # Log start.
    logging.info(f"Starting {mode} balance from Bank {high} to {low}.")
    # Log event.
    event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {mode} balancing started from Bank {high} to {low}")
    if len(event_log) > settings.get('EventLogSize', 20):
        event_log.pop(0)
    # Set flags.
    balancing_active = True
    web_data['balancing'] = True
    # Read initial voltages.
    initial_high_v, _, _ = read_voltage_with_retry(high, settings)
    initial_low_v, _, _ = read_voltage_with_retry(low, settings)
    # Skip if either initial read failed (None) or low bank is zero (disconnected).
    if initial_high_v is None or initial_low_v is None:
        logging.warning(f"Cannot balance: initial voltage read failed (high={initial_high_v}, low={initial_low_v}). Skipping.")
        balancing_active = False
        web_data['balancing'] = False
        return
    if initial_low_v == 0.0:
        logging.warning(f"Cannot balance to Bank {low} (0.00V). Skipping.")
        balancing_active = False
        web_data['balancing'] = False
        return
    # Set relays.
    set_relay_connection(high, low, settings)
    # Turn on converter.
    control_dcdc_converter(True, settings)
    try:
        # Start timer.
        balance_start_time = time.time()
        # Initial trends.
        voltage_high = initial_high_v if initial_high_v is not None else 0.0
        voltage_low = initial_low_v if initial_low_v is not None else 0.0
        # Animation for progress.
        animation_frames = ['|', '/', '-', '\\']
        frame_index = 0
        # Screen dimensions for display.
        height, width = stdscr.getmaxyx()
        right_half_x = width // 2
        progress_y = 1
        high_trend = [voltage_high]
        low_trend = [voltage_low]
        # Read interval during balance (reuse startup).
        read_interval = settings['test_read_interval'] # Reuse from startup
        last_read = time.time()
        # Loop for duration.
        while time.time() - balance_start_time < settings['BalanceDurationSeconds']:
            # Update timestamp.
            alive_timestamp = time.time()
            # Progress calc.
            elapsed = time.time() - balance_start_time
            progress = min(1.0, elapsed / settings['BalanceDurationSeconds'])
            # Read voltages periodically.
            if time.time() - last_read >= read_interval:
                new_high, _, _ = read_voltage_with_retry(high, settings)
                new_low, _, _ = read_voltage_with_retry(low, settings)
                voltage_high = new_high if new_high is not None else voltage_high
                voltage_low = new_low if new_low is not None else voltage_low
                high_trend.append(voltage_high)
                low_trend.append(voltage_low)
                last_read = time.time()
            # Progress bar.
            bar_length = 20
            filled = int(bar_length * progress)
            bar = '=' * filled + ' ' * (bar_length - filled)
            # Display on TUI if space.
            if progress_y < height and right_half_x + 50 < width:
                try:
                    stdscr.addstr(progress_y, right_half_x, f"{mode} Balancing Bank {high} ({voltage_high:.2f}V) -> Bank {low} ({voltage_low:.2f}V)... [{animation_frames[frame_index % 4]}]", curses.color_pair(6))
                except curses.error:
                    logging.warning("addstr error for balancing status.")
                try:
                    stdscr.addstr(progress_y + 1, right_half_x, f"Progress: [{bar}] {int(progress * 100)}%", curses.color_pair(6))
                except curses.error:
                    logging.warning("addstr error for balancing progress bar.")
            else:
                logging.warning("Skipping balancing progress display - out of bounds.")
            stdscr.refresh()
            # Log progress.
            logging.debug(f"Balancing progress: {progress * 100:.2f}%, High: {voltage_high:.2f}V, Low: {voltage_low:.2f}V")
            frame_index += 1
            # Short sleep for animation.
            time.sleep(0.01)
        # Final reads.
        final_high_v, _, _ = read_voltage_with_retry(high, settings)
        final_low_v, _, _ = read_voltage_with_retry(low, settings)
        final_high_v = final_high_v if final_high_v is not None else voltage_high
        final_low_v = final_low_v if final_low_v is not None else voltage_low
        high_trend.append(final_high_v)
        low_trend.append(final_low_v)
    
        # Use module-level weighted_average function for both displayed values and verification
        # This smooths out DC-DC converter pulsing/inrush current variations
        # Weighting: more recent readings have higher weight (exponential decay)
        # This ensures the average reflects the end-of-balance state more than the start
        avg_high_v = weighted_average(high_trend)
        avg_low_v = weighted_average(low_trend)
    
        # Fallback to final reading if averaging fails
        avg_high_v = avg_high_v if avg_high_v is not None else final_high_v
        avg_low_v = avg_low_v if avg_low_v is not None else final_low_v
    
        # Log both discrete and averaged values for comparison
        logging.info(f"Balance final readings: Discrete High={final_high_v:.3f}V, Low={final_low_v:.3f}V | "
                     f"Averaged High={avg_high_v:.3f}V, Low={avg_low_v:.3f}V | "
                     f"Trend points: High={len(high_trend)}, Low={len(low_trend)}")
    
        # Display averaged values on console
        if progress_y + 2 < height and right_half_x + 60 < width:
            try:
                stdscr.addstr(progress_y + 2, right_half_x, f"Averaged: High={avg_high_v:.3f}V, Low={avg_low_v:.3f}V", curses.color_pair(4))
            except curses.error:
                logging.warning("addstr error for averaged values display.")
        # Update web_data with averaged readings BEFORE turning off converter
        # This ensures the displayed voltages reflect the balanced state, not settling values
        with data_lock:
            web_data['voltages'][high - 1] = avg_high_v
            web_data['voltages'][low - 1] = avg_low_v
        # Turn off converter.
    finally:
        # Turn off converter.
        control_dcdc_converter(False, settings)
        logging.info("Turning off DC-DC converter.")
        # Reset relays.
        set_relay_connection(0, 0, settings)
        logging.info("Resetting relay connections to default state.")
        # Reset flags.
        balancing_active = False
        with data_lock:
            web_data['balancing'] = False
        last_balance_time = time.time()
    # Verify: Check changes using AVERAGED readings, not discrete final readings
    # This avoids false failures due to DC-DC converter pulsing at the exact moment of final read
    if len(high_trend) >= 3 and len(low_trend) >= 3:
        # Use averaged readings for verification (more stable)
        high_change = avg_high_v - initial_high_v
        low_change = avg_low_v - initial_low_v
        # Also track discrete changes for logging
        discrete_high_change = final_high_v - initial_high_v
        discrete_low_change = final_low_v - initial_low_v
        min_delta = settings['min_voltage_delta']
        # Expected: High decreases, low increases by at least min_delta.
        if high_change >= 0 or low_change <= 0 or abs(high_change) < min_delta or low_change < min_delta:
            # Check if discrete readings show different trend (would indicate converter pulsing)
            if discrete_high_change < -min_delta and discrete_low_change > min_delta:
                # Discrete readings show success, but averaged failed - likely averaging issue
                alert = (f"Balancing ambiguous: Discrete shows change (High {discrete_high_change:+.3f}V, "
                        f"Low {discrete_low_change:+.3f}V) but averaged shows insufficient "
                        f"(High {high_change:+.3f}V, Low {low_change:+.3f}V). "
                        f"Will NOT set balancer_failed - check DC-DC converter pulsing.")
                logging.warning(alert)
                event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
                if len(event_log) > settings.get('EventLogSize', 20):
                    event_log.pop(0)
            else:
                alert = (f"Balancing failed from Bank {high} to {low}: No voltage change detected "
                        f"(Averaged High change: {high_change:+.3f}V, Low change: {low_change:+.3f}V, "
                        f"Discrete High: {discrete_high_change:+.3f}V, Low: {discrete_low_change:+.3f}V). "
                        f"Possible relay or DC-DC converter failure.")
                event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
                if len(event_log) > settings.get('EventLogSize', 20):
                    event_log.pop(0)
                logging.error(alert)
                balancer_failed = True
                balancer_failed_time = time.time()
                balancer_fail_count += 1
                balancer_fail_reason = alert
        else:
            # Success - log both averaged and discrete changes
            logging.info(f"Balancing verified: Averaged change: High {high_change:+.3f}V, Low {low_change:+.3f}V | "
                        f"Discrete change: High {discrete_high_change:+.3f}V, Low {discrete_low_change:+.3f}V.")
            # Reset consecutive failure counter on success
            if balancer_fail_count > 0:
                logging.info(f"Balancer recovery: successful verification after {balancer_fail_count} consecutive failure(s).")
                balancer_fail_count = 0
    else:
        logging.warning(f"Insufficient readings for balancing verification from {high} to {low}.")
    # Log end.
    logging.info(f"{mode} balancing process completed.")
    event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {mode} balancing completed from Bank {high} to {low}")
    if len(event_log) > settings.get('EventLogSize', 20):
        event_log.pop(0)

def compute_bank_medians(calibrated_temps, valid_min):
    """
    Compute median, min, max, and invalid count for each bank's temperatures.
    Groups sensors by bank, filters valids (>valid_min), computes stats using statistics.median.
    Handles empty banks with zeros. Non-programmer: Like summarizing test scores for each class group: average, lowest, highest, misses.
    
    Args:
        calibrated_temps (list): List of temps (None for invalid).
        valid_min (float): Threshold for valid.
    
    Returns:
        list: Dict per bank with 'median', 'min', 'max', 'invalid' counts.
    """
    # List for bank stats.
    bank_stats = []
    # For each bank.
    for bank_indices in BANK_SENSOR_INDICES:
        # Get valid temps for this bank.
        bank_temps = [calibrated_temps[i] for i in bank_indices if calibrated_temps[i] is not None]
        # Count invalids.
        invalid_count = len(bank_indices) - len(bank_temps)
        if bank_temps:
            try:
                # Compute stats.
                med = statistics.median(bank_temps)
                mn = min(bank_temps)
                mx = max(bank_temps)
            except (TypeError, ValueError, statistics.StatisticsError) as e:
                # Error (e.g., all non-numeric)—default to 0.
                logging.warning(f"Error calculating stats for bank: {e}, temps={bank_temps}")
                med = mn = mx = 0.0
        else:
            # No valids.
            med = mn = mx = 0.0
        # Store as dict.
        bank_stats.append({'median': med, 'min': mn, 'max': mx, 'invalid': invalid_count})
    return bank_stats

def fetch_rrd_history(settings):
    """
    Fetch historical data from RRD database for charts.
    Uses rrdtool xport to get last HISTORY_LIMIT points (60s steps) for medtemp and each volt bank.
    Parses XML output into list of dicts with time and values (None for NaN). Non-programmer: Like pulling recent log entries
    from a journal for a trend graph.
    
    Args:
        settings (dict): Num banks for DS count.
    
    Returns:
        list: History entries reversed (newest first), or empty on error.
    """
    # Start time: Now minus limit * 60s.
    start = int(time.time()) - (HISTORY_LIMIT * 60)
    try:
        # Build DEF lines for each DS.
        def_list = [f'DEF:mt={RRD_FILE}:medtemp:LAST']
        xport_list = ['XPORT:mt:MedianTemp']
        for i in range(1, settings['num_series_banks'] + 1):
            def_list.append(f'DEF:v{i}={RRD_FILE}:volt{i}:LAST')
            xport_list.append(f'XPORT:v{i}:Bank{i}')
        # Run xport command.
        output = subprocess.check_output(['rrdtool', 'xport',
                                          '--start', str(start),
                                          '--end', 'now',
                                          '--step', '60'] + def_list + xport_list)
        # Log raw for debug.
        logging.debug(f"Raw RRD xport output: {output.decode()}")
        # Parse XML.
        root = ET.fromstring(output.decode())
        # Get meta if present.
        meta = root.find('meta')
        if meta is not None:
            meta_start = int(meta.find('start').text) if meta.find('start') is not None else start
            meta_step = int(meta.find('step').text) if meta.find('step') is not None else 60
        else:
            meta_start = start
            meta_step = 60
        # List for data.
        data = []
        current_time = meta_start
        # Expected values per row: medtemp + banks.
        expected_vs = settings['num_series_banks'] + 1 # medtemp + volts
        # Process each row.
        for row in root.findall('.//row'):
            vs = []
            # Parse each <v> element.
            for v in row.findall('v'):
                if v.text is None:
                    vs.append(None)
                    continue
                try:
                    # NaN to None.
                    vs.append(float(v.text) if v.text != 'NaN' else None)
                except ValueError:
                    vs.append(None)
            # Skip incomplete rows.
            if len(vs) != expected_vs:
                logging.warning(f"Skipping RRD row with incomplete values (got {len(vs)}, expected {expected_vs}).")
                continue
            # Build row dict.
            row_data = {'time': current_time, 'medtemp': vs[0]}
            for i in range(settings['num_series_banks']):
                row_data[f'volt{i+1}'] = vs[i+1]
            data.append(row_data)
            # Next timestamp.
            current_time += meta_step
        # Log count.
        logging.debug(f"Fetched {len(data)} history entries from RRD.")
        # Reverse for newest first.
        return data[::-1]
    except subprocess.CalledProcessError as e:
        logging.error(f"RRD xport failed: {e}")
        return []
    except ET.ParseError as e:
        logging.error(f"RRD XML parse error: {e}. Output was: {output.decode()}")
        return []
    except FileNotFoundError:
        logging.error("rrdtool not found for fetch. Install rrdtool.")
        return []
    except Exception as e:
        logging.error(f"Unexpected error in fetch_rrd_history: {e}\n{traceback.format_exc()}")
        return []

def draw_tui(stdscr, voltages, calibrated_temps, raw_temps, offsets, bank_stats, startup_median, alerts, settings, startup_set, is_startup):
    """
    Draw the Terminal User Interface (TUI) using curses.
    Renders ASCII art batteries with voltages/temps overlaid, bank summaries, full temp list, alerts, config info, event log.
    Colors for status (green normal, red alert). Handles screen size limits. Non-programmer: Like drawing a dashboard on your terminal screen
    with pictures, numbers, and warnings.
    
    Args:
        stdscr: Curses window.
        voltages (list): Bank voltages.
        calibrated_temps (list): Temps.
        raw_temps (list): Raw temps (for startup display).
        offsets (list): Offsets.
        bank_stats (list): Bank summaries.
        startup_median (float): Calibration median.
        alerts (list): Current alerts.
        settings (dict): Config for display.
        startup_set (bool): If calibrated.
        is_startup (bool): First run flag for extra info.
    
    Returns:
        None
    """
    # Log refresh.
    logging.debug("Refreshing TUI.")
    # Clear screen.
    stdscr.clear()
    # Setup colors.
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_RED, -1)       # Red - overheat
    curses.init_pair(2, curses.COLOR_RED, -1)       # Red - alerts
    curses.init_pair(3, curses.COLOR_YELLOW, -1)    # Yellow - warm
    curses.init_pair(4, curses.COLOR_GREEN, -1)     # Green - normal
    curses.init_pair(5, curses.COLOR_WHITE, -1)     # White
    curses.init_pair(6, curses.COLOR_YELLOW, -1)    # Orange - hot (reuse yellow pair)
    curses.init_pair(7, curses.COLOR_CYAN, -1)      # Cyan - cold
    curses.init_pair(8, curses.COLOR_MAGENTA, -1)   # Magenta - very cold
    curses.init_pair(9, curses.COLOR_WHITE, -1)     # Spare
    # Screen size (needed early for comm stats display)
    screen_height, screen_width = stdscr.getmaxyx()
    
    # Communication Quality Display (bottom right)
    try:
        comm_stats = get_comm_stats()
        comm_str = "Comm: "
        for slave in comm_stats.get('slaves', []):
            rate = slave.get('success_rate', 0)
            comm_str += f"S{slave['slave_addr']}:{rate}% "
        # Position at bottom right
        display_y = max(0, screen_height - 2)
        display_x = max(0, screen_width - len(comm_str) - 2)
        try:
            stdscr.addstr(display_y, display_x, comm_str, curses.color_pair(4) | curses.A_BOLD)
        except curses.error:
            pass
    except Exception as e:
        pass  # Silent fail for display errors
    
    # Reassign to standard names for rest of function
    height, width = screen_height, screen_width
    right_half_x = width // 2
    # Total voltage and color.
    total_v = sum(voltages)
    total_high = settings['HighVoltageThresholdPerBattery'] * NUM_BANKS
    total_low = settings['LowVoltageThresholdPerBattery'] * NUM_BANKS
    v_color = curses.color_pair(2) if total_v > total_high else curses.color_pair(3) if total_v < total_low else curses.color_pair(4)
    # ASCII art for total V.
    roman_v = text2art(f"{total_v:.2f}V", font='roman', chr_ignore=True)
    roman_lines = roman_v.splitlines()
    # Draw art lines.
    for i, line in enumerate(roman_lines):
        if i + 1 < height and len(line) < right_half_x:
            try:
                stdscr.addstr(i + 1, 0, line, v_color)
            except curses.error:
                logging.warning(f"addstr error for total voltage art line {i+1}.")
        else:
            logging.warning(f"Skipping total voltage art line {i+1} - out of bounds.")
    # Offset for next section.
    y_offset = len(roman_lines) + 3
    if y_offset >= height:
        logging.warning("TUI y_offset exceeds height; skipping art.")
        return
    # Base battery ASCII art (one bank).
    battery_art_base = [
        " _______________ ",
        " |             | ",
        " |             | ",
        " |             | ",
        " |             | ",
        " | +++         | ",
        " | +++         | ",
        " |             | ",
        " |             | ",
        " |             | ",
        " |             | ",
        " | ---         | ",
        " | ---         | ",
        " | ---         | ",
        " |             | ",
        " |             | ",
        " |_____________| "
    ]
    art_height = len(battery_art_base)
    art_width = len(battery_art_base[0])
    gap = " "
    gap_len = len(gap)
    # Draw multiple banks side by side.
    for row, line in enumerate(battery_art_base):
        full_line = gap.join([line] * NUM_BANKS)
        if y_offset + row < height and len(full_line) < right_half_x:
            try:
                stdscr.addstr(y_offset + row, 0, full_line, curses.color_pair(4))
            except curses.error:
                logging.warning(f"addstr error for art row {row}.")
        else:
            logging.warning(f"Skipping art row {row} - out of bounds.")
    # Overlay voltages on art.
    for bank_id in range(NUM_BANKS):
        start_pos = bank_id * (art_width + gap_len)
        v_str = f"{voltages[bank_id]:.2f}V" if voltages[bank_id] > 0 else "0.00V"
        # Color based on status.
        v_color = curses.color_pair(8) if voltages[bank_id] == 0.0 else \
                 curses.color_pair(2) if voltages[bank_id] > settings['HighVoltageThresholdPerBattery'] else \
                 curses.color_pair(3) if voltages[bank_id] < settings['LowVoltageThresholdPerBattery'] else \
                 curses.color_pair(4)
        v_center = start_pos + (art_width - len(v_str)) // 2
        v_y = y_offset + 2
        if v_y < height and v_center + len(v_str) < right_half_x:
            try:
                stdscr.addstr(v_y, v_center, v_str, v_color)
            except curses.error:
                logging.warning(f"addstr error for voltage overlay Bank {bank_id+1}.")
        else:
            logging.warning(f"Skipping voltage overlay for Bank {bank_id+1} - out of bounds.")
        # Bank summary.
        summary = bank_stats[bank_id]
        med_str = f"Med: {summary['median']:.1f}°C"
        min_str = f"Min: {summary['min']:.1f}°C"
        max_str = f"Max: {summary['max']:.1f}°C"
        inv_str = f"Inv: {summary['invalid']}"
        # Color for summary.
        s_color = curses.color_pair(2) if summary['median'] > settings['high_threshold'] or summary['median'] < settings['low_threshold'] or summary['invalid'] > 0 else curses.color_pair(4)
        for idx, s_str in enumerate([med_str, min_str, max_str, inv_str]):
            s_center = start_pos + (art_width - len(s_str)) // 2
            s_y = y_offset + 7 + idx
            if s_y < height and s_center + len(s_str) < right_half_x:
                try:
                    stdscr.addstr(s_y, s_center, s_str, s_color)
                except curses.error:
                    logging.warning(f"addstr error for summary line {idx+1} Bank {bank_id+1}.")
            else:
                logging.warning(f"Skipping summary line {idx+1} for Bank {bank_id+1} - out of bounds.")
    # Next offset.
    y_offset += art_height + 2
    # Full temps per bank - TABLE format for ~30 line screens
    # Structure: 8 batteries × 3 banks × 8 cells = 192 cells total
    # Display: Each row shows all 8 cell temps for one battery across all 3 banks
    number_parallel = settings['number_of_parallel_batteries']
    sensors_per_bank = settings['sensors_per_bank']  # 8 cells per bank per battery
    
    # Table header
    header = "Bat\Bank  " + "   Bank 1    " + "   Bank 2    " + "   Bank 3    "
    if y_offset < height and len(header) < right_half_x:
        try:
            stdscr.addstr(y_offset, 0, header, curses.color_pair(7) | curses.A_BOLD)
        except curses.error:
            pass
    y_offset += 1
    
    # Separator
    sep = "-" * len(header)
    if y_offset < height and len(sep) < right_half_x:
        try:
            stdscr.addstr(y_offset, 0, sep, curses.color_pair(7))
        except curses.error:
            pass
    y_offset += 1
    
    # Show each battery as a row with 8 cell temps per bank
    for bat_id in range(1, number_parallel + 1):
        if y_offset >= height - 2:
            break
        
        x_pos = 0
        # Draw battery label in default color
        label = f"Battery {bat_id}  "
        if len(label) < right_half_x:
            try:
                stdscr.addstr(y_offset, x_pos, label, curses.color_pair(5))
                x_pos += len(label)
            except curses.error:
                pass
        
        for bank_id in range(NUM_BANKS):
            # Draw opening bracket in default color
            if x_pos + 1 < right_half_x:
                try:
                    stdscr.addstr(y_offset, x_pos, "[", curses.color_pair(5))
                    x_pos += 1
                except curses.error:
                    pass
            
            # Draw each cell with its individual color
            for sensor_pos in range(sensors_per_bank):
                global_idx = ((bat_id - 1) * NUM_BANKS * sensors_per_bank) + (bank_id * sensors_per_bank) + sensor_pos
                char_to_draw = "?"
                color = curses.color_pair(5)  # Default white
                
                if global_idx < len(calibrated_temps):
                    calib = calibrated_temps[global_idx]
                    if calib is not None:
                        bank_idx = bank_id
                        bank_median = bank_stats[bank_idx]['median'] if bank_idx < len(bank_stats) else calib
                        diff = calib - bank_median if bank_median else 0
                        disp_temp = calib
                        
                        if diff >= 3.0 or disp_temp > settings['high_threshold']:
                            char_to_draw = "H"
                            color = curses.color_pair(2) | curses.A_BOLD  # Red bold for hot
                        elif diff >= 2.0:
                            char_to_draw = "h"
                            color = curses.color_pair(6)  # Orange for warm
                        elif diff >= 1.0:
                            char_to_draw = "+"
                            color = curses.color_pair(3)  # Yellow for warmish
                        elif diff >= -1.0:
                            char_to_draw = "."
                            color = curses.color_pair(4)  # Green for normal
                        elif diff >= -2.0:
                            char_to_draw = "-"
                            color = curses.color_pair(7)  # Cyan for cool
                        elif diff >= -3.0:
                            char_to_draw = "l"
                            color = curses.color_pair(7) | curses.A_BOLD  # Blue bold for cold
                        else:
                            char_to_draw = "L"
                            color = curses.color_pair(8) | curses.A_BOLD  # Magenta bold for very cold
                    else:
                        char_to_draw = "?"
                        color = curses.color_pair(8)  # Magenta for invalid
                else:
                    char_to_draw = "-"
                    color = curses.color_pair(5)  # White for out of range
                
                if x_pos + 1 < right_half_x:
                    try:
                        stdscr.addstr(y_offset, x_pos, char_to_draw, color)
                        x_pos += 1
                    except curses.error:
                        pass
            
            # Draw closing bracket and space in default color
            if x_pos + 2 < right_half_x:
                try:
                    stdscr.addstr(y_offset, x_pos, "] ", curses.color_pair(5))
                    x_pos += 2
                except curses.error:
                    pass
        
        y_offset += 1
    
    # Color-coded legend at bottom
    if y_offset < height:
        try:
            # H = hot (red)
            stdscr.addstr(y_offset, 0, "H", curses.color_pair(2) | curses.A_BOLD)
            stdscr.addstr(y_offset, 1, "=hot>+3  ", curses.color_pair(2))
            # h = warm (orange)
            stdscr.addstr(y_offset, 10, "h", curses.color_pair(6))
            stdscr.addstr(y_offset, 11, "=warm>+2  ", curses.color_pair(6))
            # + = warmish (yellow)
            stdscr.addstr(y_offset, 22, "+", curses.color_pair(3))
            stdscr.addstr(y_offset, 23, "=warmish>+1  ", curses.color_pair(3))
            # . = normal (green)
            stdscr.addstr(y_offset, 37, ".", curses.color_pair(4))
            stdscr.addstr(y_offset, 38, "=normal  ", curses.color_pair(4))
            # - = cool (cyan)
            stdscr.addstr(y_offset, 47, "-", curses.color_pair(7))
            stdscr.addstr(y_offset, 48, "=cool<-2  ", curses.color_pair(7))
            # l = cold (blue)
            stdscr.addstr(y_offset, 58, "l", curses.color_pair(7))
            stdscr.addstr(y_offset, 59, "=cold  ", curses.color_pair(7))
            # L = very cold (magenta bold)
            stdscr.addstr(y_offset, 67, "L", curses.color_pair(8) | curses.A_BOLD)
            stdscr.addstr(y_offset, 68, "=v.cold  ", curses.color_pair(8))
            # ? = invalid (magenta)
            stdscr.addstr(y_offset, 78, "?", curses.color_pair(8))
            stdscr.addstr(y_offset, 79, "=invalid  ", curses.color_pair(8))
            # - = missing (white)
            stdscr.addstr(y_offset, 89, "-", curses.color_pair(5))
            stdscr.addstr(y_offset, 90, "=missing", curses.color_pair(5))
        except curses.error:
            pass
    y_offset += 1
    
    # Valid count
    if y_offset < height:
        valid_count = len([t for t in calibrated_temps if t is not None])
        total_count = len(calibrated_temps)
        pct = f"{valid_count*100/total_count:.1f}" if total_count > 0 else "0.0"
        row = f"Valid: {valid_count}/{total_count} ({pct}%) | Updated: {time.strftime('%H:%M:%S')}"
        if len(row) < right_half_x:
            try:
                stdscr.addstr(y_offset, 0, row, curses.color_pair(7))
            except curses.error:
                pass
    # Startup median.
    y_offset += 1
    med_str = f"{startup_median:.1f}°C" if startup_median else "N/A"
    if y_offset < height:
        try:
            stdscr.addstr(y_offset, 0, f"Startup Median Temp: {med_str}", curses.color_pair(7))
        except curses.error:
            logging.warning("addstr error for startup median.")
    else:
        logging.warning("Skipping startup median - out of bounds.")
    y_offset += 2
    # Alerts section.
    if y_offset < height:
        try:
            stdscr.addstr(y_offset, 0, "Alerts:", curses.color_pair(7))
        except curses.error:
            logging.warning("addstr error for alerts header.")
    y_offset += 1
    if alerts:
        for alert in alerts:
            if y_offset < height and len(alert) < right_half_x:
                try:
                    stdscr.addstr(y_offset, 0, alert, curses.color_pair(8))
                except curses.error:
                    logging.warning(f"addstr error for alert '{alert}'.")
            else:
                logging.warning(f"Skipping alert '{alert}' - out of bounds.")
            y_offset += 1
    else:
        if y_offset < height:
            try:
                stdscr.addstr(y_offset, 0, "No alerts.", curses.color_pair(4))
            except curses.error:
                logging.warning("addstr error for no alerts message.")
        else:
            logging.warning("Skipping no alerts message - out of bounds.")
    # Get local IP for web URL.
    local_ip = 'localhost'
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        local_ip = socket.gethostbyname(socket.gethostname())
    # Config display in right half.
    y_config = 3
    config_lines = [
        f"Web Dashboard URL: http://{local_ip}:{settings['web_port']}",
        f"Number of Parallel Batteries: {settings['number_of_parallel_batteries']}",
        f"Number of Series Banks: {settings['num_series_banks']}",
        f"Sensors per Bank per Battery: {settings['sensors_per_bank']}",
        f"Polling Interval: {settings['poll_interval']} seconds",
        f"Modbus Slave IPs: {', '.join(sorted(set(settings['modbus_slave_ips'])))}",
        f"Modbus TCP Ports: {', '.join(map(str, sorted(set(settings['modbus_slave_ports']))))}",
        f"High Temperature Threshold: {settings['high_threshold']}°C",
        f"Low Temperature Threshold: {settings['low_threshold']}°C",
        f"Absolute Deviation Threshold: {settings['abs_deviation_threshold']}°C",
        f"Relative Deviation Threshold: {settings['deviation_threshold']}",
        f"Abnormal Rise Threshold: {settings['rise_threshold']}°C",
        f"Group Lag Threshold: {settings['disconnection_lag_threshold']}°C",
        f"Cabinet Over-Temp Threshold: {settings['cabinet_over_temp_threshold']}°C",
        f"Valid Minimum Temperature: {settings['valid_min']}°C",
        f"Heating Threshold: {settings['heating_threshold']}°C",
        f"Low Voltage Threshold per Bank: {settings['LowVoltageThresholdPerBattery']}V",
        f"High Voltage Threshold per Bank: {settings['HighVoltageThresholdPerBattery']}V",
        f"Voltage Difference to Balance: {settings['VoltageDifferenceToBalance']}V",
        f"Min Balance Source Voltage: {settings.get('min_balance_source_voltage', 17.0)}V",
        f"Balance Duration: {settings['BalanceDurationSeconds']} seconds",
        f"Balance Rest Period: {settings['BalanceRestPeriodSeconds']} seconds"
    ]
    col_width = max(len(line) for line in config_lines) + 2
    num_cols = 1
    # Draw config lines (simple column).
    for i, line in enumerate(config_lines):
        col = i // 20
        row = i % 20
        if col < num_cols and y_config + row < height:
            try:
                stdscr.addstr(y_config + row, right_half_x + col * col_width, line, curses.color_pair(7))
            except curses.error:
                pass
    # Event history in bottom right.
    y_offset = height // 2
    if y_offset < height:
        try:
            stdscr.addstr(y_offset, right_half_x, "Event History:", curses.color_pair(7))
        except curses.error:
            logging.warning("addstr error for event history header.")
    y_offset += 1
    # Last 20 events.
    for event in event_log[-20:]:
        if y_offset >= height:
            break
        max_len = max(1, width - right_half_x - 1)
        trunc = event[:max_len]
        try:
            stdscr.addstr(y_offset, right_half_x, trunc, curses.color_pair(5))
        except curses.error:
            pass
        y_offset += 1
    # Refresh screen.
    stdscr.refresh()

def setup_watchdog(timeout=15):
    """
    Initialize the hardware watchdog timer.
    Loads appropriate kernel module (bcm2835_wdt for older Pi, rp1-wdt for Pi5+), opens /dev/watchdog,
    sets timeout via ioctl. Returns True if successful. Non-programmer: Like setting a timer that resets the Pi if the script hangs.
    
    Args:
        timeout (int): Watchdog timeout in seconds (default 15s, Pi max).
    
    Returns:
        bool: True if setup OK, False on failure.
    """
    # Check if fcntl available (Linux-specific).
    if fcntl is None:
        logging.warning("fcntl not available - watchdog disabled")
        return False
    # Global fd.
    global watchdog_fd
    try:
        # Detect Pi model from /proc.
        model = "Unknown"
        if os.path.exists('/proc/device-tree/model'):
            with open('/proc/device-tree/model', 'rb') as f:
                model = f.read().replace(b'\x00', b'').decode('utf-8', errors='ignore').strip().lower()
        # Choose module: Older Pi vs Pi5+.
        logging.info(f"Detected Raspberry Pi model: {model}")
        if 'raspberry pi' in model and not 'raspberry pi 5' in model:
            module = 'bcm2835_wdt'
        else:
            module = 'rp1-wdt'
            logging.info("Assuming rp1-wdt for Pi 5 or newer model")
        # Load module.
        os.system(f'modprobe {module}')
        logging.info(f"Loaded watchdog module: {module}")
        # Wait for load.
        time.sleep(1)
        # Check device file.
        if not os.path.exists(WATCHDOG_DEV):
            logging.error(f"Watchdog device {WATCHDOG_DEV} not found. Watchdog disabled.")
            return False
        # Open device.
        watchdog_fd = open(WATCHDOG_DEV, 'wb')
        logging.debug(f"Opened watchdog device: {WATCHDOG_DEV}")
        # Set timeout via ioctl (magic 'W' + 6, pack timeout).
        try:
            # WDIOC_SETTIMEOUT = _IOWR('W', 6, int) = 0xc0045706
            WDIOC_SETTIMEOUT = 0xc0045706
            fcntl.ioctl(watchdog_fd, WDIOC_SETTIMEOUT, struct.pack("I", timeout))
            logging.info(f"Watchdog timeout set to {timeout}s")
        except IOError as e:
            logging.warning(f"Failed to set watchdog timeout: {e}. Using kernel default.")
        # Log init.
        logging.debug("Watchdog initialized")
        return True
    except Exception as e:
        logging.error(f"Failed to setup watchdog: {e}.")
        return False

def watchdog_pet_thread(pet_interval=5, hang_threshold=60):
    """
    Dedicated thread to pet (reset) the watchdog every pet_interval seconds, but only if main thread is alive.
    Checks alive_timestamp; if diff > hang_threshold, assumes hang and stops petting (allows reset).
    Increased hang_threshold to 60s to prevent false hang detection during 8-slave Modbus reads (can take 20-40s), ensuring watchdog (15s timeout) is petted reliably.
    Non-programmer: Like a watchdog dog that you feed treats regularly; if you stop moving (hang), it barks and resets the system.
    
    Args:
        pet_interval (int): Seconds between pets (5s).
        hang_threshold (int): Max time without alive update before assuming hang (12s).
    
    Returns:
        None: Runs in loop until hang or error.
    """
    # Globals.
    global watchdog_fd, alive_timestamp
    # Infinite loop.
    while True:
        try:
            # Check if main hung (timestamp stale).
            if time.time() - alive_timestamp > hang_threshold:
                logging.warning("Main thread hang detected; stopping watchdog pets to allow reset.")
                break # Stop petting
            # Pet: Write 'w' to device.
            if watchdog_fd:
                watchdog_fd.write(b'w')
                watchdog_fd.flush()
                logging.debug("Watchdog petted")
        except IOError as e:
            # Pet failed—try reopen.
            logging.error(f"Watchdog pet failed: {e}. Reopening device.")
            try:
                watchdog_fd.close()
                watchdog_fd = open(WATCHDOG_DEV, 'wb')
            except IOError as reopen_e:
                logging.error(f"Failed to reopen watchdog: {reopen_e}. Disabling pets.")
                break
        # Wait.
        time.sleep(pet_interval)

def close_watchdog():
    """
    Disable watchdog by writing 'V' (disable) and closing file.
    Non-programmer: Like telling the watchdog "all good, go home—no reset needed."
    
    Returns:
        None
    """
    # Global.
    global watchdog_fd
    if watchdog_fd:
        try:
            # Write 'V' to disable.
            watchdog_fd.write(b'V')
            watchdog_fd.close()
        except IOError:
            pass  # Ignore errors on close.

def startup_self_test(settings, stdscr, data_dir):
    """
    Perform comprehensive startup self-test: Config, hardware, reads, calibration, balancer verification.
    Runs in loop with retries (up to 5, 2min wait). Displays progress on TUI. If fails max, resets flags and proceeds.
    Tests each step: Config valid, I2C/Modbus connect, initial reads, calibrate if all valid, test all balance pairs with delta check.
    Non-programmer: Like a car's startup diagnostic: Checks engine, lights, etc.; retries if issue, but drives if minor.
    
    Args:
        settings (dict): Config.
        stdscr: TUI screen.
        data_dir (str): For offsets.
    
    Returns:
        list: Empty if passed, or alerts (but proceeds anyway after retries).
    """
    # Globals.
    global startup_failed, startup_alerts, startup_set, startup_median, startup_offsets, balancer_failed, balancer_failed_time, balancer_fail_count, balancer_fail_reason
    # Skip if disabled.
    if not settings['StartupSelfTestEnabled']:
        logging.info("Startup self-test disabled via configuration.")
        return []
    # Max retries.
    max_retries = 5
    retries = 0
    # Retry loop.
    while retries < max_retries:
        # Log attempt.
        logging.info(f"Starting self-test attempt {retries + 1}")
        # Reset balancer_failed at start of each attempt so previous failures dont persist
        balancer_failed = False
        balancer_failed_time = None
        balancer_fail_reason = ""
        # Alerts for this run.
        alerts = []
        # Clear screen.
        stdscr.clear()
        y = 0
        # Title.
        if y < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y, 0, "Startup Self-Test in Progress", curses.color_pair(1))
            except curses.error:
                logging.warning("addstr error for title.")
        y += 2
        stdscr.refresh()
        # Step 1: Config.
        logging.info("Step 1: Validating configuration parameters.")
        logging.debug(
            f"Configuration details: I2C_BusNumber={settings['I2C_BusNumber']}, "
            f"MultiplexerAddress=0x{settings['MultiplexerAddress']:02x}, "
            f"VoltageMeterAddress=0x{settings['VoltageMeterAddress']:02x}, "
            f"Temp_IP={settings['ip']}, Temp_Port={settings['modbus_port']}, "
            f"TotalChannels={settings['total_channels']}, ScalingFactor={settings['scaling_factor']}, "
            f"ParallelBatteries={settings['number_of_parallel_batteries']}, SlaveAddresses={settings['modbus_slave_addresses']}"
        )
        if y < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y, 0, "Step 1: Validating config...", curses.color_pair(4))
            except curses.error:
                logging.warning("addstr error for step 1.")
        stdscr.refresh()
        time.sleep(0.5)
        # Assume passed (validate_config already called).
        logging.debug("Configuration validation passed.")
        if y + 1 < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y + 1, 0, "Config OK.", curses.color_pair(4))
            except curses.error:
                logging.warning("addstr error for config OK.")
        y += 2
        stdscr.refresh()
        # Step 2: Hardware.
        logging.info("Step 2: Testing hardware connectivity (I2C and Modbus per slave).")
        if y < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y, 0, "Step 2: Testing hardware connectivity...", curses.color_pair(4))
            except curses.error:
                logging.warning("addstr error for step 2.")
        stdscr.refresh()
        time.sleep(0.5)
        logging.debug(f"Testing I2C connectivity on bus {settings['I2C_BusNumber']}: "
                      f"Multiplexer=0x{settings['MultiplexerAddress']:02x}, "
                      f"VoltageMeter=0x{settings['VoltageMeterAddress']:02x}")
        try:
            if bus:
                logging.debug(f"Selecting I2C channel 0 on multiplexer 0x{settings['MultiplexerAddress']:02x}")
                choose_channel(0, settings['MultiplexerAddress'])
                logging.debug(f"Reading byte from VoltageMeter at 0x{settings['VoltageMeterAddress']:02x}")
                bus.read_byte(settings['VoltageMeterAddress'])
                logging.debug("I2C connectivity test passed for voltage meter.")
            if y + 1 < stdscr.getmaxyx()[0]:
                try:
                    stdscr.addstr(y + 1, 0, "I2C OK.", curses.color_pair(4))
                except curses.error:
                    logging.warning("addstr error for I2C OK.")
        except (IOError, AttributeError) as e:
            alert = f"I2C connectivity failure: {str(e)}"
            alerts.append(alert)
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
            if len(event_log) > settings.get('EventLogSize', 20):
                event_log.pop(0)
            logging.error(f"I2C connectivity failure: {str(e)}. Bus={settings['I2C_BusNumber']}, "
                          f"Multiplexer=0x{settings['MultiplexerAddress']:02x}, "
                          f"VoltageMeter=0x{settings['VoltageMeterAddress']:02x}")
            if y + 1 < stdscr.getmaxyx()[0]:
                try:
                    stdscr.addstr(y + 1, 0, f"I2C failure: {str(e)}", curses.color_pair(2))
                except curses.error:
                    logging.warning("addstr error for I2C failure.")
        # Test Modbus per slave.
        y_test = y + 2
        for addr in settings['modbus_slave_addresses']:
            port_for_slave = get_port_for_slave(addr, settings['modbus_slave_addresses'], settings['modbus_slave_ports'], settings['modbus_port'])
            logging.info(f"Testing Modbus slave {addr} on port {port_for_slave} (config: {settings['modbus_slave_ports']})")
            logging.debug(f"Testing Modbus slave {addr} connectivity to {settings['ip']}:{port_for_slave} with num_channels=1")
            try:
                test_query = read_ntc_sensors(settings['ip'], port_for_slave, settings['query_delay'], 1, settings['scaling_factor'], 1, 1, slave_addr=addr, slave_ips=settings.get('modbus_slave_ips', []), slave_addresses=settings.get('modbus_slave_addresses', []))
                if isinstance(test_query, str) and "Error" in test_query:
                    raise ValueError(test_query)
                logging.debug(f"Modbus test successful for slave {addr}: Received {len(test_query)} values: {test_query}")
                if y_test < stdscr.getmaxyx()[0]:
                    try:
                        stdscr.addstr(y_test, 0, f"Modbus Slave {addr} OK.", curses.color_pair(4))
                    except curses.error:
                        logging.warning("addstr error for Modbus Slave {addr} OK.")
            except Exception as e:
                alert = f"Modbus Slave {addr} test failure: {str(e)}"
                alerts.append(alert)
                event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
                if len(event_log) > settings.get('EventLogSize', 20):
                    event_log.pop(0)
                logging.error(f"Modbus Slave {addr} test failure: {str(e)}. Connection={settings['ip']}:{port_for_slave}, "
                              f"num_channels=1, query_delay={settings['query_delay']}, scaling_factor={settings['scaling_factor']}")
                if y_test < stdscr.getmaxyx()[0]:
                    try:
                        stdscr.addstr(y_test, 0, f"Modbus Slave {addr} failure: {str(e)}", curses.color_pair(2))
                    except curses.error:
                        logging.warning("addstr error for Modbus Slave {addr} failure.")
            y_test += 1
            stdscr.refresh()
        y = y_test
        # Step 3: Initial reads.
        logging.info("Step 3: Performing initial sensor reads (temperature per slave and voltage).")
        if y < stdscr.getmaxyx()[0]:
            try:
                stdscr.addstr(y, 0, "Step 3: Initial sensor reads...", curses.color_pair(4))
            except curses.error:
                logging.warning("addstr error for step 3.")
        stdscr.refresh()
        time.sleep(0.5)
        # Temps.
        all_initial_temps = []
        temp_fail = False
        for addr in settings['modbus_slave_addresses']:
            port_for_slave = get_port_for_slave(addr, settings['modbus_slave_addresses'], settings['modbus_slave_ports'], settings['modbus_port'])
            initial_temps = read_ntc_sensors(settings['ip'], port_for_slave, settings['query_delay'],
                                              settings['sensors_per_battery'], settings['scaling_factor'],
                                              settings['max_retries'], settings['retry_backoff_base'], slave_addr=addr,
                                              slave_ips=settings.get('modbus_slave_ips', []),
                                              slave_addresses=settings.get('modbus_slave_addresses', []))
            if isinstance(initial_temps, str):
                alert = f"Initial temp read failure for slave {addr}: {initial_temps}"
                alerts.append(alert)
                event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
                if len(event_log) > settings.get('EventLogSize', 20):
                    event_log.pop(0)
                logging.error(f"Initial temperature read failure for slave {addr}: {initial_temps}")
                all_initial_temps.extend([settings['valid_min']] * settings['sensors_per_battery'])
                temp_fail = True
            else:
                logging.debug(f"Initial temperature read successful for slave {addr}: {len(initial_temps)} values, {initial_temps}")
                all_initial_temps.extend(initial_temps)
        # Display temp result.
        if temp_fail:
            if y + 1 < stdscr.getmaxyx()[0]:
                try:
                    stdscr.addstr(y + 1, 0, "Some temp read failures.", curses.color_pair(2))
                except curses.error:
                    logging.warning("addstr error for temp failure.")
        else:
            if y + 1 < stdscr.getmaxyx()[0]:
                try:
                    stdscr.addstr(y + 1, 0, "Temps OK.", curses.color_pair(4))
                except curses.error:
                    logging.warning("addstr error for temps OK.")
        # Voltages.
        initial_voltages = []
        for i in range(1, NUM_BANKS + 1):
            voltage, readings, adc_values = read_voltage_with_retry(i, settings)
            initial_voltages.append(voltage if voltage is not None else 0.0)
        # Check voltages.
        if any(v == 0.0 for v in initial_voltages):
            alert = "Initial voltage read failure: Zero voltage on one or more banks."
            alerts.append(alert)
            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
            if len(event_log) > settings.get('EventLogSize', 20):
                event_log.pop(0)
            logging.error(f"Initial voltage read failure: Voltages={initial_voltages}")
            if y + 2 < stdscr.getmaxyx()[0]:
                try:
                    stdscr.addstr(y + 2, 0, "Voltage read failure (zero).", curses.color_pair(2))
                except curses.error:
                    logging.warning("addstr error for voltage failure.")
        else:
            logging.debug(f"Initial voltage read successful: Voltages={initial_voltages}")
            if y + 2 < stdscr.getmaxyx()[0]:
                try:
                    stdscr.addstr(y + 2, 0, "Voltages OK.", curses.color_pair(4))
                except curses.error:
                    logging.warning("addstr error for voltages OK.")
        # Calibrate if all temps valid.
        if not temp_fail:
            valid_count = sum(1 for t in all_initial_temps if t is not None and t > settings['valid_min'])
            if valid_count == settings['total_channels']:
                startup_median = statistics.median(all_initial_temps)
                logging.debug(f"Calculated startup median: {startup_median:.1f}°C")
                _, startup_offsets = load_offsets(settings['total_channels'], data_dir)
                if startup_offsets is None:
                    startup_offsets = [startup_median - t for t in all_initial_temps]
                    # Warn if any offset differs by more than 10% from median
                    for i, offset in enumerate(startup_offsets):
                        if abs(offset) > abs(startup_median * 0.10):
                            logging.warning(f"Large offset detected: Sensor {i+1} offset {offset:.2f}C ({abs(offset)/abs(startup_median)*100:.1f}% of median {startup_median:.1f}C) - verify sensor is functioning correctly")
                    save_offsets(startup_median, startup_offsets, data_dir)
                    logging.info(f"Calculated and saved new offsets: {startup_offsets}")
                else:
                    logging.info(f"Using existing offsets: {startup_offsets}")
                startup_set = True
            else:
                logging.warning(f"Calibration skipped: Only {valid_count}/{settings['total_channels']} valid.")
                startup_median = None
                startup_offsets = None
                startup_set = False
        y += 3
        stdscr.refresh()
        # Step 4: Balancer test if no alerts and voltages OK.
        if not alerts and all(v > 0 for v in initial_voltages):
            logging.info("Step 4: Verifying balancer functionality.")
            if y < stdscr.getmaxyx()[0]:
                try:
                    stdscr.addstr(y, 0, "Step 4: Balancer verification...", curses.color_pair(4))
                except curses.error:
                    logging.warning("addstr error for step 4.")
            y += 1
            stdscr.refresh()
            time.sleep(0.5)
            # Initial voltages for test.
            initial_bank_voltages = []
            for bank in range(1, NUM_BANKS + 1):
                voltage, _, _ = read_voltage_with_retry(bank, settings)
                initial_bank_voltages.append(voltage if voltage is not None else 0.0)
            if y + 1 < stdscr.getmaxyx()[0]:
                try:
                    voltage_str = ", ".join([f"Bank {i+1}={v:.2f}V" if v is not None else f"Bank {i+1}=N/A" for i, v in enumerate(initial_bank_voltages)])
                    stdscr.addstr(y + 1, 0, f"Initial Bank Voltages: {voltage_str}", curses.color_pair(4))
                except curses.error:
                    logging.warning("addstr error for initial bank voltages.")
            voltage_debug = ", ".join([f"Bank {i+1}={v:.2f}V" if v is not None else f"Bank {i+1}=N/A" for i, v in enumerate(initial_bank_voltages)])
            logging.debug(f"Initial Bank Voltages: {voltage_debug}")
            y += 2
            stdscr.refresh()
            # Dict for sorting.
            bank_voltages_dict = {b: initial_bank_voltages[b-1] for b in range(1, NUM_BANKS + 1)}
            sorted_banks = sorted(bank_voltages_dict, key=bank_voltages_dict.get, reverse=True)
            # All possible pairs.
            pairs = []
            for source in sorted_banks:
                for dest in [b for b in range(1, NUM_BANKS + 1) if b != source]:
                    pairs.append((source, dest))
            # Test params.
            test_duration = settings['test_balance_duration']
            read_interval = settings['test_read_interval']
            min_delta = settings['min_voltage_delta']
            logging.debug(f"Balancer test parameters: test_duration={test_duration}s, "
                          f"read_interval={read_interval}s, min_voltage_delta={min_delta}V")
            # Test each pair.
            for source, dest in pairs:
                logging.debug(f"Testing balance from Bank {source} to Bank {dest}")
                if y < stdscr.getmaxyx()[0]:
                    try:
                        stdscr.addstr(y, 0, f"Testing balance from Bank {source} to Bank {dest} for {test_duration}s.", curses.color_pair(6))
                    except curses.error:
                        logging.warning("addstr error for testing balance.")
                stdscr.refresh()
                logging.info(f"Testing balance from Bank {source} to Bank {dest} for {test_duration}s.")

                # Check temps for anomalies.
                temp_anomaly = False
                if all_initial_temps:
                    for t in all_initial_temps:
                        if t is not None and (t > settings['high_threshold'] or t < settings['low_threshold']):
                            temp_anomaly = True
                            break
                if temp_anomaly:
                    warning = f"Skipping balance test from Bank {source} to Bank {dest}: Temp anomalies."
                    event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {warning}")
                    if len(event_log) > settings.get('EventLogSize', 20):
                        event_log.pop(0)
                    logging.warning(f"Skipping balance test from Bank {source} to Bank {dest}: Temperature anomalies detected.")
                    if y + 1 < stdscr.getmaxyx()[0]:
                        try:
                            stdscr.addstr(y + 1, 0, "Skipped: Temp anomalies.", curses.color_pair(2))
                        except curses.error:
                            logging.warning("addstr error for skipped temp.")
                    y += 2
                    stdscr.refresh()
                    continue
                # Initial for test.
                initial_source_v = read_voltage_with_retry(source, settings)[0] or 0.0
                initial_dest_v = read_voltage_with_retry(dest, settings)[0] or 0.0
                time.sleep(0.5)
                logging.debug(f"Balance test from Bank {source} to Bank {dest}: Initial - Bank {source}={initial_source_v:.2f}V, Bank {dest}={initial_dest_v:.2f}V")
                # Check source voltage is high enough for DC-DC converter to operate
                min_src_voltage = settings.get('min_balance_source_voltage', 17.0)
                if initial_source_v < min_src_voltage:
                    # Turn off any relays that might be on
                    control_dcdc_converter(False, settings)
                    time.sleep(0.2)
                    set_relay_connection(0, 0, settings)
                    # Log as warning, NOT as failure - DC-DC just can't operate at this voltage
                    warning = f"Skipped: Bank {source} voltage {initial_source_v:.2f}V < {min_src_voltage:.1f}V minimum (DC-DC won't start)."
                    event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: Skipped balance test from Bank {source} to Bank {dest}: Source voltage {initial_source_v:.2f}V < {min_src_voltage:.1f}V.")
                    if len(event_log) > settings.get('EventLogSize', 20):
                        event_log.pop(0)
                    logging.warning(f"Skipping balance test from Bank {source} to Bank {dest}: Source voltage too low ({initial_source_v:.2f}V < {min_src_voltage:.1f}V)")
                    if y + 1 < stdscr.getmaxyx()[0]:
                        try:
                            stdscr.addstr(y + 1, 0, warning, curses.color_pair(3))  # Yellow for warning
                        except curses.error:
                            logging.warning("addstr error for skipped low voltage.")
                    y += 2
                    stdscr.refresh()
                    continue
                # Start test balance.
                set_relay_connection(source, dest, settings)
                time.sleep(0.5)  # Allow relays to settle
                control_dcdc_converter(True, settings)
                start_time = time.time()
                source_trend = [initial_source_v]
                dest_trend = [initial_dest_v]
                progress_y = y + 1
                # Loop for duration.
                while time.time() - start_time < test_duration:
                    time.sleep(read_interval)
                    source_v = read_voltage_with_retry(source, settings)[0] or 0.0
                    dest_v = read_voltage_with_retry(dest, settings)[0] or 0.0
                    source_trend.append(source_v)
                    dest_trend.append(dest_v)
                    elapsed = time.time() - start_time
                    if elapsed + read_interval >= test_duration:
                        final_source_v = source_v
                        final_dest_v = dest_v
                        # Display progress BEFORE turning off converter
                        if progress_y < stdscr.getmaxyx()[0]:
                            try:
                                stdscr.addstr(progress_y, 0, " " * 80, curses.color_pair(6))
                                stdscr.addstr(progress_y, 0, f"Progress: {elapsed:.1f}s, Bank {source} {source_v:.2f}V, Bank {dest} {dest_v:.2f}V", curses.color_pair(6))
                            except curses.error:
                                logging.warning("addstr error in startup balance progress.")
                        stdscr.refresh()
                        logging.debug(f"Balance test from Bank {source} to Bank {dest}: Final - Bank {source}={final_source_v:.2f}V, Bank {dest}={final_dest_v:.2f}V")
                        # Now turn off converter and relays
                        control_dcdc_converter(False, settings)
                        time.sleep(0.5)  # Allow relays to settle
                        set_relay_connection(0, 0, settings)
                    else:
                        logging.debug(f"Balance test from Bank {source} to Bank {dest}: Bank {source}={source_v:.2f}V, Bank {dest}={dest_v:.2f}V")
                        if progress_y < stdscr.getmaxyx()[0]:
                            try:
                                stdscr.addstr(progress_y, 0, " " * 80, curses.color_pair(6))
                                stdscr.addstr(progress_y, 0, f"Progress: {elapsed:.1f}s, Bank {source} {source_v:.2f}V, Bank {dest} {dest_v:.2f}V", curses.color_pair(6))
                            except curses.error:
                                logging.warning("addstr error in startup balance progress.")
                        stdscr.refresh()
                
                if progress_y + 1 < stdscr.getmaxyx()[0]:
                    try:
                        stdscr.addstr(progress_y + 1, 0, "Analyzing...", curses.color_pair(6))
                    except curses.error:
                        logging.warning("addstr error for analyzing.")
                stdscr.refresh()
                # Analyze using weighted averages for more stable readings
                # This smooths out DC-DC converter pulsing/inrush current variations
                if len(source_trend) >= 3:
                    # Use weighted average for verification
                    avg_source_v = weighted_average(source_trend)
                    avg_dest_v = weighted_average(dest_trend)
                    
                    # Fallback to discrete if averaging fails
                    avg_source_v = avg_source_v if avg_source_v is not None else final_source_v
                    avg_dest_v = avg_dest_v if avg_dest_v is not None else final_dest_v
                    
                    # Calculate changes using averaged readings
                    source_change = avg_source_v - initial_source_v
                    dest_change = avg_dest_v - initial_dest_v
                    
                    # Also calculate discrete changes for logging/comparison
                    discrete_source_change = final_source_v - initial_source_v
                    discrete_dest_change = final_dest_v - initial_dest_v
                    
                    logging.debug(f"Balance test from Bank {source} to Bank {dest} analysis: "
                                f"Initial={initial_source_v:.2f}V, Final={final_source_v:.2f}V, "
                                f"Averaged={avg_source_v:.3f}V | "
                                f"Discrete change: {discrete_source_change:+.3f}V, "
                                f"Averaged change: {source_change:+.3f}V | "
                                f"Min change={min_delta}V")
                    
                    # Check if source is decreasing and destination is increasing
                    # The key metric is the voltage differential between banks is reducing
                    # When balancing source->dest: differential = source - dest
                    # After transfer: differential should decrease
                    source_decreasing = source_change < 0
                    dest_increasing = dest_change > 0
                    diff_reduced = (avg_source_v - avg_dest_v) < (initial_source_v - initial_dest_v)
                    
                    # Pass if: source decreasing AND dest increasing AND differential reduced
                    if min_delta > 0 and not (source_decreasing and dest_increasing and diff_reduced):
                        # Additional check: discrete readings might show success while averaged fails
                        discrete_source_decreasing = discrete_source_change < 0
                        discrete_dest_increasing = discrete_dest_change > 0
                        discrete_diff_reduced = (final_source_v - final_dest_v) < (initial_source_v - initial_dest_v)
                        
                        if discrete_source_decreasing and discrete_dest_increasing and discrete_diff_reduced:
                            # Discrete shows success, averaged fails - likely converter pulsing
                            alert = (f"Balance test ambiguous: Discrete shows transfer working ({discrete_source_change:+.3f}V, {discrete_dest_change:+.3f}V) "
                                    f"but averaged shows marginal ({source_change:+.3f}V, {dest_change:+.3f}V). "
                                    f"NOT marking as failed - transfer is occurring.")
                            alerts.append(alert)
                            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
                            if len(event_log) > settings.get('EventLogSize', 20):
                                event_log.pop(0)
                            logging.warning(alert)
                            if progress_y + 1 < stdscr.getmaxyx()[0]:
                                try:
                                    stdscr.addstr(progress_y + 1, 0, f"Ambiguous: Discrete OK, averaged marginal ({source_change:+.3f}V, {dest_change:+.3f}V). Transfer OK.", curses.color_pair(3))
                                except curses.error:
                                    logging.warning("addstr error for ambiguous result.")
                        else:
                            alert = (f"Balance test from Bank {source} to Bank {dest} failed: "
                                    f"Source {source_change:+.3f}V, Dest {dest_change:+.3f}V. "
                                    f"Source dec={source_decreasing}, Dest inc={dest_increasing}, Diff reduced={diff_reduced}. "
                                    f"Possible relay failure.")
                            alerts.append(alert)
                            event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
                            if len(event_log) > settings.get('EventLogSize', 20):
                                event_log.pop(0)
                            logging.error(alert)
                            balancer_failed = True
                            balancer_failed_time = time.time()
                            balancer_fail_count += 1
                            balancer_fail_reason = alert
                            if progress_y + 1 < stdscr.getmaxyx()[0]:
                                try:
                                    stdscr.addstr(progress_y + 1, 0, f"Test FAILED: Source {source_change:+.3f}V, Dest {dest_change:+.3f}V. Diff {'reduced' if diff_reduced else 'not reduced'}.", curses.color_pair(2))
                                except curses.error:
                                    logging.warning("addstr error for test failed.")
                    else:
                        logging.debug(f"Balance test from Bank {source} to Bank {dest} passed: "
                                    f"Averaged change: {source_change:+.3f}V source, {dest_change:+.3f}V dest.")
                        if progress_y + 1 < stdscr.getmaxyx()[0]:
                            try:
                                stdscr.addstr(progress_y + 1, 0, f"Test PASSED: Averaged {source_change:+.3f}V, {dest_change:+.3f}V.", curses.color_pair(4))
                            except curses.error:
                                logging.warning("addstr error for test passed.")
                else:
                    alert = f"Balance test from Bank {source} to Bank {dest} failed: Insufficient readings."
                    alerts.append(alert)
                    event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {alert}")
                    if len(event_log) > settings.get('EventLogSize', 20):
                        event_log.pop(0)
                    logging.error(f"Balance test from Bank {source} to Bank {dest} failed: Only {len(source_trend)} readings collected.")
                    balancer_failed = True
                    balancer_failed_time = time.time()
                    balancer_fail_count += 1
                    balancer_fail_reason = alert
                    if progress_y + 1 < stdscr.getmaxyx()[0]:
                        try:
                            stdscr.addstr(progress_y + 1, 0, "Test failed: Insufficient readings.", curses.color_pair(2))
                        except curses.error:
                            logging.warning("addstr error for test failed insufficient readings.")
                stdscr.refresh()
                y = progress_y + 2
                time.sleep(2)
        # Set alerts (warnings from skipped tests are not in 'alerts' list)
        startup_alerts = alerts
        # Only count actual FAILED tests as failures - skipped tests are expected when voltage is low.
        actual_failures = alerts  # fix: low-voltage skips go to event_log only; keep all alerts
        # If actual failures exist, handle failure. If only skips, it's still a success.
        if actual_failures:
            startup_failed = True
            logging.error("Startup self-test failures: " + "; ".join(actual_failures))
            send_alert_email("Startup self-test failures:\n" + "\n".join(actual_failures), settings)
            if GPIO:
                GPIO.output(settings['AlarmRelayPin'], GPIO.HIGH)
            stdscr.clear()
            if stdscr.getmaxyx()[0] > 0:
                try:
                    failure_msg = "; ".join(actual_failures) if actual_failures else "No failures (skips due to low voltage)"
                    stdscr.addstr(0, 0, "Startup failures: " + failure_msg, curses.color_pair(2))
                except curses.error:
                    logging.warning("addstr error for self-test failures.")
            if stdscr.getmaxyx()[0] > 2:
                try:
                    stdscr.addstr(2, 0, f"Alarm activated. Retry {retries + 1}/{max_retries}...", curses.color_pair(2))
                except curses.error:
                    logging.warning("addstr error for retry message.")
            stdscr.refresh()
            # Update web.
            if actual_failures:
                web_data['system_status'] = f'Startup Self-Test Failed - Retry {retries + 1}/{max_retries}'
            else:
                web_data['system_status'] = f'Startup Self-Test Passed (skipped low-voltage tests)'
            web_data['alerts'] = startup_alerts
            web_data['last_update'] = time.time()
            retries += 1
            if retries >= max_retries:
                logging.warning("Max retries reached for startup self-test. Proceeding to main loop with startup_failed reset to False.")
                startup_failed = False # Reset to allow balancing
                break
            time.sleep(120) # Wait 2 minutes before retry
            continue
        else:
            # Success.
            startup_failed = False
            startup_alerts = []
            if GPIO:
                GPIO.output(settings['AlarmRelayPin'], GPIO.LOW)
            stdscr.clear()
            if stdscr.getmaxyx()[0] > 0:
                try:
                    stdscr.addstr(0, 0, "Self-Test Passed. Proceeding to main loop.", curses.color_pair(4))
                except curses.error:
                    logging.warning("addstr error for self-test OK.")
            web_data['system_status'] = 'Running'
            stdscr.refresh()
            time.sleep(2)
            logging.info("Startup self-test passed.")
            return []
    # If here, passed or max retries.
    return []



# ---------------------------------------------------------------------------
# Modbus TCP Server Functions for Victron Cerbo GX Integration
# ---------------------------------------------------------------------------

def create_modbus_datastore(num_banks):
    """
    Create Modbus datastore with holding registers for battery data.
    
    Register Map (Victron Cerbo GX compatible):
    Holding Registers (40001+):
    - 40001-40003: Bank voltages in centivolts (V * 100)
    - 40004: Total voltage in centivolts
    - 40005: Average temperature in tenths of degrees (C * 10)
    - 40006: Max temperature in tenths of degrees
    - 40007: Min temperature in tenths of degrees
    - 40008: System status flags (bitfield)
    - 40009: Alert count
    - 40010: Balancing status (0=off, 1=active)
    - 40011-40013: Bank median temperatures in tenths of degrees
    - 40014-40016: Bank min temperatures
    - 40017-40019: Bank max temperatures
    - 40020-40022: Bank invalid sensor counts
    - 40023: Number of series banks
    - 40024: Number of parallel batteries
    - 40025: Total sensor count
    - 40026: Valid sensor count
    - 40027-40029: Bank voltage low threshold (centivolts)
    - 40030-40032: Bank voltage high threshold (centivolts)
    
    Args:
        num_banks (int): Number of battery banks.
    
    Returns:
        ModbusDeviceContext: The datastore for the Modbus server.
    """
    if not MODBUS_SERVER_AVAILABLE:
        return None
    
    # Create holding register block (100 registers starting at address 0)
    # Each register is 16-bit unsigned (0-65535)
    holding_block = ModbusSequentialDataBlock(0, [0]*36001)
    
    # Create slave context with the holding register block
    slave_context = ModbusDeviceContext(
        di=ModbusSequentialDataBlock(0, [0]*100),  # Discrete inputs
        co=ModbusSequentialDataBlock(0, [0]*100),  # Coils
        hr=holding_block,                   # Holding registers
        ir=ModbusSequentialDataBlock(0, [0]*100)   # Input registers
    )
    
    # Create server context with single slave
    context = ModbusServerContext(devices=slave_context, single=True)

    # Safe defaults: AllowToCharge=1, AllowToDischarge=1
    # These must be 1 at startup so the Cerbo driver never reads
    # uninitialised zeros and blocks charging/discharging on boot.
    slave_context.setValues(3, 330, [1, 1])

    return context

def update_modbus_registers(settings):
    """Update Modbus holding registers with current battery data.
    Uses Victron Cerbo GX compatible register addresses.
    """
    global modbus_registers
    
    if not MODBUS_SERVER_AVAILABLE:
        return
    
    with data_lock:
        voltages = web_data["voltages"]
        temperatures = web_data["temperatures"]
        bank_summaries = web_data["bank_summaries"]
        alerts = web_data["alerts"]
        balancing = web_data["balancing"]
        system_status = web_data["system_status"]
        data_valid = web_data.get("data_valid", False)
    
    # Calculate values
    valid_temps = [t for t in temperatures if t is not None]
    avg_temp = sum(valid_temps) / len(valid_temps) if valid_temps else 0.0
    total_voltage = sum(voltages) if voltages else 0.0
    min_voltage = min(voltages) if voltages else 0.0
    max_voltage = max(voltages) if voltages else 0.0
    
    # Skip update if no real data available yet (boot initialisation)
    if not voltages or not data_valid:
        return
    
    # Build register values using Victron addresses
    registers = {}
    
    # Register 259: Battery voltage (centivolts) - Total voltage
    registers[259] = int(total_voltage * 100)
    # Register 260: Data-valid flag. 0 = Pi starting up / no data yet.
    # 1 = bms.py has real voltage data. Thin Cerbo driver checks this
    # before publishing so it never sends 0V to D-Bus during boot.
    registers[260] = 1
    
    # Register 261: Current (tenths of amps) - Unknown, set to 0
    registers[261] = 0
    
    # Register 262: Battery temperature (tenths of degrees)
    registers[262] = int(avg_temp * 10)
    
    # Register 266: State of charge (tenths of percent)
    soc = max(0, min(100, (total_voltage - 18.0 * settings["num_series_banks"]) / 
                      (21.5 - 18.0) * 100 / settings["num_series_banks"]))
    registers[266] = int(soc * 10)
    
    # Individual alarm registers (Victron standard)
    registers[268] = 0  # Low voltage alarm
    registers[269] = 0  # High voltage alarm
    registers[273] = 0  # Low temperature alarm
    registers[274] = 0  # High temperature alarm
    
    if alerts:
        for alert in alerts:
            alert_lower = alert.lower()
            if "low voltage" in alert_lower or "low_voltage" in alert_lower:
                registers[268] = 2  # Alarm
            if "high voltage" in alert_lower or "high_voltage" in alert_lower:
                registers[269] = 2  # Alarm
            if "low temp" in alert_lower or "low_temp" in alert_lower:
                registers[273] = 2  # Alarm
            if "high temp" in alert_lower or "high_temp" in alert_lower or "over temp" in alert_lower:
                registers[274] = 2  # Alarm
    
    # Register 1282: State (Victron values: 9=Running, 10=Error, 14=Standby)
    if system_status == "Running":
        registers[1282] = 9  # Running
    elif system_status == "Alert":
        registers[1282] = 10  # Error
    else:
        registers[1282] = 14  # Standby
    
    # Register 1286: System; number of batteries (series banks)
    registers[1286] = settings["num_series_banks"]
    
    # Register 1287: System; batteries parallel
    registers[1287] = settings["number_of_parallel_batteries"]
    
    # Register 1288: System; batteries series
    registers[1288] = settings["num_series_banks"]

    # Register 1289: System; number of cells per battery (= num_series_banks, each bank is one cell)
    registers[1289] = settings["num_series_banks"]
    
    # Register 1290: System; minimum cell voltage (centivolts)
    registers[1290] = int(min_voltage * 100)
    
    # Register 1291: System; maximum cell voltage (centivolts)
    registers[1291] = int(max_voltage * 100)

    # =========================================================================
    # Pylontech-compatible registers (addresses the Cerbo GX actually reads)
    # =========================================================================

    # Register 11: Number of modules (Pylontech)
    registers[11] = settings["num_series_banks"]

    # Register 4: Battery voltage (decivolts for Pylontech)
    registers[4] = int(total_voltage * 10)

    # Register 768-771: Solar charger compatible registers
    registers[768] = int(total_voltage * 100)  # Battery voltage centivolts
    registers[769] = 0  # Current
    registers[770] = int(total_voltage * 100)  # Battery voltage centivolts
    registers[771] = int(total_voltage * 100)  # Battery voltage centivolts

    # Register 5000: Pylontech module info
    registers[5000] = int(total_voltage * 100)

    # Register 5672: Pylontech extended info
    registers[5672] = int(total_voltage * 100)

    # Register 35168 (0x8960): Pylontech identification area
    # Cerbo GX expects Pylontech-specific values here
    # Register 35168: Number of modules (Pylontech standard)
    # Register 35169: Module voltage (centivolts)
    registers[35168] = settings["num_series_banks"]  # Number of battery modules
    registers[35169] = int(total_voltage * 100)  # Module voltage in centivolts
    
    # Additional Pylontech-compatible registers for device identification
    # These help the Cerbo GX identify this as a Pylontech-compatible battery
    registers[35170] = 1  # Module 1 online
    registers[35171] = 1  # Module 2 online
    registers[35172] = 1  # Module 3 online
    
    # =========================================================================
    # Battery string voltage registers (Victron standard)
    # The Cerbo GX reads these to get individual bank voltages
    # Register 1306: String 1 voltage (centivolts)
    # Register 1307: String 2 voltage (centivolts)
    # etc.
    # =========================================================================
    for i, voltage in enumerate(voltages):
        if i < 16:  # Max 16 strings supported
            registers[1306 + i] = int(voltage * 100)
    
    # DVCC limits (registers 305-308, Victron standard)
    # Reg 305: Max charge voltage - Feature 3: HV clamp overrides CVL
    if settings.get('_hv_clamp', False):
        _cerbo_cv = settings.get('_hv_clamped_cvl', settings.get('dvcc_max_charge_voltage', 61.0))
    else:
        _cerbo_cv = settings.get('dvcc_max_charge_voltage', 61.0) + settings.get('cable_drop_compensation', 0.0)
    registers[305] = int(min(63.0, _cerbo_cv) * 10)
    # Reg 306: Min discharge voltage with manual cable drop (Feature 4)
    _cerbo_blv = settings.get('dvcc_min_discharge_voltage', 49.5) - settings.get('discharge_cable_drop', 0.0)
    registers[306] = int(max(0, _cerbo_blv) * 10)
    # Reg 307: Max charge current - temp-derated effective current (Features 1+2)
    registers[307] = int(settings.get('_effective_charge_current', settings.get('dvcc_max_charge_current', 200.0)) * 10)
    # Reg 308: Max discharge current
    registers[308] = int(settings.get('dvcc_max_discharge_current', 200.0) * 10)

    # Bank temperature registers (318-329)
    # Victron standard: 318 = min cell temp, 319 = max cell temp (decicelsius)
    # Extended: 320-322 = bank 1/2/3 median temps, 323-325 = bank min, 326-328 = bank max
    if bank_summaries:
        all_mins = [s['min'] for s in bank_summaries if s['min'] != 0]
        all_maxs = [s['max'] for s in bank_summaries if s['max'] != 0]
        if all_mins:
            registers[318] = int(min(all_mins) * 10)  # Min cell temperature (decicelsius)
        if all_maxs:
            registers[319] = int(max(all_maxs) * 10)  # Max cell temperature (decicelsius)
        # Per-bank median temperatures (registers 320-322)
        for i, summary in enumerate(bank_summaries):
            if i < 3:
                registers[320 + i] = int(summary['median'] * 10)  # Bank median temp (decicelsius)
                registers[323 + i] = int(summary['min'] * 10)     # Bank min temp (decicelsius)
                registers[326 + i] = int(summary['max'] * 10)     # Bank max temp (decicelsius)

    # Registers 330-331: AllowToCharge / AllowToDischarge
    # Pi controls these based on alarm state so the thin Cerbo driver can
    # blindly publish them without needing local decision logic.
    high_v_alarm = any('high voltage' in a.lower() or 'high_voltage' in a.lower() for a in alerts)
    low_v_alarm  = any('low voltage'  in a.lower() or 'low_voltage'  in a.lower() for a in alerts)
    bms_error    = system_status in ('Error', 'Alert')
    registers[330] = 0 if (high_v_alarm or bms_error) else 1  # AllowToCharge
    registers[331] = 0 if (low_v_alarm  or bms_error) else 1  # AllowToDischarge

    # Store in global cache
    modbus_registers = registers
    
    return registers


def write_registers_to_datastore(context, registers):
    """
    Write register values to the Modbus datastore.
    
    Args:
        context: ModbusServerContext object.
        registers (dict): Dictionary of register address -> value.
    """
    if context is None:
        return
    
    try:
        # Get the slave context (single slave, ID=1)
        slave = context[1]
        
        # Write each register value
        for addr, value in registers.items():
            # Clamp value to valid 16-bit unsigned range
            value = max(0, min(65535, int(value)))
            slave.setValues(3, addr, [value])  # 3 = holding registers
    except Exception as e:
        logging.error(f"Error writing to Modbus datastore: {e}")

def modbus_server_thread(context, settings):
    """
    Background thread that runs the Modbus TCP server.
    Also periodically updates the register values.
    
    Args:
        context: ModbusServerContext object.
        settings (dict): Configuration settings.
    """
    global modbus_server_running
    
    if not MODBUS_SERVER_AVAILABLE:
        return
    
    # Server identity for Victron Cerbo GX
    identity = ModbusDeviceIdentification()
    identity.VendorName = 'BMS'
    identity.ProductCode = 'BMS001'
    identity.VendorUrl = 'https://github.com/erkel1/bms'
    identity.ProductName = 'Battery Management System'
    identity.ModelName = 'BMS Modbus Server'
    identity.MajorMinorRevision = '1.0.0'
    
    modbus_server_running = True
    logging.info(f"Modbus TCP server starting on port {settings['port']}")
    
    try:
        # Start the server (this blocks until stopped)
        StartTcpServer(
            context=context,
            identity=identity,
            address=('0.0.0.0', settings['port'])
        )
    except Exception as e:
        logging.error(f"Modbus server error: {e}")
    finally:
        modbus_server_running = False
        logging.info("Modbus TCP server stopped")

def modbus_updater_thread(context, settings):
    """
    Background thread that periodically updates Modbus register values.
    
    Args:
        context: ModbusServerContext object.
        settings (dict): Configuration settings.
    """
    global modbus_server_running
    
    if not MODBUS_SERVER_AVAILABLE:
        return
    
    update_interval = settings.get('update_interval', 1.0)
    
    while modbus_server_running:
        try:
            # Update registers with current data
            registers = update_modbus_registers(settings)
            
            # Write to datastore
            if registers and context:
                write_registers_to_datastore(context, registers)
            
            # Wait for next update
            time.sleep(update_interval)
        except Exception as e:
            logging.error(f"Error updating Modbus registers: {e}")
            time.sleep(1)


def start_mdns_advertisement(port, unit_id=1):
    """
    Start mDNS service advertisement for Victron Cerbo GX discovery.
    Uses avahi-publish-service to advertise the Modbus TCP service.
    
    Args:
        port (int): Modbus TCP port.
        unit_id (int): Modbus unit/slave ID.
    
    Returns:
        subprocess.Popen: The mDNS process or None if failed.
    """
    global mdns_process
    
    try:
        # Check if avahi-publish-service is available
        import shutil
        if not shutil.which("avahi-publish-service"):
            logging.warning("avahi-publish-service not found - mDNS advertisement disabled")
            return None
        
        # Kill any existing mDNS process
        if mdns_process and mdns_process.poll() is None:
            mdns_process.terminate()
            try:
                mdns_process.wait(timeout=5)
            except Exception:
                mdns_process.kill()
        
        # Start mDNS advertisement
        # Service type: _modbus._tcp (standard Modbus TCP service)
        # TXT records: device info for Victron compatibility
        txt_records = [
            f"unit_id={unit_id}",
            "device_type=battery",
            "manufacturer=BMS",
            "model=Battery Management System"
        ]
        
        cmd = [
            "avahi-publish-service",
            "-s",
            "BMS Battery Monitor",  # Service name
            "_modbus._tcp",  # Service type
            str(port)  # Port
        ] + txt_records
        
        mdns_process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        logging.info(f"mDNS service advertisement started: _modbus._tcp on port {port}")
        return mdns_process
        
    except Exception as e:
        logging.error(f"Failed to start mDNS advertisement: {e}")
        return None

def stop_mdns_advertisement():
    """
    Stop the mDNS service advertisement.
    """
    global mdns_process
    
    if mdns_process and mdns_process.poll() is None:
        try:
            mdns_process.terminate()
            mdns_process.wait(timeout=5)
            logging.info("mDNS service advertisement stopped")
        except Exception as e:
            logging.error(f"Error stopping mDNS advertisement: {e}")
            try:
                mdns_process.kill()
            except:
                pass
    mdns_process = None

def start_modbus_server(settings):
    """
    Start the Modbus TCP server for Victron Cerbo GX integration.
    Creates the datastore and starts server + updater threads.
    
    Args:
        settings (dict): Configuration settings.
    """
    global modbus_datastore, modbus_server_running
    
    if not MODBUS_SERVER_AVAILABLE:
        logging.warning("pymodbus not available - Modbus TCP server disabled")
        return
    
    if not settings.get('enabled', True):
        logging.info("Modbus TCP server disabled via configuration")
        return
    
    # Create the datastore
    modbus_datastore = create_modbus_datastore(settings['num_series_banks'])
    modbus_server_running = True
    
    if modbus_datastore is None:
        logging.error("Failed to create Modbus datastore")
        return
    
    # Start the updater thread
    updater_thread = threading.Thread(
        target=modbus_updater_thread,
        args=(modbus_datastore, settings),
        daemon=True
    )
    updater_thread.start()
    logging.info("Modbus register updater thread started")
    
    # Start the server thread
    server_thread = threading.Thread(
        target=modbus_server_thread,
        args=(modbus_datastore, settings),
        daemon=True
    )
    server_thread.start()
    logging.info(f"Modbus TCP server thread started on port {settings['port']}")

    # Store thread refs so the watchdog can monitor and restart them
    settings['_modbus_server_thread']  = server_thread
    settings['_modbus_updater_thread'] = updater_thread
    settings['_modbus_context']        = modbus_datastore

    # Modbus server watchdog -- mirrors the Flask web watchdog.
    # If the server thread dies both threads are restarted (10 s delay
    # for OS port release). If only the updater dies it restarts alone.
    def _modbus_watchdog():
        import time as _mwt
        _mwt.sleep(60)          # grace period on startup
        while True:
            try:
                _srv = settings.get('_modbus_server_thread')
                _upd = settings.get('_modbus_updater_thread')
                _ctx = settings.get('_modbus_context')
                if _srv is not None and not _srv.is_alive():
                    logging.warning('Modbus watchdog: server thread died -- '
                                    'waiting 10 s for OS port release, then restarting both threads')
                    _mwt.sleep(10)
                    global modbus_server_running
                    modbus_server_running = True
                    _new_upd = threading.Thread(
                        target=modbus_updater_thread, args=(_ctx, settings), daemon=True)
                    _new_upd.start()
                    settings['_modbus_updater_thread'] = _new_upd
                    _new_srv = threading.Thread(
                        target=modbus_server_thread, args=(_ctx, settings), daemon=True)
                    _new_srv.start()
                    settings['_modbus_server_thread'] = _new_srv
                    _mwt.sleep(5)
                    if _new_srv.is_alive():
                        logging.info('Modbus watchdog: server thread restarted successfully.')
                    else:
                        logging.error('Modbus watchdog: server thread restart FAILED.')
                elif _upd is not None and not _upd.is_alive():
                    logging.warning('Modbus watchdog: updater thread died -- restarting updater only')
                    _new_upd = threading.Thread(
                        target=modbus_updater_thread, args=(_ctx, settings), daemon=True)
                    _new_upd.start()
                    settings['_modbus_updater_thread'] = _new_upd
                    logging.info('Modbus watchdog: updater thread restarted.')
            except Exception as _e:
                logging.error(f'Modbus watchdog error: {_e}')
            _mwt.sleep(30)

    _modbus_wd = threading.Thread(target=_modbus_watchdog, name='modbus-watchdog', daemon=True)
    _modbus_wd.start()
    logging.info('Modbus server watchdog started.')

    # Start mDNS service advertisement for Victron Cerbo GX discovery
    start_mdns_advertisement(settings['port'], settings.get('unit_id', 1))


def start_web_server(settings):
    """
    Start the Flask web server in a separate thread.
    Defines routes: / for dashboard HTML (with Chart.js), /api/status for data, /api/history for RRD, /api/balance for manual trigger.
    Includes auth and CORS if enabled. Dynamic JS for charts based on num_banks. Non-programmer: Like setting up a website
    on your Pi that shows battery status and graphs in a browser.
    
    Args:
        settings (dict): Web config (host, port, auth, etc.).
    
    Returns:
        None: Starts thread.
    """
    # Global.
    global web_server
    # Skip if disabled.
    if not settings['WebInterfaceEnabled']:
        logging.info("Web interface disabled via configuration.")
        return
    # Skip if Flask missing.
    if Flask is None:
        logging.warning("Flask not available - web interface cannot start.")
        return
    # Create app.
    app = Flask(__name__)
    # Route for main page.
    @app.route('/chart.min.js')
    def serve_chartjs():
        import os as _os
        _path = _os.path.join(settings.get('data_dir', '/projects/battery_balancer'), 'chart.min.js')
        if _os.path.exists(_path):
            with open(_path, 'r') as _f:
                return _f.read(), 200, {'Content-Type': 'application/javascript'}
        return '// Chart.js not found', 200, {'Content-Type': 'application/javascript'}

    @app.route('/')
    def index():
        # Dynamic datasets for charts.
        colors = ['green', 'blue', 'red', 'orange', 'purple', 'brown', 'pink', 'gray']
        datasets_list = []
        for i in range(1, settings['num_series_banks'] + 1):
            color = colors[(i-1) % len(colors)]
            datasets_list.append(f"{{ label: 'Bank {i} V', data: hist.map(h => h.volt{i}), borderColor: '{color}' }}")
        datasets_list.append("{ label: 'Median Temp °C', data: hist.map(h => h.medtemp), borderColor: 'cyan', yAxisID: 'temp' }")
        datasets_array = ',\n'.join(datasets_list)
        logging.debug(f"Constructed datasets_array: {datasets_array}")
        # Full HTML with modern design, dark-mode by default, Chart.js charts.
        html = f"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BMS Dashboard</title>
    <!-- Google Fonts removed: self-hosted fallback -->
    <script src="/chart.min.js"></script>
    <style>
        :root {{
            --bg:#080f1a;--bg2:#0d1829;--card:#111d2e;--card2:#16243a;
            --fg:#e2e8f0;--fg2:#7a8ba0;--fg3:#4a5c6e;
            --acc:#3b82f6;--acc2:#2563eb;--acc-glow:rgba(59,130,246,.25);
            --ok:#22c55e;--ok-glow:rgba(34,197,94,.2);
            --warn:#f59e0b;--warn-glow:rgba(245,158,11,.2);
            --bad:#ef4444;--bad-glow:rgba(239,68,68,.2);
            --border:#1e3044;--border2:#243650;
            --sh:0 4px 24px rgba(0,0,0,.5);--sh2:0 8px 40px rgba(0,0,0,.6);
            --r:14px;--r2:9px;--r3:6px;
        }}
        [data-theme=light] {{
            --bg:#f0f4f8;--bg2:#e4ecf4;--card:#ffffff;--card2:#f4f8fc;
            --fg:#0f1e2d;--fg2:#4a6072;--fg3:#8ba0b0;
            --acc:#2563eb;--acc2:#1d4ed8;--acc-glow:rgba(37,99,235,.15);
            --ok:#16a34a;--ok-glow:rgba(22,163,74,.12);
            --warn:#d97706;--warn-glow:rgba(217,119,6,.12);
            --bad:#dc2626;--bad-glow:rgba(220,38,38,.12);
            --border:#d0dae4;--border2:#c0d0de;
            --sh:0 2px 16px rgba(0,0,0,.08);--sh2:0 4px 24px rgba(0,0,0,.1);
        }}
        *{{box-sizing:border-box;margin:0;padding:0}}
        html{{scroll-behavior:smooth}}
        body{{font-family:'Inter',system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--fg);min-height:100vh;line-height:1.5;transition:background .3s,color .3s}}
        /* ── Topbar ── */
        .topbar{{background:var(--bg2);border-bottom:1px solid var(--border);padding:0 28px;display:flex;align-items:center;justify-content:space-between;height:62px;position:sticky;top:0;z-index:200;backdrop-filter:blur(12px)}}
        .logo{{display:flex;align-items:center;gap:10px;font-size:1.05rem;font-weight:800;letter-spacing:-.3px}}
        .logo-icon{{width:30px;height:30px;background:linear-gradient(135deg,var(--acc),#7c3aed);border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:.9rem;box-shadow:0 0 12px var(--acc-glow)}}
        .badge{{display:inline-flex;align-items:center;gap:6px;padding:5px 13px;border-radius:999px;font-size:.75rem;font-weight:600;letter-spacing:.01em}}
        .badge.ok{{background:var(--ok-glow);color:var(--ok);border:1px solid rgba(34,197,94,.3)}}
        .badge.bad{{background:var(--bad-glow);color:var(--bad);border:1px solid rgba(239,68,68,.3)}}
        .badge.idle{{background:rgba(122,139,160,.1);color:var(--fg2);border:1px solid var(--border)}}
        .dot{{width:7px;height:7px;border-radius:50%;background:currentColor;flex-shrink:0}}
        .dot.pulse{{animation:pulse 2s infinite}}
        @keyframes pulse{{0%,100%{{opacity:1;transform:scale(1)}}50%{{opacity:.4;transform:scale(.75)}}}}
        .topbar-r{{display:flex;align-items:center;gap:8px}}
        .ts{{font-size:.76rem;color:var(--fg2);white-space:nowrap}}
        .btn{{display:inline-flex;align-items:center;gap:5px;padding:7px 14px;border-radius:var(--r3);border:1px solid var(--border);background:transparent;color:var(--fg2);font-size:.8rem;font-weight:500;cursor:pointer;transition:all .15s;font-family:inherit}}
        .btn:hover{{border-color:var(--acc);color:var(--acc)}}
        .btn:disabled{{opacity:.3;cursor:not-allowed}}
        .btn.p{{background:var(--acc);border-color:var(--acc);color:#fff;box-shadow:0 0 12px var(--acc-glow)}}.btn.p:hover{{background:var(--acc2);border-color:var(--acc2)}}
        .btn.danger{{background:var(--bad-glow);border-color:rgba(239,68,68,.3);color:var(--bad)}}.btn.danger:hover{{background:rgba(239,68,68,.2)}}
        /* Cerbo GX toggle */
        .cerbo-toggle{{display:flex;align-items:center;gap:8px;padding:0 4px}}
        .cerbo-label{{font-size:.76rem;font-weight:600;color:var(--fg2);white-space:nowrap}}
        .sw{{position:relative;width:40px;height:22px;flex-shrink:0}}
        .sw input{{opacity:0;width:0;height:0;position:absolute}}
        .sw-track{{position:absolute;inset:0;border-radius:999px;background:var(--border2);border:1px solid var(--border);cursor:pointer;transition:background .2s,border-color .2s}}
        .sw-track::after{{content:'';position:absolute;top:2px;left:2px;width:16px;height:16px;border-radius:50%;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.4);transition:transform .2s}}
        .sw input:checked+.sw-track{{background:var(--ok);border-color:var(--ok)}}
        .sw input:checked+.sw-track::after{{transform:translateX(18px)}}
        .sw input:disabled+.sw-track{{opacity:.5;cursor:not-allowed}}
        .cerbo-status{{font-size:.72rem;font-weight:700;min-width:36px}}
        .cerbo-status.on{{color:var(--ok)}}.cerbo-status.off{{color:var(--bad)}}
        /* ── Layout ── */
        .wrap{{max-width:1440px;margin:0 auto;padding:24px 28px}}
        @media(max-width:768px){{.wrap{{padding:16px}}}}
        /* ── Section header ── */
        .sh{{display:flex;align-items:center;gap:10px;font-size:.7rem;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:var(--fg3);margin-bottom:16px}}
        .sh::after{{content:"";flex:1;height:1px;background:var(--border)}}
        /* ── Metric row ── */
        .mrow{{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:14px;margin-bottom:28px}}
        .mc{{background:var(--card);border:1px solid var(--border);border-radius:var(--r);padding:20px;box-shadow:var(--sh);position:relative;overflow:hidden;transition:border-color .2s}}
        .mc::before{{content:"";position:absolute;top:0;left:0;right:0;height:2px;background:var(--accent-line,var(--acc));opacity:.6}}
        .mc.ok-line{{--accent-line:var(--ok)}}.mc.warn-line{{--accent-line:var(--warn)}}.mc.bad-line{{--accent-line:var(--bad)}}
        .ml{{font-size:.67rem;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--fg3);margin-bottom:8px}}
        .mv{{font-size:1.7rem;font-weight:800;line-height:1;letter-spacing:-.02em}}.mv.ok{{color:var(--ok)}}.mv.warn{{color:var(--warn)}}.mv.bad{{color:var(--bad)}}
        .ms{{font-size:.73rem;color:var(--fg2);margin-top:5px}}
        /* ── Bank cards ── */
        .bg{{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:16px;margin-bottom:28px}}
        .bcard{{background:var(--card);border:1px solid var(--border);border-radius:var(--r);padding:20px;box-shadow:var(--sh);transition:border-color .25s,box-shadow .25s}}
        .bcard:hover{{border-color:var(--border2);box-shadow:var(--sh2)}}
        .bcard.alert{{border-color:rgba(239,68,68,.4);box-shadow:0 0 20px rgba(239,68,68,.1)}}
        .bhdr{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:16px}}
        .bname{{font-size:.9rem;font-weight:700;color:var(--fg)}}.bsub{{font-size:.68rem;color:var(--fg3);margin-top:2px}}
        .bv{{font-size:2.1rem;font-weight:800;letter-spacing:-.03em;line-height:1}}.bv.ok{{color:var(--ok)}}.bv.warn{{color:var(--warn)}}.bv.bad{{color:var(--bad)}}
        .vtrack{{height:6px;background:var(--border);border-radius:3px;margin:12px 0;overflow:hidden}}
        .vfill{{height:100%;border-radius:3px;transition:width .6s cubic-bezier(.4,0,.2,1)}}.vfill.ok{{background:linear-gradient(90deg,var(--ok),#4ade80)}}.vfill.warn{{background:linear-gradient(90deg,var(--warn),#fcd34d)}}.vfill.bad{{background:linear-gradient(90deg,var(--bad),#f87171)}}
        .btemps{{display:flex;gap:16px;font-size:.75rem;color:var(--fg2);margin-bottom:12px;flex-wrap:wrap}}
        .btemps>span{{display:flex;flex-direction:column;gap:2px}}
        .btemps .lbl{{font-size:.63rem;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:var(--fg3)}}
        .btemps .val{{font-size:.9rem;font-weight:600;color:var(--fg)}}.btemps .val.warn{{color:var(--warn)}}.btemps .val.bad{{color:var(--bad)}}
        .sgrid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(44px,1fr));gap:3px;margin-top:4px}}
        .sc{{padding:4px 2px;border-radius:5px;font-size:.67rem;text-align:center;border:1px solid transparent;background:rgba(122,139,160,.05);font-weight:500;transition:background .2s}}
        .sc.ok{{border-color:rgba(34,197,94,.2);color:var(--ok);background:rgba(34,197,94,.05)}}
        .sc.warm{{border-color:rgba(245,158,11,.3);color:var(--warn);background:rgba(245,158,11,.07)}}
        .sc.hot{{border-color:rgba(239,68,68,.35);color:var(--bad);background:rgba(239,68,68,.08)}}
        .sc.null{{color:var(--fg3);font-style:italic}}
        /* ── Two-col ── */
        .two{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:28px}}
        @media(max-width:900px){{.two{{grid-template-columns:1fr}}}}
        .card{{background:var(--card);border:1px solid var(--border);border-radius:var(--r);padding:20px;box-shadow:var(--sh)}}
        /* ── Alerts ── */
        .alist{{display:flex;flex-direction:column;gap:8px}}
        .ai{{display:flex;align-items:flex-start;gap:10px;padding:10px 14px;border-radius:var(--r2);font-size:.82rem;background:var(--bad-glow);border:1px solid rgba(239,68,68,.25);color:#fca5a5;line-height:1.4}}
        .ai-icon{{flex-shrink:0;margin-top:1px}}
        .noa{{display:flex;align-items:center;gap:8px;padding:11px 15px;border-radius:var(--r2);background:var(--ok-glow);border:1px solid rgba(34,197,94,.2);color:var(--ok);font-size:.82rem;font-weight:500}}
        .acts{{display:flex;gap:10px;flex-wrap:wrap;margin-top:16px;padding-top:16px;border-top:1px solid var(--border)}}
        /* ── Comm stats ── */
        .ct{{width:100%;border-collapse:collapse;font-size:.8rem}}
        .ct th{{text-align:left;padding:8px 10px;border-bottom:2px solid var(--border);color:var(--fg3);font-weight:600;font-size:.68rem;text-transform:uppercase;letter-spacing:.06em}}
        .ct td{{padding:8px 10px;border-bottom:1px solid var(--border)}}.ct tr:last-child td{{border-bottom:none}}
        .ct tr:hover td{{background:rgba(255,255,255,.02)}}
        .rbar{{display:flex;align-items:center;gap:7px}}.rmini{{flex:1;height:5px;background:var(--border);border-radius:3px;overflow:hidden}}.rfill{{height:100%;border-radius:3px;transition:width .4s}}
        /* ── Chart ── */
        .chart-wrap{{position:relative;height:290px;margin-top:4px}}
        /* ── FAB ── */
        .fab{{position:fixed;bottom:20px;right:20px;width:44px;height:44px;display:flex;align-items:center;justify-content:center;cursor:pointer;z-index:300;filter:drop-shadow(0 2px 8px rgba(0,0,0,.4))}}
        .ring{{position:absolute;inset:0}}.rc{{font-size:.68rem;font-weight:700;color:var(--fg2);line-height:1}}
        /* ── Flash animation ── */
        @keyframes flashOk{{0%{{background:rgba(34,197,94,.15)}}100%{{background:transparent}}}}
        @keyframes flashBad{{0%{{background:rgba(239,68,68,.15)}}100%{{background:transparent}}}}
        .flash-ok{{animation:flashOk .5s ease-out}}
        .flash-bad{{animation:flashBad .5s ease-out}}
        /* ── Spinner ── */
        .spin{{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--acc);animation:pulse 1s infinite}}
        /* ── Balancing indicator ── */
        .bal-active{{color:var(--acc);font-weight:700}}
        /* ── Divider ── */
        .div{{height:1px;background:var(--border);margin:4px 0 16px}}
    </style>
</head>
<body>
<div class="topbar">
    <div style="display:flex;align-items:center;gap:14px">
        <div class="logo"><div class="logo-icon">⚡</div>BMS Dashboard</div>
        <span class="badge idle" id="sbadge"><span class="dot pulse" id="sbdot"></span><span id="stext">Connecting…</span></span>
    </div>
    <div class="topbar-r">
        <span class="ts" id="lupd"></span>
        <div class="cerbo-toggle" title="Enable / disable Cerbo GX dbus-bms-battery service">
            <span class="cerbo-label">Cerbo GX</span>
            <label class="sw"><input type="checkbox" id="cerbo-toggle"><span class="sw-track"></span></label>
            <span class="cerbo-status" id="cerbo-status">…</span>
        </div>
        <button class="btn" id="theme-btn" title="Toggle theme">◑</button>
        <button class="btn p" id="refresh-btn">↻ Refresh</button>
    </div>
</div>
<div class="wrap">
    <!-- Metric Summary Row -->
    <div class="mrow" id="metric-row">
        <div class="mc ok-line" id="mc-tv"><div class="ml">Total Voltage</div><div class="mv ok" id="tv">—</div><div class="ms" id="tv-sub">All banks combined</div></div>
        <div class="mc" id="mc-bm"><div class="ml">Balancing</div><div class="mv ok" id="bm">—</div><div class="ms" id="bms2">—</div></div>
        <div class="mc ok-line" id="mc-ac"><div class="ml">Active Alerts</div><div class="mv ok" id="ac">—</div><div class="ms">System health</div></div>
        <div class="mc ok-line" id="mc-at"><div class="ml">Avg Temperature</div><div class="mv ok" id="at">—</div><div class="ms">All sensors</div></div>
        <div class="mc ok-line" id="mc-cv"><div class="ml">Charge Voltage</div><div class="mv ok" id="cv-display">—</div><div class="ms" style="display:flex;flex-direction:column;gap:5px"><div style="display:flex;align-items:center;gap:4px"><span style="font-size:.7rem;color:var(--fg2);min-width:44px">Target</span><input type="number" id="cv-input" min="0.1" max="63" step="0.1" style="width:58px;background:var(--surface2);color:var(--fg);border:1px solid var(--border);border-radius:4px;padding:2px 4px;font-size:.75rem" title="Desired voltage at battery terminals"><span style="font-size:.7rem;color:var(--fg3)">V</span><button onclick="setChargeVoltage()" style="padding:2px 7px;font-size:.72rem;background:var(--acc);color:#fff;border:none;border-radius:4px;cursor:pointer">Set</button></div><div style="font-size:.7rem;color:var(--fg2)">Drop: <span id="cv-drop-display" style="color:var(--fg)">—</span> <span style="color:var(--fg3)">(auto)</span> &nbsp;→&nbsp; <span id="cv-cerbo-display" style="color:var(--warn)">—</span> to Cerbo <span id="cv-cap-warn" style="display:none;color:var(--bad);font-size:.7rem" title="Target voltage is unreachable - increase target or reduce cable resistance">⚠ CAP</span></div></div></div>
    </div>
    <!-- Charge State + DVCC Settings -->
    <div id="mc-cs" class="mc ok-line" style="display:none"><div class="ml">Charge State</div><div class="mv ok" id="cs-display">—</div><div class="ms" id="cs-sub">Effective: <span id="cs-eff">—</span>A</div></div>
    <div class="card" style="margin-bottom:16px" id="dvcc-card">
        <div class="sh">DVCC Settings</div>
        <div style="display:flex;flex-wrap:wrap;gap:18px;padding:4px 0 8px 0">
            <div style="display:flex;flex-direction:column;gap:4px;min-width:160px">
                <label style="font-size:.75rem;color:var(--fg2)">Max Charge Current (A)</label>
                <div style="display:flex;align-items:center;gap:6px">
                    <input type="number" id="dvcc-mcc" min="0" max="1000" step="1" style="width:70px;background:var(--card2);color:var(--fg);border:1px solid var(--border);border-radius:4px;padding:3px 6px;font-size:.82rem">
                    <span style="font-size:.72rem;color:var(--fg3)">(eff: <span id="dvcc-eff-cc">—</span>A)</span>
                </div>
            </div>
            <div style="display:flex;flex-direction:column;gap:4px;min-width:160px">
                <label style="font-size:.75rem;color:var(--fg2)">Max Discharge Current (A)</label>
                <input type="number" id="dvcc-mdc" min="0" max="1000" step="1" style="width:70px;background:var(--card2);color:var(--fg);border:1px solid var(--border);border-radius:4px;padding:3px 6px;font-size:.82rem">
            </div>
            <div style="display:flex;flex-direction:column;gap:4px;min-width:160px">
                <label style="font-size:.75rem;color:var(--fg2)">Min Discharge Voltage (V)</label>
                <input type="number" id="dvcc-mdv" min="0" max="63" step="0.1" style="width:70px;background:var(--card2);color:var(--fg);border:1px solid var(--border);border-radius:4px;padding:3px 6px;font-size:.82rem">
            </div>
            <div style="display:flex;flex-direction:column;gap:4px;min-width:160px">
                <label style="font-size:.75rem;color:var(--fg2)">Discharge Cable Drop (V)</label>
                <input type="number" id="dvcc-dcd" min="0" max="5" step="0.01" style="width:70px;background:var(--card2);color:var(--fg);border:1px solid var(--border);border-radius:4px;padding:3px 6px;font-size:.82rem">
            </div>
            <div style="display:flex;flex-direction:column;gap:4px;min-width:160px">
                <label style="font-size:.75rem;color:var(--fg2)">Temp Derate Start (°C)</label>
                <input type="number" id="dvcc-tds" min="20" max="60" step="1" style="width:70px;background:var(--card2);color:var(--fg);border:1px solid var(--border);border-radius:4px;padding:3px 6px;font-size:.82rem">
            </div>
            <div style="display:flex;flex-direction:column;gap:4px;min-width:160px">
                <label style="font-size:.75rem;color:var(--fg2)">Temp Derate End (°C)</label>
                <input type="number" id="dvcc-tde" min="20" max="70" step="1" style="width:70px;background:var(--card2);color:var(--fg);border:1px solid var(--border);border-radius:4px;padding:3px 6px;font-size:.82rem">
            </div>
            <div style="display:flex;flex-direction:column;gap:4px;min-width:160px">
                <label style="font-size:.75rem;color:var(--fg2)">Cold Cutoff Temp (°C)</label>
                <input type="number" id="dvcc-cco" min="-10" max="20" step="0.5" style="width:70px;background:var(--card2);color:var(--fg);border:1px solid var(--border);border-radius:4px;padding:3px 6px;font-size:.82rem">
            </div>
            <div style="display:flex;flex-direction:column;gap:4px;min-width:160px">
                <label style="font-size:.75rem;color:var(--fg2)">Cold Stop Temp (°C)</label>
                <input type="number" id="dvcc-cst" min="-10" max="15" step="0.5" style="width:70px;background:var(--card2);color:var(--fg);border:1px solid var(--border);border-radius:4px;padding:3px 6px;font-size:.82rem">
            </div>
            <div style="display:flex;align-items:flex-end;gap:8px">
                <button onclick="saveDvccSettings()" style="padding:5px 14px;font-size:.8rem;background:var(--acc);color:#fff;border:none;border-radius:5px;cursor:pointer">Save</button>
                <span id="dvcc-status" style="font-size:.75rem;color:var(--fg2)"></span>
            </div>
        </div>
        <div style="font-size:.72rem;color:var(--fg3);margin-top:2px">HV clamp: <span id="dvcc-hv-clamp" style="color:var(--fg2)">No</span>&nbsp;|&nbsp;Cerbo BLV: <span id="dvcc-blv">—</span>V</div>
    </div>
    <!-- Battery Banks -->
    <div class="sh">Battery Banks</div>
    <div class="bg" id="battery-container"></div>
    <!-- Alerts + Comm -->
    <div class="two">
        <div class="card">
            <div class="sh">Alerts</div>
            <div id="alerts-container"><div class="noa">⟳ Loading…</div></div>
            <div class="acts">
                <button class="btn danger" id="balance-btn" disabled>⚡ Balance Now</button>
            </div>
        </div>
        <div class="card">
            <div class="sh">Communication Health</div>
            <div id="comm-stats-container"><p style="color:var(--fg2);font-size:.82rem">Loading…</p></div>
        </div>
    </div>
    <!-- History Chart -->
    <div class="card" style="margin-bottom:28px">
        <div class="sh">Voltage &amp; Temperature History</div>
        <div class="chart-wrap"><canvas id="bmsChart"></canvas></div>
    </div>
</div>
<!-- FAB auto-refresh ring -->
<div class="fab" id="fab" title="Auto-refresh">
    <svg class="ring" viewBox="0 0 44 44">
        <circle cx="22" cy="22" r="18" fill="none" stroke="var(--border)" stroke-width="3"/>
        <circle id="rarc" cx="22" cy="22" r="18" fill="none" stroke="var(--acc)" stroke-width="3"
            stroke-dasharray="113.1" stroke-dashoffset="113.1" stroke-linecap="round" transform="rotate(-90 22 22)"/>
    </svg>
    <span class="rc" id="rcd">5</span>
</div>
<script>
    // ── Theme ──────────────────────────────────────────────────────
    const docEl = document.documentElement;
    const storedTheme = localStorage.getItem('bms-t') || 'dark';
    docEl.setAttribute('data-theme', storedTheme);
    document.getElementById('theme-btn').addEventListener('click', () => {{
        const t = docEl.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
        docEl.setAttribute('data-theme', t);
        localStorage.setItem('bms-t', t);
        if (myChart) {{ applyChartTheme(); myChart.update(); }}
    }});
    function isDark() {{ return docEl.getAttribute('data-theme') === 'dark'; }}

    // ── Chart theme ───────────────────────────────────────────────
    function chartColors() {{
        return {{
            text:      isDark() ? '#7a8ba0' : '#4a6072',
            grid:      isDark() ? 'rgba(30,48,68,.9)' : 'rgba(208,218,228,.8)',
            tip_bg:    isDark() ? '#111d2e' : '#ffffff',
            tip_bdr:   isDark() ? '#1e3044' : '#d0dae4',
            tip_title: isDark() ? '#e2e8f0' : '#0f1e2d',
            tip_body:  isDark() ? '#7a8ba0' : '#4a6072',
        }};
    }}
    function applyChartTheme() {{
        if (!myChart) return;
        const c = chartColors();
        ['x','y','temp'].forEach(ax => {{
            if (myChart.options.scales[ax]) {{
                myChart.options.scales[ax].ticks.color = c.text;
                myChart.options.scales[ax].grid.color = c.grid;
                if (myChart.options.scales[ax].title) myChart.options.scales[ax].title.color = c.text;
            }}
        }});
        myChart.options.plugins.legend.labels.color = c.text;
        Object.assign(myChart.options.plugins.tooltip, {{
            backgroundColor: c.tip_bg, borderColor: c.tip_bdr,
            titleColor: c.tip_title, bodyColor: c.tip_body
        }});
    }}

    // ── FAB countdown ─────────────────────────────────────────────
    let rIn = 5;
    const arc = document.getElementById('rarc');
    const rcdEl = document.getElementById('rcd');
    const C = 2 * Math.PI * 18;
    function updArc() {{ arc.setAttribute('stroke-dashoffset', C * (rIn / 5)); rcdEl.textContent = rIn; }}
    updArc();
    setInterval(() => {{ rIn--; if (rIn <= 0) {{ rIn = 5; updateStatus(); }} updArc(); }}, 1000);

    // ── Helpers ────────────────────────────────────────────────────
    function vcls(v, lo, hi) {{ return (!v || v === 0) ? 'bad' : (v > hi || v < lo) ? 'warn' : 'ok'; }}
    function tcls(t, lo, hi) {{ return t === null ? 'null' : t > hi ? 'hot' : t > hi * .9 ? 'warm' : 'ok'; }}
    function flash(el, cls) {{
        el.classList.remove('flash-ok','flash-bad');
        void el.offsetWidth;
        el.classList.add(cls);
        setTimeout(() => el.classList.remove(cls), 600);
    }}
    let prevVoltage = null;

    // ── Status update ──────────────────────────────────────────────
    function updateStatus() {{
        fetch('/api/status').then(r => r.json()).then(data => {{
            const st = data.system_status || '';
            const badge = document.getElementById('sbadge');
            let badgeCls, badgeText;
            if (st === 'Running') {{
                badgeCls = 'ok'; badgeText = '✓ Running';
            }} else if (st === 'Alert') {{
                badgeCls = 'bad'; badgeText = '⚠ Alert';
            }} else if (st.startsWith('Startup') || st === 'Initializing') {{
                badgeCls = 'idle'; badgeText = '⟳ ' + st;
            }} else {{
                badgeCls = 'idle'; badgeText = st || 'Connecting…';
            }}
            badge.className = 'badge ' + badgeCls;
            document.getElementById('sbdot').className = 'dot pulse';
            document.getElementById('stext').textContent = badgeText;
            document.getElementById('lupd').textContent = 'Updated ' + new Date(data.last_update * 1000).toLocaleTimeString();

            // Total voltage
            const tvEl = document.getElementById('tv');
            const newV = data.total_voltage;
            tvEl.textContent = newV.toFixed(2) + 'V';
            const tvCls = vcls(newV, data.low_voltage_threshold * data.voltages.length * 0.95, data.high_voltage_threshold * data.voltages.length * 1.02);
            tvEl.className = 'mv ' + tvCls;
            const mcTv = document.getElementById('mc-tv');
            mcTv.className = 'mc ' + tvCls + '-line';
            if (prevVoltage !== null && Math.abs(newV - prevVoltage) > 0.01) flash(mcTv, 'flash-ok');
            prevVoltage = newV;

            // Balancing
            const bmEl = document.getElementById('bm');
            const bm2 = document.getElementById('bms2');
            const mcBm = document.getElementById('mc-bm');
            if (data.balancing) {{
                bmEl.innerHTML = '<span class="spin"></span> Active';
                bmEl.className = 'mv bal-active';
                bm2.textContent = 'Charge transfer in progress';
                mcBm.className = 'mc ok-line';
            }} else {{
                bmEl.textContent = 'Idle';
                bmEl.className = 'mv ok';
                bm2.textContent = 'No transfer needed';
                mcBm.className = 'mc ok-line';
            }}

            // Alerts count
            const acEl = document.getElementById('ac');
            acEl.textContent = data.alerts.length;
            const acCls = data.alerts.length > 0 ? 'bad' : 'ok';
            acEl.className = 'mv ' + acCls;
            document.getElementById('mc-ac').className = 'mc ' + acCls + '-line';

            // Charge State (Feature 6)
            const csEl = document.getElementById('cs-display');
            if (csEl) {{
                const csState = data.charge_state || 'Idle';
                csEl.textContent = csState;
                const csMap = {{'Bulk':'ok','Absorption':'warn','Float':'ok','Discharging':'warn','Idle':''}};
                csEl.className = 'mv ' + (csMap[csState] || '');
                const mcCs = document.getElementById('mc-cs');
                if (mcCs) {{ mcCs.style.display=''; const _csCls={{'Float':'ok','Bulk':'ok','Absorption':'warn','Discharging':'warn'}}; mcCs.className='mc '+(_csCls[csState]||'ok')+'-line'; }}
                const effV = data.effective_charge_current !== undefined ? parseFloat(data.effective_charge_current).toFixed(1) : '—';
                const sub = document.getElementById('cs-sub');
                if (sub) sub.innerHTML = 'Effective: <span id="cs-eff">' + effV + '</span>A' + (data.hv_clamp ? ' <span style="color:var(--bad);font-size:.7rem">HV CLAMP</span>' : '');
            }}

            // Avg temp
            const vt = data.temperatures.filter(t => t !== null);
            const avg = vt.length ? vt.reduce((a, b) => a + b, 0) / vt.length : null;
            const atEl = document.getElementById('at');
            if (avg !== null) {{
                atEl.textContent = avg.toFixed(1) + '°C';
                const tCls = tcls(avg, data.low_threshold, data.high_threshold);
                atEl.className = 'mv ' + (tCls === 'ok' ? 'ok' : tCls === 'warm' ? 'warn' : 'bad');
                document.getElementById('mc-at').className = 'mc ' + (tCls === 'ok' ? 'ok' : 'warn') + '-line';
            }}

            // Banks
            const lo = data.low_voltage_threshold, hi = data.high_voltage_threshold;
            const spbk = Math.max(1, Math.floor(data.temperatures.length / Math.max(1, data.voltages.length)));
            const container = document.getElementById('battery-container');
            container.innerHTML = '';
            data.voltages.forEach((v, idx) => {{
                const s = data.bank_summaries[idx] || {{median:0,min:0,max:0,invalid:0}};
                const vc = vcls(v, lo, hi);
                const pct = v > 0 ? Math.min(100, Math.max(0, ((v - lo) / (hi - lo)) * 100)) : 0;
                const sensors = data.temperatures.slice(idx * spbk, (idx + 1) * spbk);
                const spb = data.sensors_per_battery || 8;
                const chips = sensors.map((t, li) => {{
                    const gi = idx * spbk + li;
                    const bat = Math.floor(gi / spb) + 1;
                    const ch = (gi % spb) + 1;
                    const tc = tcls(t, data.low_threshold, data.high_threshold);
                    return `<div class="sc ${{tc}}" title="Bat ${{bat}} C${{ch}}">${{t !== null ? t.toFixed(1) + '°' : 'N/A'}}</div>`;
                }}).join('');
                const mc = tcls(s.median, data.low_threshold, data.high_threshold);
                const alertCls = (vc !== 'ok' || mc === 'hot') ? ' alert' : '';
                container.innerHTML += `<div class="bcard${{alertCls}}">
                    <div class="bhdr">
                        <div><div class="bname">Bank ${{idx + 1}}</div><div class="bsub">${{spbk}} temp sensors</div></div>
                        <div class="bv ${{vc}}">${{v && v > 0 ? v.toFixed(3) + 'V' : 'N/A'}}</div>
                    </div>
                    <div class="vtrack"><div class="vfill ${{vc}}" style="width:${{pct}}%"></div></div>
                    <div class="btemps">
                        <span><span class="lbl">Median</span><span class="val ${{mc === 'ok' ? '' : mc === 'warm' ? 'warn' : 'bad'}}">${{s.median.toFixed(1)}}°C</span></span>
                        <span><span class="lbl">Min</span><span class="val">${{s.min.toFixed(1)}}°C</span></span>
                        <span><span class="lbl">Max</span><span class="val ${{s.max > data.high_threshold ? 'bad' : ''}}">${{s.max.toFixed(1)}}°C</span></span>
                        <span><span class="lbl">Invalid</span><span class="val ${{s.invalid > 0 ? 'warn' : ''}}">${{s.invalid}}</span></span>
                    </div>
                    <div class="div"></div>
                    <div class="sgrid">${{chips}}</div>
                </div>`;
            }});

            // Alerts list
            const ad = document.getElementById('alerts-container');
            if (data.alerts.length > 0) {{
                ad.innerHTML = '<div class="alist">' + data.alerts.map(a =>
                    `<div class="ai"><span class="ai-icon">⚠</span>${{a}}</div>`
                ).join('') + '</div>';
            }} else {{
                ad.innerHTML = '<div class="noa"><span>✓</span> All systems normal</div>';
            }}
            document.getElementById('balance-btn').disabled = data.balancing || data.alerts.length > 0;
            // Update DVCC panel
            const hvEl = document.getElementById('dvcc-hv-clamp');
            if (hvEl && data.hv_clamp !== undefined) {{
                hvEl.textContent = data.hv_clamp ? 'YES '+parseFloat(data.hv_clamped_cvl||0).toFixed(2)+'V' : 'No';
                hvEl.style.color = data.hv_clamp ? 'var(--bad)' : 'var(--fg2)';
            }}
            const blvEl = document.getElementById('dvcc-blv');
            if (blvEl && data.dvcc_min_discharge_voltage !== undefined) {{
                blvEl.textContent = (data.dvcc_min_discharge_voltage - (data.discharge_cable_drop||0)).toFixed(2);
            }}
            const dEffEl = document.getElementById('dvcc-eff-cc');
            if (dEffEl && data.effective_charge_current !== undefined) {{
                dEffEl.textContent = parseFloat(data.effective_charge_current).toFixed(1);
            }}
            // Auto-refresh cable drop display
            if (data.cable_drop_compensation !== undefined && data.cerbo_voltage !== undefined) {{
                updateCvDisplay(parseFloat(data.charge_voltage || 60.3), parseFloat(data.cable_drop_compensation), parseFloat(data.cerbo_voltage));
            }}
            // Show cap warning if target is unreachable
            const capWarn = document.getElementById('cv-cap-warn');
            if (capWarn) {{
                const isAtCap = data.cerbo_voltage !== undefined && data.cerbo_voltage >= 62.95;
                capWarn.style.display = isAtCap ? '' : 'none';
            }}

        }}).catch(() => {{
            document.getElementById('sbadge').className = 'badge bad';
            document.getElementById('stext').textContent = '✗ Connection Error';
        }});
    }}

    // ── History Chart ──────────────────────────────────────────────
    let myChart = null;
    function updateChart() {{
        fetch('/api/history').then(r => r.json()).then(data => {{
            const hist = data.history;
            if (!hist || !hist.length) return;
            const c = chartColors();
            const labels = hist.map(h => new Date(h.time * 1000).toLocaleTimeString());
            const datasets = [
                {datasets_array}
            ];
            const ctx = document.getElementById('bmsChart').getContext('2d');
            if (myChart) myChart.destroy();
            myChart = new Chart(ctx, {{
                type: 'line',
                data: {{ labels, datasets }},
                options: {{
                    responsive: true, maintainAspectRatio: false,
                    animation: {{ duration: 300 }},
                    interaction: {{ mode: 'index', intersect: false }},
                    elements: {{ point: {{ radius: 0, hoverRadius: 4 }}, line: {{ tension: 0.3, borderWidth: 2 }} }},
                    plugins: {{
                        legend: {{ labels: {{ color: c.text, usePointStyle: true, pointStyle: 'circle', padding: 16, font: {{ size: 12, family: 'Inter' }} }} }},
                        tooltip: {{ backgroundColor: c.tip_bg, borderColor: c.tip_bdr, borderWidth: 1, titleColor: c.tip_title, bodyColor: c.tip_body, padding: 10, titleFont: {{ family: 'Inter' }}, bodyFont: {{ family: 'Inter' }} }}
                    }},
                    scales: {{
                        x: {{ ticks: {{ color: c.text, maxTicksLimit: 10, font: {{ size: 11 }} }}, grid: {{ color: c.grid }} }},
                        y: {{ type: 'linear', position: 'left', title: {{ display: true, text: 'Voltage (V)', color: c.text, font: {{ size: 11 }} }}, ticks: {{ color: c.text, font: {{ size: 11 }} }}, grid: {{ color: c.grid }} }},
                        temp: {{ type: 'linear', position: 'right', title: {{ display: true, text: 'Temp (°C)', color: c.text, font: {{ size: 11 }} }}, ticks: {{ color: c.text, font: {{ size: 11 }} }}, grid: {{ drawOnChartArea: false }} }}
                    }}
                }}
            }});
        }}).catch(e => console.error('History error:', e));
    }}

    // ── Comm Stats ────────────────────────────────────────────────
    function updateCommStats() {{
        fetch('/api/comm_stats').then(r => r.json()).then(data => {{
            const el = document.getElementById('comm-stats-container');
            if (!data.slaves || !data.slaves.length) {{
                el.innerHTML = '<p style="color:var(--fg2);font-size:.82rem">No data</p>';
                return;
            }}
            let h = '<table class="ct"><thead><tr><th>Slave</th><th>OK</th><th>Fail</th><th>Rate</th><th>Last Error</th></tr></thead><tbody>';
            data.slaves.forEach(s => {{
                const r = s.success_rate;
                const rc = r >= 90 ? 'var(--ok)' : r >= 60 ? 'var(--warn)' : 'var(--bad)';
                const err = s.last_error_type || (s.last_error ? new Date(s.last_error * 1000).toLocaleTimeString() : '—');
                h += `<tr>
                    <td style="font-weight:600;color:var(--fg)">S${{s.slave_addr}}</td>
                    <td style="color:var(--ok)">${{s.success_count}}</td>
                    <td style="color:var(--bad)">${{s.fail_count}}</td>
                    <td><div class="rbar"><div class="rmini"><div class="rfill" style="width:${{r}}%;background:${{rc}}"></div></div><span style="color:${{rc}};font-weight:600;font-size:.73rem;white-space:nowrap">${{r}}%</span></div></td>
                    <td style="color:var(--fg2);font-size:.73rem">${{err}}</td>
                </tr>`;
            }});
            h += `</tbody></table><div style="margin-top:10px;font-size:.73rem;color:var(--fg3)">Total: ${{data.total_success}} ok / ${{data.total_fail}} fail</div>`;
            el.innerHTML = h;
        }}).catch(() => {{
            document.getElementById('comm-stats-container').innerHTML = '<p style="color:var(--bad);font-size:.82rem">Failed to load</p>';
        }});
    }}

    // ── Balance ───────────────────────────────────────────────────
    function initiateBalance() {{
        fetch('/api/balance', {{ method: 'POST' }}).then(r => r.json())
            .then(d => alert((d.success ? '✓ ' : '⚠ ') + d.message))
            .catch(e => alert('Error: ' + e.message));
    }}

    // ── Cerbo GX Toggle ───────────────────────────────────────────
    const cerboToggle = document.getElementById('cerbo-toggle');
    const cerboStatus = document.getElementById('cerbo-status');

    function setCerboUI(enabled, busy) {{
        cerboToggle.checked = enabled;
        cerboToggle.disabled = busy;
        cerboStatus.textContent = busy ? '…' : (enabled ? 'ON' : 'OFF');
        cerboStatus.className = 'cerbo-status ' + (busy ? '' : (enabled ? 'on' : 'off'));
    }}

    fetch('/api/cerbo_integration').then(r => r.json())
        .then(d => setCerboUI(d.enabled, false))
        .catch(() => setCerboUI(false, false));

    cerboToggle.addEventListener('change', function() {{
        const want = this.checked;
        setCerboUI(want, true);
        fetch('/api/cerbo_integration', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{enabled: want}})
        }}).then(r => r.json()).then(d => {{
            if (d.success) {{
                setCerboUI(d.enabled, false);
            }} else {{
                setCerboUI(!want, false);  // revert
                alert('⚠ ' + (d.message || 'Failed to contact Cerbo GX'));
            }}
        }}).catch(e => {{
            setCerboUI(!want, false);  // revert
            alert('Error: ' + e.message);
        }});
    }});

    // ── Charge Voltage ────────────────────────────────────────────
    function updateCvDisplay(cv, drop, cerboV) {{
        document.getElementById('cv-display').textContent = cv.toFixed(1) + 'V';
        document.getElementById('cv-input').value = cv.toFixed(1);
        const dropEl = document.getElementById('cv-drop-display');
        const cerboEl = document.getElementById('cv-cerbo-display');
        dropEl.textContent = drop.toFixed(3) + 'V';
        cerboEl.textContent = cerboV.toFixed(2) + 'V';
        cerboEl.style.color = drop > 0.01 ? 'var(--warn)' : 'var(--fg2)';
    }}
    function loadChargeVoltage() {{
        fetch('/api/charge_voltage').then(r => r.json()).then(d => {{
            updateCvDisplay(parseFloat(d.charge_voltage), parseFloat(d.cable_drop_compensation || 0), parseFloat(d.cerbo_voltage || d.charge_voltage));
        }}).catch(() => {{ document.getElementById('cv-display').textContent = 'ERR'; }});
    }}
    function setChargeVoltage() {{
        const cv = parseFloat(document.getElementById('cv-input').value);
        if (isNaN(cv) || cv <= 0 || cv > 63) {{ alert('Target voltage must be between 0.1 and 63V'); return; }}
        fetch('/api/charge_voltage', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{charge_voltage: cv}})
        }}).then(r => r.json()).then(d => {{
            if (d.success) {{ updateCvDisplay(parseFloat(d.charge_voltage), parseFloat(d.cable_drop_compensation), parseFloat(d.cerbo_voltage)); }}
            else {{ alert('\u26a0 ' + (d.message || 'Failed to set charge voltage')); }}
        }}).catch(e => alert('Error: ' + e.message));
    }}

    // ── Wiring ────────────────────────────────────────────────────
    document.getElementById('refresh-btn').addEventListener('click', () => {{ rIn = 5; updateStatus(); }});
    document.getElementById('balance-btn').addEventListener('click', initiateBalance);
    document.getElementById('fab').addEventListener('click', () => {{ rIn = 5; updateStatus(); }});

    // DVCC Settings functions (Feature 5)
    function loadDvccSettings() {{
        fetch('/api/dvcc_settings').then(r => r.json()).then(d => {{
            const mcc=document.getElementById('dvcc-mcc');
            const mdc=document.getElementById('dvcc-mdc');
            const mdv=document.getElementById('dvcc-mdv');
            const dcd=document.getElementById('dvcc-dcd');
            if(mcc && d.max_charge_current!==undefined) mcc.value=d.max_charge_current;
            if(mdc && d.max_discharge_current!==undefined) mdc.value=d.max_discharge_current;
            if(mdv && d.min_discharge_voltage!==undefined) mdv.value=d.min_discharge_voltage;
            if(dcd && d.discharge_cable_drop!==undefined) dcd.value=d.discharge_cable_drop;
            const tds=document.getElementById('dvcc-tds');
            const tde=document.getElementById('dvcc-tde');
            const cco=document.getElementById('dvcc-cco');
            const cst=document.getElementById('dvcc-cst');
            if(tds && d.temp_derate_start!==undefined) tds.value=d.temp_derate_start;
            if(tde && d.temp_derate_end!==undefined) tde.value=d.temp_derate_end;
            if(cco && d.cold_charge_cutoff!==undefined) cco.value=d.cold_charge_cutoff;
            if(cst && d.cold_charge_min!==undefined) cst.value=d.cold_charge_min;
            const e=document.getElementById('dvcc-eff-cc');
            if(e) e.textContent=d.effective_charge_current!==undefined?parseFloat(d.effective_charge_current).toFixed(1):'—';
        }}).catch(()=>{{ const s=document.getElementById('dvcc-status'); if(s) s.textContent='Load error'; }});
    }}
    function saveDvccSettings() {{
        const mccEl=document.getElementById('dvcc-mcc');
        const mdcEl=document.getElementById('dvcc-mdc');
        const mdvEl=document.getElementById('dvcc-mdv');
        const dcdEl=document.getElementById('dvcc-dcd');
        if(!mccEl||!mdcEl||!mdvEl||!dcdEl){{ alert('DVCC fields not found'); return; }}
        const tdsEl=document.getElementById('dvcc-tds');
        const tdeEl=document.getElementById('dvcc-tde');
        const ccoEl=document.getElementById('dvcc-cco');
        const cstEl=document.getElementById('dvcc-cst');
        const mcc=parseFloat(mccEl.value), mdc=parseFloat(mdcEl.value);
        const mdv=parseFloat(mdvEl.value), dcd=parseFloat(dcdEl.value);
        const tds=tdsEl?parseFloat(tdsEl.value):NaN, tde=tdeEl?parseFloat(tdeEl.value):NaN;
        const cco=ccoEl?parseFloat(ccoEl.value):NaN, cst=cstEl?parseFloat(cstEl.value):NaN;
        if([mcc,mdc,mdv,dcd].some(isNaN)){{ alert('All DVCC fields must be valid numbers'); return; }}
        const payload={{max_charge_current:mcc,max_discharge_current:mdc,min_discharge_voltage:mdv,discharge_cable_drop:dcd}};
        if(!isNaN(tds)) payload.temp_derate_start=tds;
        if(!isNaN(tde)) payload.temp_derate_end=tde;
        if(!isNaN(cco)) payload.cold_charge_cutoff=cco;
        if(!isNaN(cst)) payload.cold_charge_min=cst;
        fetch('/api/dvcc_settings',{{
            method:'POST',headers:{{'Content-Type':'application/json'}},
            body:JSON.stringify(payload)
        }}).then(r=>r.json()).then(d=>{{
            if(d.success){{
                const s=document.getElementById('dvcc-status'); if(s){{s.textContent='✓ Saved';setTimeout(()=>{{s.textContent=''}},3000);}}
                loadDvccSettings();
            }}else{{ alert('⚠ '+(d.message||'Failed to save')); }}
        }}).catch(e=>alert('Error: '+e.message));
    }}

    // Initial load
    updateStatus();
    updateChart();
    updateCommStats();
    loadChargeVoltage();
    loadDvccSettings();
    setInterval(updateChart, 30000);
    setInterval(updateCommStats, 10000);
</script>
</body>
</html>"""
        return html
    # API for status.
    @app.route('/api/status')
    def api_status():
        try:
            # Lock for thread safety.
            with data_lock:
                voltages = [v if v is not None else 0.0 for v in web_data['voltages']]
                response = {
                    'voltages': web_data['voltages'],
                    'temperatures': web_data['temperatures'],
                    'bank_summaries': web_data['bank_summaries'],
                    'alerts': web_data['alerts'],
                    'balancing': web_data['balancing'],
                    'last_update': web_data['last_update'],
                    'system_status': web_data['system_status'],
                    'total_voltage': sum(voltages),
                    'high_threshold': settings['high_threshold'],
                    'low_threshold': settings['low_threshold'],
                    'high_voltage_threshold': settings['HighVoltageThresholdPerBattery'],
                    'low_voltage_threshold': settings['LowVoltageThresholdPerBattery'],
                    'sensors_per_battery': settings['sensors_per_battery'],
                    'charge_state': web_data.get('charge_state', 'Idle'),
                    'effective_charge_current': web_data.get('effective_charge_current', settings.get('dvcc_max_charge_current', 200.0)),
                    'hv_clamp': web_data.get('hv_clamp', False),
                    'hv_clamped_cvl': web_data.get('hv_clamped_cvl', 0.0),
                    'dvcc_max_charge_current': settings.get('dvcc_max_charge_current', 200.0),
                    'dvcc_max_discharge_current': settings.get('dvcc_max_discharge_current', 200.0),
                    'dvcc_min_discharge_voltage': settings.get('dvcc_min_discharge_voltage', 49.5),
                    'discharge_cable_drop': settings.get('discharge_cable_drop', 0.0),
                    'cable_drop_compensation': settings.get('cable_drop_compensation', 0.0),
                    # cerbo_voltage: actual CVL sent — respects HV clamp (no cable drop when clamped)
                    'cerbo_voltage': round(min(63.0, web_data.get('hv_clamped_cvl', settings.get('dvcc_max_charge_voltage', 60.3)) if web_data.get('hv_clamp', False) else settings.get('dvcc_max_charge_voltage', 60.3) + settings.get('cable_drop_compensation', 0.0)), 2),
                    'cerbo_dc_voltage': _cerbo_dc_cache.get('v'),  # Cerbo GX measured DC bus voltage (null if unavailable)
                    'charge_voltage': settings.get('dvcc_max_charge_voltage', 60.3),
                    'web_server_healthy': web_data.get('_web_server_healthy', True),
                }
            return jsonify(response)
        except Exception as e:
            logging.error(f"Error in /api/status: {str(e)}\n{traceback.format_exc()}")
            return jsonify({'error': str(e)}), 500
    # API for history.
    @app.route('/api/history')
    def api_history():
        try:
            history = fetch_rrd_history(settings)
            return jsonify({'history': history})
        except Exception as e:
            logging.error(f"Error in /api/history: {str(e)}\n{traceback.format_exc()}")
            return jsonify({'error': str(e)}), 500
    
    # API for communication statistics
    @app.route('/api/comm_stats')
    def api_comm_stats():
        try:
            stats = get_comm_stats()
            return jsonify(stats)
        except Exception as e:
            logging.error(f"Error in /api/comm_stats: {str(e)}\n{traceback.format_exc()}")
            return jsonify({'error': str(e)}), 500
    # API for balance trigger.
    @app.route('/api/balance', methods=['POST'])
    def api_balance():
        global balancing_active
        with data_lock:
            if balancing_active:
                return jsonify({'success': False, 'message': 'Balancing already in progress'}), 400
            if len(web_data['alerts']) > 0:
                return jsonify({'success': False, 'message': 'Cannot balance with active alerts'}), 400
            voltages = web_data['voltages']
            if len(voltages) < 2:
                return jsonify({'success': False, 'message': 'Not enough battery banks'}), 400
            max_v = max(voltages)
            min_v = min(voltages)
            high_bank = voltages.index(max_v) + 1
            low_bank = voltages.index(min_v) + 1
            if max_v - min_v < settings['VoltageDifferenceToBalance']:
                return jsonify({'success': False, 'message': 'Voltage difference too small for balancing'}), 400
            balancing_active = True
        logging.info(f"Balancing initiated via web API from Bank {high_bank} to Bank {low_bank}")
        return jsonify({'success': True, 'message': f'Balancing initiated from Bank {high_bank} to Bank {low_bank}'})
    # API to enable/disable Cerbo GX dbus-bms-battery integration.
    @app.route('/api/cerbo_integration', methods=['GET', 'POST'])
    def api_cerbo_integration():
        global cerbo_integration_enabled
        if request.method == 'GET':
            return jsonify({'enabled': cerbo_integration_enabled})
        try:
            data = request.get_json(force=True)
            want_enabled = bool(data.get('enabled', True))
            cerbo_ip = settings.get('cerbo_ip', '192.168.15.67')
            cerbo_pass = settings.get('cerbo_pass', '555555')
            timeout = settings.get('cerbo_ssh_timeout', 8)
            if want_enabled:
                cmd = ['sshpass', '-p', cerbo_pass, 'ssh',
                       '-o', 'StrictHostKeyChecking=no',
                       '-o', f'ConnectTimeout={timeout}',
                       f'root@{cerbo_ip}',
                       'svc -u /service/dbus-bms-battery']
                action = 'enabled'
            else:
                cmd = ['sshpass', '-p', cerbo_pass, 'ssh',
                       '-o', 'StrictHostKeyChecking=no',
                       '-o', f'ConnectTimeout={timeout}',
                       f'root@{cerbo_ip}',
                       'svc -d /service/dbus-bms-battery']
                action = 'disabled'
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 2)
            if result.returncode != 0:
                err = result.stderr.strip() or result.stdout.strip()
                logging.error(f'Cerbo GX SSH error ({action}): {err}')
                return jsonify({'success': False, 'message': f'SSH error: {err}'}), 500
            cerbo_integration_enabled = want_enabled
            state_file = settings.get('cerbo_state_file')
            if state_file:
                try:
                    with open(state_file, 'w') as _sf:
                        _sf.write('enabled' if want_enabled else 'disabled')
                except Exception as _e:
                    logging.warning(f'Could not save Cerbo state: {_e}')
            logging.info(f'Cerbo GX integration {action} via web UI')
            return jsonify({'success': True, 'enabled': cerbo_integration_enabled, 'message': f'Cerbo GX integration {action}'})
        except subprocess.TimeoutExpired:
            return jsonify({'success': False, 'message': 'SSH timed out — is the Cerbo GX reachable?'}), 504
        except Exception as e:
            logging.error(f'Error in /api/cerbo_integration: {e}\n{traceback.format_exc()}')
            return jsonify({'error': str(e)}), 500

    @app.route('/api/charge_voltage', methods=['GET', 'POST'])
    def api_charge_voltage():
        drop = settings.get('cable_drop_compensation', 0.0)
        cv   = settings.get('dvcc_max_charge_voltage', 61.0)
        if request.method == 'GET':
            return jsonify({
                'charge_voltage': cv,
                'cable_drop_compensation': drop,
                'cerbo_voltage': round(min(63.0, cv + drop), 2)
            })
        try:
            data   = request.get_json(force=True)
            new_cv = float(data['charge_voltage']) if 'charge_voltage' in data else cv
            if new_cv <= 0 or new_cv > 63:
                return jsonify({'success': False, 'message': 'Charge voltage must be between 0.1 and 63V'}), 400
            settings['dvcc_max_charge_voltage'] = new_cv
            # Reset cable drop to 0 when target changes so auto-compensation relearns from scratch
            settings['cable_drop_compensation'] = 0.0
            ini_path = os.path.join(settings.get('data_dir', '/projects/battery_balancer'), 'battery_monitor.ini')
            try:
                _cfg = configparser.ConfigParser(comment_prefixes=(';', '#'))
                _cfg.read(ini_path)
                if not _cfg.has_section('DVCC'):
                    _cfg.add_section('DVCC')
                _cfg.set('DVCC', 'max_charge_voltage', str(new_cv))
                _cfg.set('DVCC', 'cable_drop_compensation', '0.0')
                with open(ini_path, 'w') as _f:
                    _cfg.write(_f)
            except Exception as _e:
                logging.warning(f'Could not persist charge voltage to INI: {_e}')
            cerbo_v = round(min(63.0, new_cv + settings.get('cable_drop_compensation', 0.0)), 2)
            logging.info(f'Charge voltage target set to {new_cv}V (cable drop auto-reset to 0, will re-learn)')
            return jsonify({'success': True, 'charge_voltage': new_cv, 'cable_drop_compensation': 0.0, 'cerbo_voltage': cerbo_v})
        except Exception as e:
            logging.error(f'Error in /api/charge_voltage: {e}\n{traceback.format_exc()}')
            return jsonify({'error': str(e)}), 500

    @app.route('/api/dvcc_settings', methods=['GET', 'POST'])
    def api_dvcc_settings():
        if request.method == 'GET':
            return jsonify({
                'max_charge_current': settings.get('dvcc_max_charge_current', 200.0),
                'max_discharge_current': settings.get('dvcc_max_discharge_current', 200.0),
                'min_discharge_voltage': settings.get('dvcc_min_discharge_voltage', 49.5),
                'discharge_cable_drop': settings.get('discharge_cable_drop', 0.0),
                'effective_charge_current': settings.get('_effective_charge_current', settings.get('dvcc_max_charge_current', 200.0)),
                'temp_derate_start': settings.get('temp_derate_start', 38.0),
                'temp_derate_end': settings.get('temp_derate_end', 45.0),
                'cold_charge_cutoff': settings.get('cold_charge_cutoff', 5.0),
                'cold_charge_min': settings.get('cold_charge_min', 0.0),
            })
        try:
            data = request.get_json(force=True)
            ini_path = os.path.join(settings.get('data_dir', '/projects/battery_balancer'), 'battery_monitor.ini')
            # Pre-validate temp derate cross-constraint
            _tds_proposed = float(data['temp_derate_start']) if 'temp_derate_start' in data else settings.get('temp_derate_start', 38.0)
            _tde_proposed = float(data['temp_derate_end']) if 'temp_derate_end' in data else settings.get('temp_derate_end', 45.0)
            if not (0.0 <= _tds_proposed <= 60.0):
                return jsonify({'success': False, 'message': 'temp_derate_start must be 0-60°C'}), 400
            if not (0.0 <= _tde_proposed <= 60.0):
                return jsonify({'success': False, 'message': 'temp_derate_end must be 0-60°C'}), 400
            if _tde_proposed <= _tds_proposed:
                return jsonify({'success': False, 'message': 'temp_derate_end must be greater than temp_derate_start'}), 400
            # Pre-validate cold charge cross-constraint before mutating any settings
            _cc_proposed = float(data['cold_charge_cutoff']) if 'cold_charge_cutoff' in data else settings.get('cold_charge_cutoff', 5.0)
            _cm_proposed = float(data['cold_charge_min']) if 'cold_charge_min' in data else settings.get('cold_charge_min', 0.0)
            if _cc_proposed <= _cm_proposed:
                return jsonify({'success': False, 'message': 'cold_charge_cutoff must be greater than cold_charge_min'}), 400
            changed = []
            if 'max_charge_current' in data:
                val = float(data['max_charge_current'])
                if val < 0 or val > 1000:
                    return jsonify({'success': False, 'message': 'max_charge_current must be 0-1000A'}), 400
                settings['dvcc_max_charge_current'] = val
                # Do not touch _effective_charge_current here; main loop recalculates it
                # correctly with temp derating on the next poll cycle (within 2 seconds).
                changed.append(('max_charge_current', str(val)))
            if 'max_discharge_current' in data:
                val = float(data['max_discharge_current'])
                if val < 0 or val > 1000:
                    return jsonify({'success': False, 'message': 'max_discharge_current must be 0-1000A'}), 400
                settings['dvcc_max_discharge_current'] = val
                changed.append(('max_discharge_current', str(val)))
            if 'min_discharge_voltage' in data:
                val = float(data['min_discharge_voltage'])
                if val < 0 or val > 63:
                    return jsonify({'success': False, 'message': 'min_discharge_voltage must be 0-63V'}), 400
                settings['dvcc_min_discharge_voltage'] = val
                changed.append(('min_discharge_voltage', str(val)))
            if 'discharge_cable_drop' in data:
                val = float(data['discharge_cable_drop'])
                if val < 0 or val > 5:
                    return jsonify({'success': False, 'message': 'discharge_cable_drop must be 0-5V'}), 400
                settings['discharge_cable_drop'] = val
                changed.append(('discharge_cable_drop', str(val)))
            if 'temp_derate_start' in data:
                val = float(data['temp_derate_start'])
                settings['temp_derate_start'] = val
                changed.append(('temp_derate_start', str(val)))
            if 'temp_derate_end' in data:
                val = float(data['temp_derate_end'])
                settings['temp_derate_end'] = val
                changed.append(('temp_derate_end', str(val)))
            if 'cold_charge_cutoff' in data:
                val = float(data['cold_charge_cutoff'])
                settings['cold_charge_cutoff'] = val
                changed.append(('cold_charge_cutoff', str(val)))
            if 'cold_charge_min' in data:
                val = float(data['cold_charge_min'])
                settings['cold_charge_min'] = val
                changed.append(('cold_charge_min', str(val)))
            # cold_charge cross-validation already done above before any mutation
            if changed:
                try:
                    _cfg = configparser.ConfigParser(comment_prefixes=(';', '#'))
                    _cfg.read(ini_path)
                    if not _cfg.has_section('DVCC'):
                        _cfg.add_section('DVCC')
                    for _k, _v in changed:
                        _cfg.set('DVCC', _k, _v)
                    with open(ini_path, 'w') as _f:
                        _cfg.write(_f)
                except Exception as _e:
                    logging.warning(f'Could not persist DVCC settings: {_e}')
            logging.info(f'DVCC settings updated: {dict(changed)}')
            return jsonify({
                'success': True,
                'max_charge_current': settings.get('dvcc_max_charge_current', 200.0),
                'max_discharge_current': settings.get('dvcc_max_discharge_current', 200.0),
                'min_discharge_voltage': settings.get('dvcc_min_discharge_voltage', 49.5),
                'discharge_cable_drop': settings.get('discharge_cable_drop', 0.0),
                'effective_charge_current': settings.get('_effective_charge_current', settings.get('dvcc_max_charge_current', 200.0)),
                'temp_derate_start': settings.get('temp_derate_start', 38.0),
                'temp_derate_end': settings.get('temp_derate_end', 45.0),
                'cold_charge_cutoff': settings.get('cold_charge_cutoff', 5.0),
                'cold_charge_min': settings.get('cold_charge_min', 0.0),
            })
        except Exception as e:
            logging.error(f'Error in /api/dvcc_settings: {e}\n{traceback.format_exc()}')
            return jsonify({'error': str(e)}), 500

    # Before each request: Auth check and CORS preflight.
    @app.before_request
    def before_request():
        if settings['auth_required']:
            auth = request.authorization
            if not auth or not (auth.username == settings['username'] and auth.password == settings['password']):
                return make_response('Unauthorized', 401, {'WWW-Authenticate': 'Basic realm="BMS"'})
        if settings['cors_enabled'] and request.method == 'OPTIONS':
            response = make_response()
            response.headers['Access-Control-Allow-Origin'] = settings['cors_origins']
            response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
            response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
            return response
    # After each request: Add CORS headers to all responses.
    @app.after_request
    def after_request(response):
        if settings['cors_enabled']:
            response.headers['Access-Control-Allow-Origin'] = settings['cors_origins']
        return response
    # Function to run app.
    def run_app():
        logging.info("Starting Flask app...")
        try:
            app.run(host=settings['host'], port=settings['web_port'], threaded=True, debug=False, use_reloader=False)
        except Exception as e:
            logging.error(f"Web server error: {e}\n{traceback.format_exc()}")
    # Start thread.
    server_thread = threading.Thread(target=run_app, name='flask-web')
    server_thread.daemon = True
    server_thread.start()
    settings['_flask_thread'] = server_thread
    settings['_flask_run_app'] = run_app
    logging.info(f"Web server started on {settings['host']}:{settings['web_port']}")

    # Feature 7: Web server watchdog
    def web_server_watchdog():
        import time as _wt
        _wt.sleep(60)
        while True:
            try:
                _thr = settings.get('_flask_thread')
                if _thr is not None and not _thr.is_alive():
                    logging.warning('Web watchdog: Flask thread died, restarting...')
                    with data_lock:
                        web_data['_web_server_healthy'] = False
                    _new_thr = threading.Thread(target=run_app, name='flask-web-restarted')
                    _new_thr.daemon = True
                    _new_thr.start()
                    settings['_flask_thread'] = _new_thr
                    _wt.sleep(5)
                    if _new_thr.is_alive():
                        logging.info('Web watchdog: Flask restarted successfully.')
                        with data_lock:
                            web_data['_web_server_healthy'] = True
                    else:
                        logging.error('Web watchdog: Flask restart FAILED.')
                else:
                    with data_lock:
                        web_data['_web_server_healthy'] = True
            except Exception as _e:
                logging.error(f'Web watchdog error: {_e}')
            _wt.sleep(30)

    _web_wd = threading.Thread(target=web_server_watchdog, name='web-watchdog', daemon=True)
    _web_wd.start()
    logging.info('Web server watchdog started.')

def main(stdscr):
    """
    Main entry point: Initializes everything and runs the monitoring loop.
    Checks deps, loads config, setups hardware/web, self-test, starts watchdog, then infinite loop: Read temps/volts,
    check issues, update RRD/TUI/web, balance if needed, sleep. Non-programmer: The "heart" of the script—where the ongoing work happens.
    
    Args:
        stdscr: Curses screen (from wrapper).
    
    Returns:
        None: Runs forever until signal.
    """
    # Check deps.
    check_dependencies()
    # Curses setup.
    stdscr.keypad(True)
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_RED, -1)       # Red - overheat
    curses.init_pair(2, curses.COLOR_RED, -1)       # Red - alerts
    curses.init_pair(3, curses.COLOR_YELLOW, -1)    # Yellow - warm
    curses.init_pair(4, curses.COLOR_GREEN, -1)     # Green - normal
    curses.init_pair(5, curses.COLOR_WHITE, -1)     # White
    curses.init_pair(6, curses.COLOR_YELLOW, -1)    # Orange - hot (reuse yellow pair)
    curses.init_pair(7, curses.COLOR_CYAN, -1)      # Cyan - cold
    curses.init_pair(8, curses.COLOR_MAGENTA, -1)   # Magenta - very cold
    curses.init_pair(9, curses.COLOR_WHITE, -1)     # Spare
    stdscr.nodelay(True)
    # Globals.
    global previous_temps, previous_bank_medians, run_count, startup_offsets, startup_median, startup_set, battery_voltages, web_data, balancing_active, BANK_SENSOR_INDICES, alive_timestamp, NUM_BANKS, balancer_failed, balancer_failed_time, balancer_fail_count, balancer_fail_reason, comm_stats, REASONABLE_TEMP_MIN, REASONABLE_TEMP_MAX, CONSECUTIVE_FAILURE_THRESHOLD
    # Load and validate config.
    settings = load_config(data_dir)
    settings['data_dir'] = data_dir  # Store for use by API handlers
    validate_config(settings)
    # Set config-based globals
    global REASONABLE_TEMP_MIN, REASONABLE_TEMP_MAX, CONSECUTIVE_FAILURE_THRESHOLD
    REASONABLE_TEMP_MIN = settings.get('reasonable_temp_min', -10.0)
    REASONABLE_TEMP_MAX = settings.get('reasonable_temp_max', 60.0)
    CONSECUTIVE_FAILURE_THRESHOLD = settings.get('consecutive_failure_threshold', 5)
    # Set banks.
    NUM_BANKS = settings['num_series_banks'] # Dynamic now.
    number_parallel = settings['number_of_parallel_batteries']
    slave_addresses = settings['modbus_slave_addresses']
    sensors_per_bank = settings['sensors_per_bank']
    sensors_per_battery = NUM_BANKS * sensors_per_bank
    total_channels = number_parallel * sensors_per_battery
    # Bank indices.
    BANK_SENSOR_INDICES = [[] for _ in range(settings['num_series_banks'])] # Dynamic list of lists.
    # Init web_data.
    web_data['voltages'] = [0.0] * NUM_BANKS
    web_data['data_valid'] = False  # Set True after first real voltage read
    web_data['temperatures'] = [None] * total_channels
    web_data['bank_summaries'] = [{'median': 0.0, 'min': 0.0, 'max': 0.0, 'invalid': 0} for _ in range(NUM_BANKS)]
    web_data['charge_state'] = 'Idle'
    web_data['effective_charge_current'] = settings.get('dvcc_max_charge_current', 200.0)
    web_data['hv_clamp'] = False
    web_data['hv_clamped_cvl'] = 0.0
    web_data['_web_server_healthy'] = True
    # Build indices.
    for bat in range(number_parallel):
        base = bat * sensors_per_battery
        for bank_id in range(NUM_BANKS):
            bank_base = base + bank_id * sensors_per_bank
            BANK_SENSOR_INDICES[bank_id].extend(range(bank_base, bank_base + sensors_per_bank))
    # Setup.
    setup_hardware(settings)
    time.sleep(1) # Short delay to allow hardware initialization
    # Initialize communication statistics
    init_comm_stats(slave_addresses)
    # Web.
    # Cerbo integration state persistence.
    _cerbo_state_file = os.path.join(data_dir, 'cerbo_integration_state')
    settings['cerbo_state_file'] = _cerbo_state_file
    if os.path.exists(_cerbo_state_file):
        try:
            with open(_cerbo_state_file) as _f:
                cerbo_integration_enabled = _f.read().strip() == 'enabled'
            logging.info(f'Cerbo integration state loaded: {"enabled" if cerbo_integration_enabled else "disabled"}')
        except Exception:
            pass  # fall back to default True
    start_web_server(settings)
    # Modbus TCP server for Victron Cerbo GX.
    if settings.get('enabled', True):
        start_modbus_server(settings)
    # Give Modbus slaves time to initialize before self-test
    logging.info("Waiting 10 seconds for Modbus slaves to initialize...")
    time.sleep(10)
    settings['_cerbo_last_seen'] = time.time()  # start 60 s offline-timer from BMS boot
    # Self-test.
    startup_self_test(settings, stdscr, data_dir)
    # Signal handler.
    signal.signal(signal.SIGINT, signal_handler)
    # SIGHUP: hot-reload DVCC settings from battery_monitor.ini without restarting.
    def sighup_handler(signum, frame):
        try:
            import configparser as _cp
            _cfg = _cp.ConfigParser()
            _cfg.read(os.path.join(data_dir, 'battery_monitor.ini'))
            settings['dvcc_max_charge_voltage']    = _cfg.getfloat('DVCC', 'max_charge_voltage',    fallback=settings['dvcc_max_charge_voltage'])
            settings['dvcc_min_discharge_voltage'] = _cfg.getfloat('DVCC', 'min_discharge_voltage', fallback=settings['dvcc_min_discharge_voltage'])
            settings['dvcc_max_charge_current']    = _cfg.getfloat('DVCC', 'max_charge_current',    fallback=settings['dvcc_max_charge_current'])
            settings['dvcc_max_discharge_current'] = _cfg.getfloat('DVCC', 'max_discharge_current', fallback=settings['dvcc_max_discharge_current'])
            # cable_drop_compensation intentionally not reloaded — relearned each session
            settings['discharge_cable_drop']       = _cfg.getfloat('DVCC', 'discharge_cable_drop', fallback=settings.get('discharge_cable_drop', 0.0))
            settings['temp_derate_start']          = _cfg.getfloat('DVCC', 'temp_derate_start', fallback=settings.get('temp_derate_start', 38.0))
            settings['temp_derate_end']            = _cfg.getfloat('DVCC', 'temp_derate_end', fallback=settings.get('temp_derate_end', 45.0))
            settings['cold_charge_cutoff']         = _cfg.getfloat('DVCC', 'cold_charge_cutoff', fallback=settings.get('cold_charge_cutoff', 5.0))
            settings['cold_charge_min']            = _cfg.getfloat('DVCC', 'cold_charge_min', fallback=settings.get('cold_charge_min', 0.0))
            logging.info(f"SIGHUP: DVCC reloaded — max_charge_voltage={settings['dvcc_max_charge_voltage']}V "
                         f"max_charge_current={settings['dvcc_max_charge_current']}A")
        except Exception as _e:
            logging.warning(f'SIGHUP config reload failed: {_e}')
    signal.signal(signal.SIGHUP, sighup_handler)
    # Watchdog.
    if settings['WatchdogEnabled'] and setup_watchdog(15):
        wd_thread = threading.Thread(target=watchdog_pet_thread, daemon=True)
        wd_thread.start()
        logging.info("Watchdog pet thread started.")
    else:
        logging.info("Watchdog disabled or setup failed.")
    # Init previous.
    previous_temps = [None] * total_channels
    previous_bank_medians = [0.0] * NUM_BANKS
    alive_timestamp = time.time()
    # Main loop.
    while True:
        # Temps alerts.
        temps_alerts = [] # List to collect any temperature problems we find
        all_raw_temps = [] # Will hold all raw temperature readings from all sensors
        # Read temps per slave with delay between each slave for reliable RS485 communication.
        for i, addr in enumerate(slave_addresses):
            # Update alive timestamp for watchdog during temp reads
            alive_timestamp = time.time()
            # Add delay between slaves (except before first slave)
            if i > 0:
                inter_delay = settings.get('inter_slave_delay', 0.5)
                logging.debug(f"Inter-slave delay: {inter_delay}s before slave {addr}")
                time.sleep(inter_delay)
            
            temp_result = read_ntc_sensors(
                settings['ip'], settings['modbus_port'], settings['query_delay'],
                sensors_per_battery, settings['scaling_factor'],
                settings['max_retries'], settings['retry_backoff_base'], slave_addr=addr,
                slave_ports=settings.get('modbus_slave_ports'),
                slave_addresses=settings['modbus_slave_addresses'],
                slave_ips=settings.get('modbus_slave_ips', [])
            )
            if isinstance(temp_result, str):
                temps_alerts.append(f"Modbus slave {addr} failed: {temp_result}")
                all_raw_temps.extend([settings['valid_min']] * sensors_per_battery)
            else:
                all_raw_temps.extend(temp_result)
        raw_temps = all_raw_temps
        # Load or create temperature calibration offsets.
        # perform_calibration() handles all logic:
        # - If offsets.txt exists, load existing (never auto-recalculate)
        # - If offsets.txt doesn't exist, calculate and save (commissioning)
        # - Returns (median, offsets) tuple
        startup_median, startup_offsets = perform_calibration(settings, raw_temps, data_dir)
        startup_set = startup_offsets is not None
        # Apply offsets.
        calibrated_temps = [raw_temps[i] + startup_offsets[i] if startup_set and raw_temps[i] is not None and raw_temps[i] > settings['valid_min'] else raw_temps[i] if raw_temps[i] is not None and raw_temps[i] > settings['valid_min'] else None for i in range(total_channels)]
        # Bank stats.
        bank_stats = compute_bank_medians(calibrated_temps, settings['valid_min'])
        bank_medians = [s['median'] for s in bank_stats]
        # Check static anomalies.
        for ch, raw in enumerate(raw_temps, 1):
            if check_invalid_reading(raw, ch, temps_alerts, settings['valid_min'], settings):
                continue
            calib = calibrated_temps[ch-1]
            bank_id = get_bank_for_channel(ch)
            bank_median = bank_medians[bank_id - 1]
            check_high_temp(calib, ch, temps_alerts, settings['high_threshold'], settings)
            check_low_temp(calib, ch, temps_alerts, settings['low_threshold'], settings)
            check_deviation(calib, bank_median, ch, temps_alerts, settings['abs_deviation_threshold'], settings['deviation_threshold'], settings)
        # Dynamic checks if not first run.
        if run_count > 0 and previous_temps and previous_bank_medians is not None:
            for bank_id in range(1, NUM_BANKS + 1):
                bank_median_rise = bank_medians[bank_id - 1] - previous_bank_medians[bank_id - 1]
                bank_indices = BANK_SENSOR_INDICES[bank_id - 1]
                for i in bank_indices:
                    ch = i + 1
                    calib = calibrated_temps[i]
                    if calib is not None:
                        check_abnormal_rise(calib, previous_temps, ch, temps_alerts, settings['poll_interval'], settings['rise_threshold'], settings)
                        check_group_tracking_lag(calib, previous_temps, bank_median_rise, ch, temps_alerts, settings['disconnection_lag_threshold'], settings)
                    check_sudden_disconnection(calib, previous_temps, ch, temps_alerts, settings)
        # Update previous.
        previous_temps = calibrated_temps[:]
        previous_bank_medians = bank_medians[:]
        # Overall median.
        valid_calib_temps = [t for t in calibrated_temps if t is not None]
        try:
            overall_median = statistics.median(valid_calib_temps) if valid_calib_temps else 0.0
        except (TypeError, statistics.StatisticsError) as e:
            logging.warning(f"Error calculating overall median: {e}, using 0.0")
            overall_median = 0.0
        # Fan for cabinet overheat.
        if overall_median > settings['cabinet_over_temp_threshold']:
            if GPIO:
                GPIO.output(settings['FanRelayPin'], GPIO.HIGH)
            logging.info(f"Cabinet over temp: {overall_median:.1f}°C > {settings['cabinet_over_temp_threshold']}°C. Fan activated.")
            if not any("Cabinet over temp" in a for a in temps_alerts):
                temps_alerts.append(f"Cabinet over temp: {overall_median:.1f}°C > {settings['cabinet_over_temp_threshold']}°C. Fan on.")
                event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: Cabinet over temp: {overall_median:.1f}°C > {settings['cabinet_over_temp_threshold']}°C. Fan on.")
                if len(event_log) > settings.get('EventLogSize', 20):
                    event_log.pop(0)
        else:
            if GPIO:
                GPIO.output(settings['FanRelayPin'], GPIO.LOW)
            logging.info("Cabinet temp normal. Fan deactivated.")
        # Read voltages.
        battery_voltages = []
        for i in range(1, NUM_BANKS + 1):
            v, _, _ = read_voltage_with_retry(i, settings) # Read voltage with error handling
            battery_voltages.append(v if v is not None else 0.0) # Use 0.0 if reading failed
        # Check issues.
        alert_needed, all_alerts = check_for_issues(battery_voltages, temps_alerts, settings)
        # Update RRD.
        timestamp = int(time.time())
        values = f"{timestamp}:{overall_median}:{':'.join(map(str, battery_voltages))}"
        # Only spawn rrdtool every 30 s -- RRD step is 60 s so more frequent
        # updates just burn CPU and RAM on the Pi 2B for no extra benefit.
        _rrd_now = int(time.time())
        if _rrd_now - settings.get('_rrd_last_update', 0) >= 30:
            subprocess.call(['rrdtool', 'update', RRD_FILE, values])
            settings['_rrd_last_update'] = _rrd_now
            logging.debug(f"RRD updated with: {values}")
        else:
            logging.debug(f"RRD update skipped ({_rrd_now - settings.get('_rrd_last_update', 0)}s since last write)")
        # Reset balancing_active if balancer_failed prevents us from processing it
        if balancer_failed and balancing_active:
            logging.warning("Resetting balancing_active flag - balancer_failed prevents balancing")
            balancing_active = False
        # Auto-recovery: Reset balancer_failed after cooldown period to allow retry.
        # Cooldown escalates: 300s (5min) for first failure, 1800s (30min) after 3+ consecutive failures.
        if balancer_failed and balancer_failed_time is not None:
            cooldown = 1800 if balancer_fail_count >= 3 else 300
            elapsed = time.time() - balancer_failed_time
            if elapsed >= cooldown:
                logging.warning(f"Auto-recovery: Resetting balancer_failed after {elapsed:.0f}s cooldown "
                              f"(fail_count={balancer_fail_count}, cooldown was {cooldown}s). "
                              f"Will retry balancing on next cycle.")
                event_log.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: Auto-recovery: balancer reset after {cooldown}s cooldown (failures: {balancer_fail_count})")
                if len(event_log) > settings.get('EventLogSize', 20):
                    event_log.pop(0)
                balancer_failed = False
                balancer_failed_time = None
                balancer_fail_reason = ""
        # Balance decision.
        if len(battery_voltages) == NUM_BANKS and not balancer_failed:
            max_v = max(battery_voltages) # Find highest voltage bank
            min_v = min(battery_voltages) # Find lowest voltage bank
            high_b = battery_voltages.index(max_v) + 1 # Bank number with highest voltage
            low_b = battery_voltages.index(min_v) + 1 # Bank number with lowest voltage
            current_time = time.time()
            any_low_temp = any(t is not None and t < settings["heating_threshold"] for t in calibrated_temps)
            min_src_v = settings.get('min_balance_source_voltage', 17.0)
            # Guard: Dont balance a bank to itself
            if high_b == low_b:
                logging.debug(f"Skipping balance: source and dest are same bank ({high_b})")
            elif max_v < min_src_v:
                logging.warning(f"Skipping balance: source {max_v:.2f}V < min {min_src_v:.1f}V for DC-DC")
            # Condition.
            elif balancing_active or (not alert_needed and (any_low_temp or max_v - min_v > settings['VoltageDifferenceToBalance']) and min_v > 0 and current_time - last_balance_time > settings['BalanceRestPeriodSeconds']):
                is_heating = any_low_temp
                balance_battery_voltages(stdscr, high_b, low_b, settings, temps_alerts, is_heating=is_heating) # Transfer charge
                balancing_active = False
        # Update web data (locked).
        with data_lock:
            web_data['voltages'] = battery_voltages
            web_data['data_valid'] = True
            web_data['temperatures'] = calibrated_temps
            web_data['bank_summaries'] = bank_stats
            web_data['alerts'] = all_alerts
            web_data['balancing'] = balancing_active
            web_data['last_update'] = time.time()
            web_data['system_status'] = 'Alert' if alert_needed else 'Running'
        # Feature 1+2: Temperature-derated and cold-limited charge current
        _valid_temps = [t for t in calibrated_temps if t is not None]
        _max_temp = max(_valid_temps) if _valid_temps else 25.0
        _min_temp = min(_valid_temps) if _valid_temps else 25.0
        _base_current = settings.get('dvcc_max_charge_current', 200.0)
        _eff_current = _base_current
        _derate_start = settings.get('temp_derate_start', 38.0)
        _derate_end = settings.get('temp_derate_end', 45.0)
        if _max_temp >= _derate_end:
            _eff_current = 0.0
        elif _max_temp > _derate_start and _derate_end > _derate_start:
            _hot_ratio = (_max_temp - _derate_start) / (_derate_end - _derate_start)
            _eff_current = _base_current * (1.0 - _hot_ratio)
        _cold_cutoff = settings.get('cold_charge_cutoff', 5.0)
        _cold_min = settings.get('cold_charge_min', 0.0)
        if _min_temp <= _cold_min:
            _eff_current = 0.0
        elif _min_temp < _cold_cutoff and _cold_cutoff > _cold_min:
            _cold_ratio = (_cold_cutoff - _min_temp) / (_cold_cutoff - _cold_min)
            _eff_current = min(_eff_current, _base_current * (1.0 - _cold_ratio))
        settings['_effective_charge_current'] = round(max(0.0, _eff_current), 1)

        # Feature 3: Per-bank high-voltage cutoff
        _hvt = settings.get('HighVoltageThresholdPerBattery', 21.0)
        if battery_voltages and max(battery_voltages) >= _hvt:
            settings['_hv_clamp'] = True
            # Set CVL to normal target (no cable drop) so charger sees CVL < current pack → stops charging.
            # Previously used sum(battery_voltages) which was at/above normal CVL and let charger maintain elevated voltage.
            settings['_hv_clamped_cvl'] = settings.get('dvcc_max_charge_voltage', 61.0)
        else:
            settings['_hv_clamp'] = False
            settings['_hv_clamped_cvl'] = 0.0

        # Feature 6: Charge state detection
        _pack_v = sum(v for v in battery_voltages if v > 0) if battery_voltages else 0.0
        _cv_target = settings.get('dvcc_max_charge_voltage', 61.0)
        _v_history = settings.get('_v_history', [])
        _v_history.append(_pack_v)
        if len(_v_history) > 5: _v_history = _v_history[-5:]
        settings['_v_history'] = _v_history
        _v_trend = (_v_history[-1] - _v_history[0]) if len(_v_history) >= 2 else 0.0
        if _pack_v <= 0:
            _charge_state = 'Idle'
        elif _v_trend < -0.1:
            _charge_state = 'Discharging'
        elif _v_trend > 0.1 and _pack_v < _cv_target - 2.0:
            _charge_state = 'Bulk'
        elif _pack_v >= _cv_target - 0.5 and abs(_v_trend) <= 0.15:
            _charge_state = 'Float'
        elif _pack_v >= _cv_target - 2.0:
            _charge_state = 'Absorption'
        else:
            _charge_state = 'Idle'
        settings['_charge_state'] = _charge_state
        with data_lock:
            web_data['charge_state'] = _charge_state
            web_data['effective_charge_current'] = settings['_effective_charge_current']
            web_data['hv_clamp'] = settings.get('_hv_clamp', False)
            web_data['hv_clamped_cvl'] = settings.get('_hv_clamped_cvl', 0.0)

        # Auto cable drop compensation: read the Cerbo GX's own DC bus voltage (the MultiPlus
        # terminals) and compare it to the BMS battery terminal voltage.  This gives the TRUE
        # cable-drop without the positive-feedback problem of the old CVL-minus-BMS approach.
        #
        # Physical direction:
        #   Charging  → Multi output > Battery terminal  → cerbo_dc > bms_total (drop > 0)
        #   Discharging → Battery terminal > Multi input  → cerbo_dc < bms_total (skip)
        #
        # Max compensation capped at 1.0 V to prevent dangerous overvoltage.
        # Not persisted to INI: relearns each session from zero.
        # Don't learn during active balancing or voltage alerts.
        _volt_alert = any(v is not None and (v <= 0 or v > settings.get('HighVoltageThresholdPerBattery', 21.5) or v < settings.get('LowVoltageThresholdPerBattery', 16.5)) for v in battery_voltages)
        # Hard-clamp any previously accumulated over-compensation immediately
        if settings.get('cable_drop_compensation', 0.0) > 1.0:
            logging.warning(f"Cable drop clamped from {settings['cable_drop_compensation']:.3f}V to 1.0V hard cap")
            settings['cable_drop_compensation'] = 1.0
        if not balancing_active and battery_voltages and not _volt_alert and not settings.get("_hv_clamp", False):
            _bms_total = sum(v for v in battery_voltages if v > 0)
            _target = settings['dvcc_max_charge_voltage']
            _old_drop = settings.get('cable_drop_compensation', 0.0)
            _cerbo_dc_v = None  # fix: initialise before branching to prevent NameError in decay branch
            # Decay immediately if battery already above target (compensation wound too high)
            if _bms_total > _target + 0.3:
                _new_drop = round(0.9 * _old_drop, 3)
                settings['cable_drop_compensation'] = max(0.0, _new_drop)
                logging.info(f'Cable drop decayed (battery above target): {_old_drop:.3f}V -> {_new_drop:.3f}V')
            else:
                # Read the Cerbo GX DC bus voltage (MultiPlus terminal, unit 227 reg 26, 0.01V)
                _cerbo_dc_v = read_cerbo_dc_voltage(settings.get('cerbo_ip', '192.168.15.67'))
                if _cerbo_dc_v is not None and _cerbo_dc_v > _bms_total:
                    # Charging confirmed: Cerbo DC > BMS terminal
                    # Only update in CV mode: battery within 2V of target AND voltage stable
                    if _bms_total >= _target - 2.0 and abs(_v_trend) < 0.15:
                        _measured = _cerbo_dc_v - _bms_total   # real physical cable drop
                        _new_drop = round(0.1 * _measured + 0.9 * _old_drop, 3)
                        _new_drop = max(0.0, min(1.0, _new_drop))  # hard cap at 1V
                        if abs(_new_drop - _old_drop) >= 0.005:
                            settings['cable_drop_compensation'] = _new_drop
                            logging.debug(f'Cable drop updated: {_old_drop:.3f}V -> {_new_drop:.3f}V '
                                          f'(cerbo_dc={_cerbo_dc_v:.2f}V bms={_bms_total:.2f}V trend={_v_trend:.3f}V)')
                elif _cerbo_dc_v is None:
                    logging.debug('Cable drop: Cerbo DC voltage unavailable, skipping update')
        # -- Cerbo connectivity tracking (fix: outside HV-clamp guard) ----
        # Runs every poll regardless of HV clamp or volt-alert state.
        # read_cerbo_dc_voltage has a 5 s TTL cache so no extra network traffic.
        if not balancing_active and battery_voltages:
            _cerbo_track_v = read_cerbo_dc_voltage(settings.get('cerbo_ip', '192.168.15.67'))
            if _cerbo_track_v is not None:
                settings['_cerbo_last_seen'] = time.time()
                all_alerts = [a for a in all_alerts if 'Cerbo GX unreachable' not in a]
            else:
                _cerbo_offline_s = time.time() - settings.get('_cerbo_last_seen', time.time())
                if _cerbo_offline_s > 60:
                    _cerbo_msg = f'Cerbo GX unreachable for {int(_cerbo_offline_s)}s -- CVL frozen'
                    all_alerts = [_cerbo_msg if 'Cerbo GX unreachable' in a else a for a in all_alerts]
                    if not any('Cerbo GX unreachable' in a for a in all_alerts):
                        all_alerts.append(_cerbo_msg)
                        logging.warning(_cerbo_msg)
            with data_lock:
                web_data['alerts'] = all_alerts
        # Draw TUI.
        draw_tui(
            stdscr, battery_voltages, calibrated_temps, raw_temps,
            startup_offsets or [0]*total_channels, bank_stats,
            startup_median, all_alerts, settings, startup_set, is_startup=(run_count == 0)
        )
        # Update alive.
        alive_timestamp = time.time() # Update aliveness for watchdog thread
        run_count += 1
        # Cleanup.
        gc.collect()
        logging.info("Poll cycle complete.")
        # Sleep.
        time.sleep(settings['poll_interval'])
      
if __name__ == '__main__':
    # Arg parser.
    parser = argparse.ArgumentParser(description='Battery Management System')
    parser.add_argument('--validate-config', action='store_true', help='Validate configuration and exit')
    parser.add_argument('--data-dir', default='.', help='Directory containing config files')
    args = parser.parse_args()
    data_dir = args.data_dir
    # If validate.
    if args.validate_config:
        try:
            config_parser.read(os.path.join(data_dir, 'battery_monitor.ini'))
            settings = load_config(data_dir)
            validate_config(settings)
            print("Configuration validation passed.")
            sys.exit(0)
        except Exception as e:
            print(f"Configuration validation failed: {e}")
            sys.exit(1)
    else:
        # Setup logging with rotation: 10MB per file, keep 10 files (~100MB max).
        from logging.handlers import RotatingFileHandler as _RFH
        _log_path = os.path.join(data_dir, 'battery_monitor.log')
        _handler = _RFH(_log_path, maxBytes=10*1024*1024, backupCount=10)
        _handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
        logging.getLogger().setLevel(logging.INFO)
        logging.getLogger().addHandler(_handler)
        # Read config.
        config_parser.read(os.path.join(data_dir, 'battery_monitor.ini'))
        # RRD path.
        RRD_FILE = os.path.join(data_dir, 'bms.rrd')
        # Run with curses wrapper (handles init/cleanup).
        curses.wrapper(main)

# **Git Workflow (Source of Truth = Production/Pi):**
# - Production (Pi /projects/battery_balancer) is the source of truth
# - GitHub is a backup/reference, NOT the source of truth
# - NEVER pull from GitHub to overwrite production files
# - ALWAYS commit from production to GitHub after changes
#
# **Commands to commit INI or code changes from Pi to GitHub:**
# 1. SSH to Pi: ssh root@192.168.15.137 (password: battery_base)
# 2. Navigate to project: cd /projects/battery_balancer
# 3. Stage changes: git add battery_monitor.ini
# 4. Commit: git commit -m "Your commit message describing the change"
# 5. Push to GitHub: git push origin main
#
# **Example workflow:**
# cd /projects/battery_balancer
# git add battery_monitor.ini
# git commit -m "Update voltage thresholds for better balancing"
# git push origin main
#
# **Note:** Ensure you're on the 'main' branch (not 'master')
# GitHub repository: git@github.com:erkel1/bms.git
