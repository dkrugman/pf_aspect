#!/bin/bash

# Stop Picframe
process_id=$(pgrep -f ^python.*picframe$)
if [ -n "$process_id" ]; then
    echo "Found picframe process (PID: $process_id), sending SIGTERM..."
    sudo kill $process_id                                                   # send SIGTERM first
    sleep 2                                                                 # wait briefly for clean exit

    # Check if still running
    if ps -p $process_id > /dev/null; then
        echo "Process did not terminate gracefully, killing forcefully..."
        sudo kill -9 $process_id
        echo -e "\x1b[?7h"                                                  # Re-enable cursor visibility
    else
        echo "Process terminated gracefully."
        echo -e "\x1b[?7h"                                                  # Re-enable cursor visibility
    fi
else
    echo "No picframe process found."
fi