#!/bin/bash
# Double-click on a Mac to start the desk.
cd "$(dirname "$0")" && exec python3 -m desk "$@"
