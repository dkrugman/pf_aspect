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
# Use unbuffer for proper process tracing output
unbuffer /home/pi/pf_aspect/src/picframe/scripts/picframe | tee $LOGFILE &
