#!/bin/bash

# Stop Picframe
process_id=`pgrep -f ^python.*picframe$`
sudo kill -9 $process_id
