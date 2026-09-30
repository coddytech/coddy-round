#!/bin/sh
# Builds every weight of Coddy Round in one step (see sources/build.py).
set -e
cd "$(dirname "$0")"
python3 sources/build.py "$@"
