  Battery Balancer Script README body { font-family: Arial, sans-serif; line-height: 1.6; max-width: 800px; margin: 0 auto; padding: 20px; } h1, h2, h3 { color: #333; } code { background-color: #f4f4f4; padding: 2px 4px; border-radius: 4px; font-family: 'Courier New', Courier, monospace; } pre { background-color: #f4f4f4; padding: 10px; border-radius: 4px; overflow: auto; } ul { list-style-type: square; }

Battery Balancer Script
=======================

A comprehensive Python-based Battery Management System (BMS) designed for monitoring and balancing multiple lithium battery cells using Raspberry Pi. The system provides real-time monitoring via both terminal-based user interface (TUI) and web dashboard, with advanced temperature and voltage management capabilities.

Features
--------

*   **Real-time Voltage Monitoring**: Monitors the voltage of each battery bank in real-time using ADS1115 ADC.
*   **Temperature Monitoring**: Monitors temperatures from multiple NTC sensors via Modbus TCP.
*   **Automatic Balancing**: Intelligently balances charge between battery banks when voltage disparities exceed thresholds.
*   **Dual User Interfaces**: Terminal-based TUI with ASCII art and web dashboard with interactive charts.
*   **Alarm System**: Alerts via email and hardware relay when cells reach critical voltage/temperature levels.
*   **Hardware Watchdog**: Automatically restarts the system if it becomes unresponsive.
*   **Time-Series Logging**: Persistent storage of voltage and temperature history using RRDTool.
*   **Comprehensive Modbus Tool**: Unified utility for Modbus device testing, configuration, and debugging.

Hardware Requirements
---------------------

*   Raspberry Pi (with Python 3 installed)
*   ADS1115 ADC for voltage measurement
*   M5Stack 4Relay module for relay control
*   DC-DC converter for balancing
*   I2C multiplexer (PaHUB2)
*   Buzzer or LED for physical alarm indication

Software Requirements
---------------------

*   Python 3
*   Libraries:
    *   `smbus`
    *   `RPi.GPIO`
    *   `smtplib`
    *   `curses`
    *   `configparser`
    *   `logging`
    *   `threading`
    *   `os`, `signal`, `sys`

Installation
------------

1.  **Clone the Repository**:
    
        git clone [your-repository-url]
        cd battery-balancer
    
2.  **Install Dependencies**:
    
        sudo apt-get update
        sudo apt-get install -y python3-smbus python3-rpi.gpio python3-curses python3-configparser
    
3.  **Setup Configuration**:
    *   Edit `config.ini` with the correct hardware settings, email configurations, and operational parameters.
4.  **Run the Script**:
    
        python3 battery_balancer.py
    

Configuration
-------------

*   **config.ini**: This file contains all necessary configuration settings:
    *   `General`: Contains operational thresholds and timing.
    *   `I2C`: Addresses for I2C devices.
    *   `GPIO`: GPIO pin numbers for relay control.
    *   `Email`: SMTP settings for email alerts.
    *   `ADS1115`: Configuration for the ADC.

Usage
-----

*   **Monitor**: The script will run, showing a TUI where you can monitor battery voltages.
*   **Balancing**: If a voltage imbalance is detected, the script will initiate balancing automatically.

Modbus Tool
-----------

The project includes a comprehensive Modbus tool at `modbus/modbus_tool.py` that consolidates all Modbus-related functionality:

**Interactive Mode:**
```bash
python modbus/modbus_tool.py --interactive
```

**Command Line Mode:**
```bash
# Test connectivity
python modbus/modbus_tool.py --ip 192.168.15.245 --port 10001

# Read registers
python modbus/modbus_tool.py --ip 192.168.15.245 --slave 1 --func 3 --start 0 --count 24

# Read device settings
python modbus/modbus_tool.py --interactive
# Then select option 5 from the menu
```

**Features:**
*   **Slave Scanning**: Automatically detect active Modbus slaves (0-247)
*   **Register Reading**: Read holding/input registers with full CRC verification
*   **Register Writing**: Write single registers (e.g., change slave ID)
*   **Device Settings**: Read common NTC sensor configuration (baud rate, parity, etc.)
*   **Raw Commands**: Send custom Modbus commands for advanced debugging
*   **Debug Mode**: Detailed output for troubleshooting communication issues
*   **CRC Calculator**: Verify Modbus CRC checksums

Troubleshooting
---------------

*   **Check Logs**: All operations are logged in `battery_monitor.log`. Check this for any errors or issues.
*   **Hardware Check**: Ensure all connections are secure and hardware is functioning.
*   **Configuration**: Verify settings in `battery_monitor.ini` are correct for your setup.
*   **Modbus Testing**: Use the interactive modbus tool to test device connectivity and configuration.
*   **Network Issues**: Use the modbus tool's connectivity test to verify network communication.

Safety Notes
------------

*   **Do Not Overcharge**: Ensure your `ALARM_VOLTAGE_THRESHOLD` is set appropriately to avoid overcharging cells.
*   **Physical Inspection**: Regularly inspect physical connections and battery health.

License
-------

\[Your License Here\] - e.g., MIT License, GPL, etc.

Acknowledgements
----------------

*   Special thanks to \[Your Name or Team\] for the development of this script.
*   Thanks to the open-source community for the libraries and tools used in this project.

Contact
-------

For any issues or suggestions, please contact \[Your Contact Information\].

* * *

Feel free to contribute to this project by submitting pull requests or raising issues on the repository.