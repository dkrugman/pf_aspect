#!/bin/bash

# Stop Picframe
process_id=`/bin/ps -C picframe -o pid=`
sudo kill -9 $process_id
