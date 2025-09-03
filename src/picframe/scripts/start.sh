#!/bin/bash
source /home/pi/venv_pf_aspect/bin/activate                                  # Activate Python virtual environment
export DISPLAY=:0
export XAUTHORITY=/home/pi/.Xauthority
export PYTHONPATH="/home/pi/pf_aspect/src:${PYTHONPATH}"

# Check if picframe is already running
if pgrep -f "python3.*picframe" > /dev/null; then
    echo "Picframe is already running. Use 'stop' to stop it first."
    exit 1
fi

[ -f /home/pi/trace.log ] && rm /home/pi/trace.log
LOGFILE="trace.log"

# Check for --foreground or -f flag
if [[ "$1" == "--foreground" ]] || [[ "$1" == "-f" ]]; then
    echo "Running picframe in foreground (Ctrl+C to stop)..."
    echo "Stop logs will appear in trace.log - monitor with: tail -f /home/pi/trace.log"
    # Run in foreground with immediate log flushing
    stdbuf -oL -eL unbuffer /home/pi/pf_aspect/src/picframe/scripts/picframe | stdbuf -oL -eL tee $LOGFILE
else
    echo "Running picframe in background (logs to trace.log)..."
    echo "Use 'tail -f /home/pi/trace.log' in another terminal to monitor logs"
    echo "Use './stop.sh' to stop picframe"
    # Use unbuffer with line buffering for better log flushing
    stdbuf -oL -eL unbuffer /home/pi/pf_aspect/src/picframe/scripts/picframe | stdbuf -oL -eL tee $LOGFILE &
fi
