#!/bin/bash

# Stop Picframe - handle both the TCL unbuffer process and Python picframe process

# Find the Python picframe process
python_process_id=$(pgrep -f "python3.*picframe")
if [ -n "$python_process_id" ]; then
    echo "Found picframe Python process (PID: $python_process_id), sending SIGTERM..."
    sudo kill -TERM $python_process_id
    echo "Waiting for graceful shutdown..."

    # Find and stop the TCL unbuffer process that was running picframe (if it exists)
    unbuffer_process_id=$(pgrep -f "tclsh8.6.*unbuffer.*picframe")
    if [ -n "$unbuffer_process_id" ]; then
        echo "Found picframe unbuffer process (PID: $unbuffer_process_id), sending SIGTERM..."
        sudo kill $unbuffer_process_id
        sleep 2
    fi

    # Check if still running
    if ps -p $unbuffer_process_id > /dev/null; then
        echo "Unbuffer process did not terminate gracefully, killing forcefully..."
        sudo kill -9 $unbuffer_process_id
    else
        echo "Unbuffer process terminated gracefully."
    fi

    sleep 2
    # Check if still running
    if ps -p $python_process_id > /dev/null; then
        echo "Python process did not terminate gracefully, killing forcefully..."
        sudo kill -9 $python_process_id
    else
        echo "Python process terminated gracefully."
    fi
fi

# Final cleanup - look for any remaining picframe-related processes
script_pid=$$
# Get all picframe processes and filter out this script
all_picframe_pids=$(pgrep -f "picframe")
remaining_processes=$(echo "$all_picframe_pids" | grep -v "^$script_pid$")
if [ -n "$remaining_processes" ]; then
    echo "Warning: Some picframe-related processes may still be running: $remaining_processes"
    echo "You may need to restart the system: sudo reboot"
else
    echo "All picframe processes have been stopped."
fi

echo -e "\x1b[?7h"                                                          # Re-enable cursor visibility