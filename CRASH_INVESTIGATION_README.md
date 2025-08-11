# Picframe Crash Investigation Guide

This guide helps you investigate and prevent crashes in picframe, especially on Raspberry Pi systems.

## Quick Start

### 1. Run the Crash Investigation Script
```bash
cd ~/src/picframe
./scripts/investigate_crash.sh
```

This will create a comprehensive investigation report in a timestamped directory.

### 2. Install Enhanced Crash Detection
```bash
pip install psutil
```

This enables the crash investigator module for automatic crash detection and logging.

## Understanding Your Recent Crash

Based on the logs I found, your picframe crashed at **11:08:17** with exit code 1. Here's what we know:

- **Crash Time**: 11:08:17
- **Exit Code**: 1 (FAILURE)
- **Auto-restart**: Yes (systemd automatically restarted the service)
- **Current Status**: Running and stable for ~8 minutes

## Common Crash Causes on Raspberry Pi

### 1. Memory Issues
- **Out of Memory (OOM)**: Pi runs out of RAM
- **Memory fragmentation**: Long-running processes fragment memory
- **GPU memory conflicts**: Graphics operations consume too much memory

### 2. Resource Exhaustion
- **File descriptors**: Too many open files
- **Process limits**: System process limits exceeded
- **Disk space**: Insufficient storage for logs/cache

### 3. Hardware Issues
- **Overheating**: CPU/GPU temperature too high
- **Power supply**: Insufficient power causing instability
- **SD card corruption**: File system issues

### 4. Software Issues
- **Python exceptions**: Unhandled errors in picframe code
- **Library conflicts**: Version incompatibilities
- **Signal handling**: Improper shutdown handling

## Investigation Steps

### Step 1: Immediate Investigation
```bash
# Check current system status
free -h
df -h
uptime
ps aux | grep picframe

# Check recent logs
journalctl --since "1 hour ago" | grep -i picframe
dmesg | tail -20
```

### Step 2: Run Full Investigation
```bash
./scripts/investigate_crash.sh
```

### Step 3: Analyze Results
Review the generated investigation directory:
- `INVESTIGATION_SUMMARY.txt` - Overview and next steps
- `system_info/` - System logs and status
- `crash_dumps/` - Any crash files found

## Prevention Strategies

### 1. Enable Enhanced Crash Detection
The crash investigator module automatically:
- Captures stack traces on crashes
- Monitors memory and CPU usage
- Generates detailed crash reports
- Handles graceful shutdowns

### 2. System Monitoring
```bash
# Monitor memory usage
watch -n 5 'free -h'

# Monitor CPU temperature
watch -n 10 'cat /sys/class/thermal/thermal_zone0/temp | awk "{print \$1/1000 \"°C\"}"'

# Monitor system load
watch -n 5 'uptime'
```

### 3. Resource Limits
```bash
# Check current limits
ulimit -a

# Increase file descriptor limit (add to ~/.bashrc)
ulimit -n 4096
```

### 4. Regular Maintenance
```bash
# Clean up old logs
sudo journalctl --vacuum-time=7d

# Check disk space
df -h

# Monitor system resources
htop
```

## Troubleshooting Specific Issues

### Memory Issues
```bash
# Check memory usage
free -h
cat /proc/meminfo

# Check for memory leaks
ps aux --sort=-%mem | head -10

# Monitor swap usage
cat /proc/swaps
```

### Process Issues
```bash
# Check picframe processes
ps aux | grep picframe

# Check process tree
pstree -p

# Check open files
lsof -p $(pgrep -f picframe)
```

### System Issues
```bash
# Check system load
cat /proc/loadavg

# Check CPU frequency
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq

# Check temperature
cat /sys/class/thermal/thermal_zone0/temp
```

## Advanced Debugging

### 1. Enable Python Debugging
```bash
# Run with Python debugger
python3 -m pdb src/picframe/scripts/picframe

# Enable verbose logging
export PYTHONVERBOSE=1
```

### 2. System Call Tracing
```bash
# Trace system calls (requires strace)
sudo strace -f -p $(pgrep -f picframe)

# Monitor file operations
sudo strace -e trace=file -f -p $(pgrep -f picframe)
```

### 3. Memory Profiling
```python
# Add to your picframe code
import tracemalloc
tracemalloc.start()

# Later, check memory usage
current, peak = tracemalloc.get_traced_memory()
print(f"Current memory usage: {current / 1024 / 1024:.1f} MB")
print(f"Peak memory usage: {peak / 1024 / 1024:.1f} MB")
```

## Recovery Actions

### 1. Immediate Recovery
```bash
# Restart picframe
sudo systemctl restart picframe.service

# Or kill and restart manually
pkill -f picframe
cd ~/src/picframe
python3 src/picframe/scripts/picframe &
```

### 2. System Recovery
```bash
# Reboot if necessary
sudo reboot

# Check file system
sudo fsck -f /

# Clear system cache
sudo sync && sudo echo 3 > /proc/sys/vm/drop_caches
```

### 3. Data Recovery
```bash
# Backup configuration
cp -r ~/picframe_data ~/picframe_data_backup_$(date +%Y%m%d)

# Check for corrupted files
find ~/picframe_data -type f -exec file {} \;
```

## Monitoring and Alerting

### 1. Set Up Monitoring
```bash
# Create monitoring script
cat > ~/monitor_picframe.sh << 'EOF'
#!/bin/bash
if ! pgrep -f picframe > /dev/null; then
    echo "Picframe crashed at $(date)" | mail -s "Picframe Alert" your@email.com
    # Auto-restart
    cd ~/src/picframe && python3 src/picframe/scripts/picframe &
fi
EOF

chmod +x ~/monitor_picframe.sh

# Add to crontab (check every 5 minutes)
crontab -e
# Add: */5 * * * * ~/monitor_picframe.sh
```

### 2. Log Rotation
```bash
# Configure logrotate for picframe logs
sudo tee /etc/logrotate.d/picframe << 'EOF'
/home/pi/scripts/picframe.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    create 644 pi pi
}
EOF
```

## Getting Help

### 1. Collect Information
Before asking for help, gather:
- Investigation report from `investigate_crash.sh`
- System logs and crash dumps
- Configuration files
- Error messages and stack traces

### 2. Community Resources
- Picframe GitHub issues
- Raspberry Pi forums
- Python debugging resources
- System administration guides

### 3. Professional Support
Consider professional support if:
- Crashes are frequent and unexplained
- System becomes unstable
- Data loss occurs
- Performance degrades significantly

## Prevention Checklist

- [ ] Install crash investigator module
- [ ] Set up system monitoring
- [ ] Configure resource limits
- [ ] Implement log rotation
- [ ] Set up automatic recovery
- [ ] Regular system maintenance
- [ ] Monitor temperature and power
- [ ] Keep system updated
- [ ] Backup configurations
- [ ] Test recovery procedures

## Conclusion

Crashes on Raspberry Pi systems are often related to resource constraints or hardware limitations. The tools and techniques in this guide will help you:

1. **Investigate** crashes effectively
2. **Prevent** future crashes
3. **Recover** quickly when issues occur
4. **Monitor** system health continuously

Start with the crash investigation script and gradually implement the prevention strategies based on your specific needs and system constraints.


