#!/bin/bash
set -euo pipefail
# Activate venv (keeps site-packages etc.)
source /home/pi/venv_pf_aspect/bin/activate

# Clean X11 env that works from SSH
export DISPLAY=:0
export XAUTHORITY=/home/pi/.Xauthority
export XDG_RUNTIME_DIR=/run/user/1000
unset WAYLAND_DISPLAY WAYLAND_SOCKET

# Exec with explicit venv python to avoid PATH surprises
exec /home/pi/venv_pf_aspect/bin/python /home/pi/pf_aspect/src/picframe/testpi3d.py
