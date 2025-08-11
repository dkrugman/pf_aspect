#!/bin/bash

# Picframe Crash Investigation Script
# This script helps investigate picframe crashes by gathering comprehensive system information

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
INVESTIGATION_DIR="picframe_crash_investigation_$(date +%Y%m%d_%H%M%S)"
LOG_FILE="$INVESTIGATION_DIR/crash_investigation.log"
CRASH_DUMPS_DIR="$INVESTIGATION_DIR/crash_dumps"
SYSTEM_INFO_DIR="$INVESTIGATION_DIR/system_info"

echo -e "${BLUE}=== Picframe Crash Investigation Tool ===${NC}"
echo "Investigation directory: $INVESTIGATION_DIR"
echo ""

# Create investigation directory structure
mkdir -p "$INVESTIGATION_DIR" "$CRASH_DUMPS_DIR" "$SYSTEM_INFO_DIR"

# Function to log messages
log_message() {
    echo -e "$1"
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" >> "$LOG_FILE"
}

# Function to capture command output
capture_command() {
    local description="$1"
    local command="$2"
    local output_file="$3"
    
    log_message "${YELLOW}Capturing: $description${NC}"
    
    if eval "$command" > "$output_file" 2>&1; then
        log_message "${GREEN}✓ Captured: $description${NC}"
    else
        log_message "${RED}✗ Failed to capture: $description${NC}"
        echo "Command failed: $command" >> "$output_file"
    fi
}

# Start investigation
log_message "${BLUE}Starting crash investigation...${NC}"

# 1. Basic system information
log_message "${BLUE}=== System Information ===${NC}"

capture_command "System uptime" "uptime" "$SYSTEM_INFO_DIR/uptime.txt"
capture_command "System load" "cat /proc/loadavg" "$SYSTEM_INFO_DIR/loadavg.txt"
capture_command "Memory information" "free -h" "$SYSTEM_INFO_DIR/memory.txt"
capture_command "Disk usage" "df -h" "$SYSTEM_INFO_DIR/disk_usage.txt"
capture_command "CPU information" "cat /proc/cpuinfo | grep -E '^(model name|Hardware|Model|Revision)'" "$SYSTEM_INFO_DIR/cpu_info.txt"
capture_command "Kernel version" "uname -a" "$SYSTEM_INFO_DIR/kernel_version.txt"
capture_command "OS information" "cat /etc/os-release" "$SYSTEM_INFO_DIR/os_info.txt"

# 2. Process information
log_message "${BLUE}=== Process Information ===${NC}"

capture_command "Picframe processes" "ps aux | grep -i picframe" "$SYSTEM_INFO_DIR/picframe_processes.txt"
capture_command "Python processes" "ps aux | grep python" "$SYSTEM_INFO_DIR/python_processes.txt"
capture_command "Process tree" "pstree -p" "$SYSTEM_INFO_DIR/process_tree.txt"
capture_command "System processes by memory" "ps aux --sort=-%mem | head -20" "$SYSTEM_INFO_DIR/top_memory_processes.txt"
capture_command "System processes by CPU" "ps aux --sort=-%cpu | head -20" "$SYSTEM_INFO_DIR/top_cpu_processes.txt"

# 3. System logs
log_message "${BLUE}=== System Logs ===${NC}"

# Check if journalctl is available
if command -v journalctl >/dev/null 2>&1; then
    capture_command "Recent system logs (last hour)" "journalctl --since '1 hour ago' | grep -E '(picframe|python|killed|segfault|crash|error|exception|oom)'" "$SYSTEM_INFO_DIR/recent_system_logs.txt"
    capture_command "Picframe service logs" "journalctl -u picframe.service --since '1 day ago' 2>/dev/null || echo 'Picframe service not found'" "$SYSTEM_INFO_DIR/picframe_service_logs.txt"
else
    log_message "${YELLOW}journalctl not available, checking syslog instead${NC}"
    capture_command "Recent syslog entries" "tail -1000 /var/log/syslog | grep -E '(picframe|python|killed|segfault|crash|error|exception|oom)'" "$SYSTEM_INFO_DIR/recent_syslog.txt"
fi

# 4. Kernel logs
log_message "${BLUE}=== Kernel Logs ===${NC}"

capture_command "Recent kernel messages" "dmesg | tail -100" "$SYSTEM_INFO_DIR/recent_kernel_messages.txt"
capture_command "Kernel errors and warnings" "dmesg | grep -i -E '(error|warning|fail|killed|segfault|crash)'" "$SYSTEM_INFO_DIR/kernel_errors.txt"

# 5. Picframe specific information
log_message "${BLUE}=== Picframe Specific Information ===${NC}"

# Check for picframe configuration
if [ -d "$HOME/picframe_data" ]; then
    capture_command "Picframe data directory contents" "ls -la $HOME/picframe_data" "$SYSTEM_INFO_DIR/picframe_data_contents.txt"
    capture_command "Picframe configuration" "find $HOME/picframe_data -name '*.yaml' -exec cat {} \;" "$SYSTEM_INFO_DIR/picframe_config.txt"
fi

# Check for crash dumps
capture_command "Picframe crash dumps" "find $HOME -name 'picframe_crash_*' -o -name '*crash*' -o -name 'core*' 2>/dev/null" "$SYSTEM_INFO_DIR/crash_dumps_found.txt"

# Check for log files
capture_command "Picframe log files" "find $HOME -name '*picframe*.log' -o -name '*picframe*.txt' 2>/dev/null" "$SYSTEM_INFO_DIR/log_files_found.txt"

