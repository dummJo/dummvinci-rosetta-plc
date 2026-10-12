#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

# PYTHON can point to the installed virtualenv interpreter, including Windows
# paths. Do not force PYTHONPATH: imports must work from the installed package.
if [[ -n "${PYTHON:-}" ]]; then
    python_command="$PYTHON"
elif command -v python3 >/dev/null 2>&1; then
    python_command=python3
else
    python_command=python
fi

"$python_command" - <<'PY'
import sys
import pytest

class RequireTests:
    def pytest_collection_finish(self, session):
        if not session.items:
            raise pytest.UsageError("Verification requires at least one collected test")

sys.exit(pytest.main(["-q", "tests"], plugins=[RequireTests()]))
PY
"$python_command" -c 'from plc_mcp.server import mcp; print("server import OK:", mcp.name)'
"$python_command" -m compileall -q src/plc_mcp
echo "VERIFY OK"
