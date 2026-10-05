#!/bin/sh
# The node runs the entry point as PID 1 with "/" as the working directory.
cd /regresioncnf || exit 1
exec python3 start.py
