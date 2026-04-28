#!/bin/bash

# Check if anything is listening on port 5000
if lsof -i:5000 > /dev/null; then
    echo "$(date): Server is running."
else
    echo "$(date): Server is DOWN. Restarting..."
    
    # 1. Expand aliases in the script
    shopt -s expand_aliases
    # 2. Source your bash configuration (adjust path if using .zshrc or similar)
    # We use the 'source' command to bring in your aliases
    source ~/.bashrc

    cd /home/mtrapp/appl/automated-survey-flask; source venv_38/bin/activate

    # Optional: Run stop command first to ensure a clean stat
    pkill -f "python manage.py runserver"
    fuser -k 5000/tcp
    #stopEserver
    
    # Wait a moment for processes to clear
    sleep 2
    
    # Start the server
    python manage.py runserver >> server.out &
    #startEServer
    
    if [ $? -eq 0 ]; then
        echo "$(date): Server started successfully."
    else
        echo "$(date): Failed to start server."
    fi
fi
