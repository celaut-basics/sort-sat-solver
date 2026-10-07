#!/bin/sh
# The node runs the entry point as PID 1 with "/" as the working directory.
cd /satsorter || exit 1
exec python3 -m src.main
