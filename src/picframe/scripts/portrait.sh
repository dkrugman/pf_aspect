s#!/bin/bash

# ============ PORTRAIT ============

# Stop Picframe
process_id=`/bin/ps -C picframe -o pid=`
sudo kill -9 $process_id

# Change Picframe configuration.yaml file
yaml_file='/home/pi/picframe_data/config/configuration.yaml'

if find "/home/pi/Pictures/Portrait/" -mindepth 1 -print -quit 2>/dev/null | grep -q .; then
    old_value='pic_dir: "~\/Pictures"'
    new_value='pic_dir: "~\/Pictures\/Portrait"'
    sed -i "s/""$old_value""/""$new_value""/g" $yaml_file
    old_value='pic_dir: "~\/Pictures\/Landscape"'
    new_value='pic_dir: "~\/Pictures\/Portrait"'
    sed -i "s/""$old_value""/""$new_value""/g" $yaml_file
fi

old_value='display_w: .*'
new_value='display_w: 2160'
sed -i "s/""$old_value""/""$new_value""/g" $yaml_file

old_value='display_h: .*'
new_value='display_h: 2894'
sed -i "s/""$old_value""/""$new_value""/g" $yaml_file

# Restart Picframe
wlr-randr --output HDMI-A-1 --mode 3840x2160@60.000000Hz --transform 270
source /home/pi/venv_picframe/bin/activate
export DISPLAY=:0
export XAUTHORITY=/home/pi/.Xauthority
picframe &  #start picframe
