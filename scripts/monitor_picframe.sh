#!/bin/bash

# Picframe Monitoring Script
# Monitors picframe process and optionally auto-restarts on crash

set -e

# Configuration
LOG_FILE="$HOME/picframe_monitor.log"
AUTO_RESTART=true
CHECK_INTERVAL=30  # seconds
MAX_RESTARTS=5
RESTART_COOLDOWN=300  # seconds between restarts

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# State variables
restart_count=0
last_restart_time=0
monitoring=true

# Function to log messages
log_message() {
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo -e "$1"
    echo "[$timestamp] $1" >> "$LOG_FILE"
}

# Function to check if picframe is running
check_picframe() {
    if pgrep -f "python3.*picframe" > /dev/null; then
        return 0  # Running
    else
        return 1  # Not running
    fi
}

# Function to get picframe process info
get_picframe_info() {
    local pid=$(pgrep -f "python3.*picframe")
    if [ -n "$pid" ]; then
        local memory=$(ps -o %mem --no-headers -p "$pid" 2>/dev/null || echo "N/A")
        local cpu=$(ps -o %cpu --no-headers -p "$pid" 2>/dev/null || echo "N/A")
        local uptime=$(ps -o etime --no-headers -p "$pid" 2>/dev/null || echo "N/A")
        echo "PID: $pid, Memory: ${memory}%, CPU: ${cpu}%, Uptime: $uptime"
    else
        echo "Not running"
    fi
}

# Function to restart picframe
restart_picframe() {
    local current_time=$(date +%s)
    local time_since_last=$((current_time - last_restart_time))
    
    # Check cooldown period
    if [ $time_since_last -lt $RESTART_COOLDOWN ]; then
        local remaining=$((RESTART_COOLDOWN - time_since_last))
        log_message "${YELLOW}Restart cooldown active. Waiting ${remaining}s before next restart.${NC}"
        return 1
    fi
    
    # Check max restart limit
    if [ $restart_count -ge $MAX_RESTARTS ]; then
        log_message "${RED}Maximum restart attempts reached (${MAX_RESTARTS}). Manual intervention required.${NC}"
        return 1
    fi
    
    log_message "${YELLOW}Attempting to restart picframe... (attempt $((restart_count + 1))/${MAX_RESTARTS})${NC}"
    
    # Kill existing processes
    pkill -f "python3.*picframe" 2>/dev/null || true
    pkill -f "tclsh.*unbuffer.*picframe" 2>/dev/null || true
    
    # Wait a moment
    sleep 2
    
    # Start picframe
    cd "$HOME/src/picframe" || {
        log_message "${RED}Failed to change to picframe directory${NC}"
        return 1
    }
    
    # Start in background
    nohup python3 src/picframe/scripts/picframe > "$HOME/picframe_restart.log" 2>&1 &
    
    # Wait to see if it starts successfully
    sleep 5
    
    if check_picframe; then
        log_message "${GREEN}Picframe restarted successfully${NC}"
        restart_count=$((restart_count + 1))
        last_restart_time=$current_time
        return 0
    else
        log_message "${RED}Failed to restart picframe${NC}"
        return 1
    fi
}

# Function to show status
show_status() {
    echo -e "${BLUE}=== Picframe Monitor Status ===${NC}"
    echo "Monitoring: $([ "$monitoring" = true ] && echo "Active" || echo "Stopped")"
    echo "Auto-restart: $([ "$AUTO_RESTART" = true ] && echo "Enabled" || echo "Disabled")"
    echo "Check interval: ${CHECK_INTERVAL}s"
    echo "Restart count: $restart_count/$MAX_RESTARTS"
    echo "Last restart: $([ $last_restart_time -gt 0 ] && date -d @$last_restart_time || echo "Never")"
    echo "Current status: $(get_picframe_info)"
    echo "Log file: $LOG_FILE"
}

# Function to show help
show_help() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  -h, --help          Show this help message"
    echo "  -s, --status        Show current status"
    echo "  -r, --restart       Manually restart picframe"
    echo "  -d, --disable       Disable auto-restart"
    echo "  -e, --enable        Enable auto-restart"
    echo "  -i, --interval N    Set check interval to N seconds (default: 30)"
    echo "  -l, --log           Show recent log entries"
    echo ""
    echo "Examples:"
    echo "  $0                  Start monitoring with auto-restart"
    echo "  $0 --status         Show current status"
    echo "  $0 --restart        Manually restart picframe"
    echo "  $0 --interval 60    Check every 60 seconds"
}

# Function to show recent logs
show_logs() {
    if [ -f "$LOG_FILE" ]; then
        echo -e "${BLUE}=== Recent Monitor Logs ===${NC}"
        tail -20 "$LOG_FILE"
    else
        echo "No log file found: $LOG_FILE"
    fi
}

# Parse command line arguments
case "${1:-}" in
    -h|--help)
        show_help
        exit 0
        ;;
    -s|--status)
        show_status
        exit 0
        ;;
    -r|--restart)
        if restart_picframe; then
            echo "Manual restart successful"
        else
            echo "Manual restart failed"
            exit 1
        fi
        exit 0
        ;;
    -d|--disable)
        AUTO_RESTART=false
        log_message "${YELLOW}Auto-restart disabled${NC}"
        ;;
    -e|--enable)
        AUTO_RESTART=true
        log_message "${GREEN}Auto-restart enabled${NC}"
        ;;
    -i|--interval)
        if [ -n "$2" ] && [ "$2" -gt 0 ]; then
            CHECK_INTERVAL="$2"
            log_message "${BLUE}Check interval set to ${CHECK_INTERVAL}s${NC}"
        else
            echo "Error: Invalid interval value"
            exit 1
        fi
        ;;
    -l|--log)
        show_logs
        exit 0
        ;;
    "")
        # No arguments, start monitoring
        ;;
    *)
        echo "Unknown option: $1"
        show_help
        exit 1
        ;;
esac

# Start monitoring
log_message "${BLUE}Starting picframe monitor...${NC}"
log_message "Check interval: ${CHECK_INTERVAL}s"
log_message "Auto-restart: $([ "$AUTO_RESTART" = true ] && echo "Enabled" || echo "Disabled")"
log_message "Max restarts: $MAX_RESTARTS"
log_message "Restart cooldown: ${RESTART_COOLDOWN}s"

# Main monitoring loop
while [ "$monitoring" = true ]; do
    if check_picframe; then
        # Picframe is running
        if [ $restart_count -gt 0 ]; then
            log_message "${GREEN}Picframe is running normally${NC}"
            restart_count=0  # Reset counter on successful run
        fi
    else
        # Picframe is not running
        log_message "${RED}Picframe is not running!${NC}"
        
        if [ "$AUTO_RESTART" = true ]; then
            if restart_picframe; then
                log_message "${GREEN}Auto-restart successful${NC}"
            else
                log_message "${RED}Auto-restart failed${NC}"
            fi
        else
            log_message "${YELLOW}Auto-restart disabled. Manual intervention required.${NC}"
        fi
    fi
    
    # Wait before next check
    sleep "$CHECK_INTERVAL"
done

log_message "${BLUE}Picframe monitor stopped${NC}"


