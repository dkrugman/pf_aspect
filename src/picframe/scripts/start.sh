#!/bin/bash
source /home/pi/venv_picframe/bin/activate  # Activate Python virtual environment
export DISPLAY=:0
export XAUTHORITY=/home/pi/.Xauthority
[ -f /home/pi/trace.log ] && rm /home/pi/trace.log
LOGFILE="trace.log"
unbuffer ~/pf_aspect/src/picframe/scripts/picframe | tee $LOGFILE &
