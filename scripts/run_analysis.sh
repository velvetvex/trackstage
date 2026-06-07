#!/bin/bash
# Launch library analysis with GPU support (sets CUDA libs before Python starts)
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$SCRIPT_DIR/../.venv"
NVIDIA="$VENV/lib/python3.12/site-packages/nvidia"

if [ -d "$NVIDIA" ]; then
    CUDA_DIRS=$(find "$NVIDIA" -name lib -type d | tr '\n' ':')
    export LD_LIBRARY_PATH="${CUDA_DIRS}${LD_LIBRARY_PATH:-}"
fi

export TF_CPP_MIN_LOG_LEVEL=3
exec "$VENV/bin/python" "$SCRIPT_DIR/analyze_library.py" "$@"