# 6. Network and system resources
log_message "${BLUE}=== Network and Resources ===${NC}"

capture_command "Network connections" "netstat -tuln 2>/dev/null || ss -tuln 2>/dev/null || echo 'Network tools not available'" "$SYSTEM_INFO_DIR/network_connections.txt"
capture_command "Open files by picframe processes" "lsof -p \$(pgrep -f picframe 2>/dev/null | tr '\n' ',') 2>/dev/null || echo 'lsof not available or no picframe processes found'" "$SYSTEM_INFO_DIR/open_files.txt"
capture_command "System limits" "ulimit -a" "$SYSTEM_INFO_DIR/system_limits.txt"

# 7. Hardware and temperature (Raspberry Pi specific)
log_message "${BLUE}=== Hardware Information (Raspberry Pi) ===${NC}"

if [ -f "/sys/class/thermal/thermal_zone0/temp" ]; then
    capture_command "CPU temperature" "cat /sys/class/thermal/thermal_zone0/temp | awk '{print \$1/1000 \"°C\"}'" "$SYSTEM_INFO_DIR/cpu_temperature.txt"
fi

if [ -f "/proc/device-tree/model" ]; then
    capture_command "Device tree model" "cat /proc/device-tree/model" "$SYSTEM_INFO_DIR/device_model.txt"
fi

if command -v vcgencmd >/dev/null 2>&1; then
    capture_command "GPU memory split" "vcgencmd get_mem gpu" "$SYSTEM_INFO_DIR/gpu_memory.txt"
    capture_command "CPU frequency" "vcgencmd measure_clock arm" "$SYSTEM_INFO_DIR/cpu_frequency.txt"
fi

# 8. Recent system activity
log_message "${BLUE}=== Recent System Activity ===${NC}"

capture_command "Recent logins" "last | head -20" "$SYSTEM_INFO_DIR/recent_logins.txt"
capture_command "Recent sudo activity" "grep sudo /var/log/auth.log | tail -20 2>/dev/null || echo 'Auth log not available'" "$SYSTEM_INFO_DIR/recent_sudo_activity.txt"

# 9. Create summary report
log_message "${BLUE}=== Creating Summary Report ===${NC}"

cat > "$INVESTIGATION_DIR/INVESTIGATION_SUMMARY.txt" << EOF
PICFRAME CRASH INVESTIGATION SUMMARY
====================================

Investigation Date: $(date)
Investigation Directory: $INVESTIGATION_DIR

SYSTEM OVERVIEW:
- Uptime: $(uptime)
- Load Average: $(cat /proc/loadavg)
- Memory: $(free -h | grep Mem | awk '{print $3"/"$2}')
- Disk Usage: $(df -h / | tail -1 | awk '{print $5}')
- Kernel: $(uname -r)

PICFRAME STATUS:
- Picframe Processes: $(ps aux | grep -i picframe | grep -v grep | wc -l)
- Python Processes: $(ps aux | grep python | grep -v grep | wc -l)
- Crash Dumps Found: $(find $HOME -name 'picframe_crash_*' 2>/dev/null | wc -l)

FILES COLLECTED:
$(find "$INVESTIGATION_DIR" -type f -name "*.txt" | sort | sed 's|.*/||')

NEXT STEPS:
1. Review the collected information
2. Check for error patterns in logs
3. Look for memory or resource issues
4. Examine crash dumps if available
5. Consider system resource limitations

For additional help, check:
- Picframe documentation
- Raspberry Pi forums
- System resource monitoring
EOF

log_message "${GREEN}✓ Investigation complete!${NC}"
log_message "${BLUE}Summary report: $INVESTIGATION_DIR/INVESTIGATION_SUMMARY.txt${NC}"
log_message "${BLUE}All collected data: $INVESTIGATION_DIR/${NC}"

# 10. Optional: Analyze crash dumps if found
if [ -s "$SYSTEM_INFO_DIR/crash_dumps_found.txt" ]; then
    log_message "${YELLOW}Crash dumps found! Analyzing...${NC}"
    
    while IFS= read -r dump_file; do
        if [ -f "$dump_file" ]; then
            log_message "${BLUE}Analyzing crash dump: $dump_file${NC}"
            
            # Copy crash dump to investigation directory
            cp "$dump_file" "$CRASH_DUMPS_DIR/"
            
            # Try to analyze JSON crash dumps
            if [[ "$dump_file" == *.json ]]; then
                if command -v jq >/dev/null 2>&1; then
                    jq '.' "$dump_file" > "$CRASH_DUMPS_DIR/$(basename "$dump_file").formatted"
                    log_message "${GREEN}✓ Formatted JSON crash dump${NC}"
                else
                    log_message "${YELLOW}jq not available for JSON formatting${NC}"
                fi
            fi
        fi
    done < "$SYSTEM_INFO_DIR/crash_dumps_found.txt"
fi

echo ""
echo -e "${GREEN}=== Investigation Complete ===${NC}"
echo -e "All information has been collected in: ${BLUE}$INVESTIGATION_DIR${NC}"
echo -e "Review the summary report: ${BLUE}$INVESTIGATION_DIR/INVESTIGATION_SUMMARY.txt${NC}"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo "1. Review the collected logs and system information"
echo "2. Look for patterns in the crash logs"
echo "3. Check system resource usage at crash time"
echo "4. Consider implementing the crash investigator module"
echo "5. Monitor system resources during normal operation"


