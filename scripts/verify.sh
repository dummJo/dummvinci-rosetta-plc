#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."
PYTHONPATH=src python3 tests/test_pipeline.py
PYTHONPATH=src python3 -c "from plc_mcp.server import mcp; print(\"server import OK:\", mcp.name)"
python3 -m py_compile src/plc_mcp/*.py src/plc_mcp/*/*.py
echo "VERIFY OK"
