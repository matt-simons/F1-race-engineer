#!/usr/bin/env bash
# Start the race engineer.
#   ./engineer.sh                    run in a terminal (Desktop Mode)
#   ./engineer.sh [options]          any 'live' options, e.g. --mode race
#
# Gaming Mode: put this in F1 23's Steam launch options (use the full path):
#   /home/YOU/F1-race-engineer/engineer.sh --with-game %command%
# The engineer then starts with the game and stops when it closes.
# Its output goes to recordings/engineer.log.
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")"
PY=.venv/bin/python
[ -x "$PY" ] || PY=python3

if [ "${1:-}" = "--with-game" ]; then
    shift
    mkdir -p recordings
    # Steam's overlay library is preloaded for the game; keep it out of Python.
    env -u LD_PRELOAD "$PY" -m f1engineer live --quiet >> recordings/engineer.log 2>&1 &
    ENGINEER=$!
    "$@"
    STATUS=$?
    kill -TERM "$ENGINEER" 2>/dev/null
    wait "$ENGINEER" 2>/dev/null
    exit $STATUS
fi

exec "$PY" -m f1engineer live "$@"
