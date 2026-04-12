#!/bin/bash
# Run weekly digest and reflection
set -e

source "$(dirname "$0")/common.sh"
init
init_mcp

echo "=== Weekly digest for $TODAY ==="

cd /home/myuser/projects/my_asb_cowork
/home/myuser/.local/bin/uv run python scripts/weekly.py
