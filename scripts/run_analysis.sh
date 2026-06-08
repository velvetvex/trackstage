#!/bin/bash
# Launch library analysis with GPU support (sets CUDA libs before Python starts)
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="${TRACKSTAGE_VENV:-$SCRIPT_DIR/../.venv}"
PYTHON_VER=$(basename "$VENV"/lib/python3.*)
NVIDIA="$VENV/lib/$PYTHON_VER/site-packages/nvidia"

if [ -d "$NVIDIA" ]; then
    CUDA_DIRS=$(find "$NVIDIA" -name lib -type d | tr '\n' ':')
    export LD_LIBRARY_PATH="${CUDA_DIRS}${LD_LIBRARY_PATH:-}"
fi

export TF_CPP_MIN_LOG_LEVEL=3
exec "$VENV/bin/python" "$SCRIPT_DIR/analyze_library.py" "$@"
