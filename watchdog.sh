#!/bin/bash

# --- Configuration ---
APP_DIR="/home/mtrapp/appl/automated-survey-flask"
# Use HTTPS for the check because the server is running with SSL
URL="https://127.0.0.1:5000/" 
CERT="cert/emolter_fullchain.pem"
KEY="cert/emolter.key"
WATCHDOG_LOG="$APP_DIR/log/watchdog.log"
SERVER_OUT="$APP_DIR/log/server.out"

# Ensure the log directory exists
mkdir -p "$APP_DIR/log"

# --- 1. The "Anti-Zombie" Health Check ---
# curl checks if the app is RESPONDING, not just if the process exists.
# --insecure: ignores self-signed/local cert errors.
# --max-time 5: kills the check if the app is hanging, triggering the restart.
HTTP_STATUS=$(curl -s -o /dev/null -L -w "%{http_code}" --insecure --max-time 5 "$URL" || echo "000")

if [ "$HTTP_STATUS" == "200" ]; then
    # Server is healthy.
    exit 0
else
    echo "$(date): Server DOWN or HANGING (Status: $HTTP_STATUS). Cleaning up..." >> "$WATCHDOG_LOG"

    # --- 2. Environment Setup ---
    shopt -s expand_aliases
    source /home/mtrapp/.bashrc

    # --- 3. Forceful Cleanup ---
    # Kill whatever is holding port 5000 (clears the socket)
    fuser -k 5000/tcp > /dev/null 2>&1
    # Kill the specific flask process
    pkill -u mtrapp -f "flask run"

    sleep 2

    # --- 4. Restart ---
    cd "$APP_DIR"
    if [ -f "venv_38/bin/activate" ]; then
        source venv_38/bin/activate

        # Start using the python module directly for better stability
        nohup python3 -m flask run --host=0.0.0.0 --port=5000 \
            --cert="$CERT" --key="$KEY" >> "$SERVER_OUT" 2>&1 &

        if [ $? -eq 0 ]; then
            echo "$(date): Server restart command issued successfully." >> "$WATCHDOG_LOG"
        else
            echo "$(date): ERROR: Restart command failed to execute." >> "$WATCHDOG_LOG"
        fi
    else
        echo "$(date): ERROR: Virtualenv not found!" >> "$WATCHDOG_LOG"
    fi
fi

