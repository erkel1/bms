#!/bin/bash
# Run BMS on console - sourced by profile for tty1 login

# Only run on tty1 (the main console)
if [ "$(tty)" = "/dev/tty1" ]; then
    cd /projects/battery_balancer
    
    # Wait for terminal to be ready
    sleep 1
    
    # Set terminal size to 60x200 - use script command to ensure it works
    stty -F /dev/tty1 rows 60 cols 200
    stty -F /dev/tty1 columns 200
    stty -F /dev/tty1 rows 60
    
    # Also set for stdin
    stty rows 60 cols 200 < /dev/tty1
    
    # Clear screen and show BMS
    clear
    echo "Starting Battery Management System..."
    echo "====================================="
    
    # Run BMS with auto-restart
    while true; do
        python3 bms.py
        echo "BMS exited, restarting in 5 seconds..."
        sleep 5
    done
fi
