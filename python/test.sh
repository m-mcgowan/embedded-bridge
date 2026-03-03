#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# Use local venv if it exists, otherwise fall back to repo-root venv
if [ -d .venv ]; then
    PYTHON=.venv/bin/python
elif [ -d ../.venv ]; then
    PYTHON=../.venv/bin/python
else
    PYTHON=python3
fi

"$PYTHON" -m pytest tests/ "$@"
