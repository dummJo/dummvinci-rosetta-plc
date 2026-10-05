"""Shared test helpers: import path, sample access, and a tiny runner so every
test file works both as `python tests/test_x.py` and under pytest."""
from __future__ import annotations

import os
import pathlib
import sys
import traceback

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SAMPLES = ROOT / "samples"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class Skip(Exception):
    """Raised when an optional dependency is missing. Reported as SKIP —
    visible in the output, never silently counted as a pass."""


def skip(reason: str) -> None:
    if os.environ.get("ROSETTA_REQUIRE_OPTIONAL") == "1":
        raise AssertionError(f"required optional dependency missing: "
                             f"{reason}")
    if "pytest" in sys.modules:
        import pytest
        pytest.skip(reason)
    raise Skip(reason)


def sample(name: str) -> str:
    return (SAMPLES / name).read_text(encoding="utf-8")


def run_module(globs: dict) -> None:
    failed = 0
    for name, fn in sorted(globs.items()):
        if not (name.startswith("test_") and callable(fn)):
            continue
        try:
            fn()
            print("PASS", name)
        except Skip as e:
            print(f"SKIP {name} ({e})")
        except Exception:
            failed += 1
            print("FAIL", name)
            traceback.print_exc()
    if failed:
        sys.exit(1)
