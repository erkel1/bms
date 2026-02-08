#!/bin/bash
# Start BMS on console with auto-restart
# Runs on tty1 and restarts if it crashes

LOG_FILE="/tmp/bms_console.log"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
}

log "Starting BMS on console..."

# Change to the battery_balancer directory
cd /projects/battery_balancer

# Loop to restart if the script exits
while true; do
    log "Starting bms.py..."
    
    # Run bms.py on tty1 - this will show on the console display
    python3 bms.py 2>&1 | tee -a "$LOG_FILE"
    
    EXIT_CODE=$?
    log "bms.py exited with code $EXIT_CODE"
    
    if [ $EXIT_CODE -eq 0 ]; then
        log "bms.py exited normally, restarting..."
    else
        log "bms.py crashed with exit code $EXIT_CODE, restarting in 5 seconds..."
    fi
    
    sleep 5
done
