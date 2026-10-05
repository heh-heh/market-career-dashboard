#!/bin/bash
set -e
cd "$(dirname "$0")"
export HOST=0.0.0.0
export PORT=8080
export UPDATE_INTERVAL=300
exec python3 server.py
