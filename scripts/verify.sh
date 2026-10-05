#!/usr/bin/env bash
# Run before AND after any change (CLAUDE.md). Every test file runs with its
# own runner; optional-dependency tests print SKIP (set
# ROSETTA_REQUIRE_OPTIONAL=1, as CI does, to make a SKIP fail instead).
set -e
cd "$(dirname "$0")/.."
export PYTHONPATH=src
for t in tests/test_*.py; do
  echo "== $t"
  python3 "$t"
done
python3 -c "from plc_mcp.server import mcp; print('server import OK:', mcp.name)"
python3 -m plc_mcp.cli targets > /dev/null && echo "cli OK"
python3 -m py_compile src/plc_mcp/*.py src/plc_mcp/*/*.py
echo "VERIFY OK"
