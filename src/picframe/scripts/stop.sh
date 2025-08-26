#!/bin/bash

# Stop Picframe - handle both the TCL unbuffer process and Python picframe process

# Find the Python picframe process
python_process_id=$(pgrep -f "python3.*picframe")

# Find the unbuffer process first (parent process)
unbuffer_process_id=$(pgrep -f "tclsh8.6.*unbuffer.*picframe")

if [ -n "$python_process_id" ]; then
    
    echo "Found picframe Python process (PID: $python_process_id), sending SIGTERM..."
    sudo kill -TERM $python_process_id
    echo "Waiting for graceful shutdown (allow time for stop logs to be written)..."

    sleep 5  # Give time for graceful shutdown and log flushing
    
    # AFTER Python process has time to write logs, then stop the unbuffer process
    if [ -n "$unbuffer_process_id" ]; then
        echo "Now stopping picframe unbuffer process (PID: $unbuffer_process_id)..."
        sudo kill -TERM $unbuffer_process_id
    fi

    # Check if still running
    if ps -p $unbuffer_process_id > /dev/null; then
        echo "Unbuffer process did not terminate gracefully, killing forcefully..."
        sudo kill -9 $unbuffer_process_id
    else
        echo "Unbuffer process terminated gracefully."
    fi

    sleep 3  # Additional time for database connections to close

    # Check if still running, add more time for graceful shutdown
    if ps -p $python_process_id > /dev/null; then
        echo "Additional time for graceful shutdown..."
        sleep 10
    fi

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

# Clean up any orphaned SQLite journal files
echo "Checking for orphaned SQLite journal files..."
journal_files=$(find /home/pi/picframe_data/data -name "*.db3-journal" 2>/dev/null)
if [ -n "$journal_files" ]; then
    echo "Found orphaned journal files, removing them:"
    echo "$journal_files"
    rm -f $journal_files
    echo "Journal files cleaned up."
else
    echo "No orphaned journal files found."
fi

echo -e "\x1b[?7h"                                                          # Re-enable cursor visibility