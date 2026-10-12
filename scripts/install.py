#!/usr/bin/env python3
"""Install Rosetta in an isolated venv and write a portable Claude MCP entry."""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid


SERVER_NAME = "dummvinci-rosetta-plc"
MIN_PYTHON = (3, 10)


class InstallError(Exception):
    """A prerequisite or configuration problem with an actionable message."""


def repository_root() -> Path:
    # The caller's working directory is irrelevant, including in Claude Desktop.
    return Path(__file__).resolve().parents[1]


def venv_python(venv: Path, *, windows: bool | None = None) -> Path:
    if windows is None:
        windows = os.name == "nt"
    return venv / ("Scripts/python.exe" if windows else "bin/python")


def server_config(python: Path) -> dict:
    return {"command": str(python.absolute()), "args": ["-m", "plc_mcp.server"]}


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise InstallError("Config contains duplicate JSON keys; resolve them before installing.")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise InstallError("Config contains a nonstandard JSON number; resolve it before installing.")


def read_config(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"),
                           object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InstallError(f"Cannot read valid JSON from {path}; fix it before installing.") from exc
    if not isinstance(value, dict):
        raise InstallError("Config must be a JSON object; the existing file has been preserved.")
    return value


def merge_config(existing: dict, entry: dict, *, replace: bool = False) -> dict:
    result = deepcopy(existing)
    servers = result.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise InstallError("Config mcpServers must be a JSON object; the existing file has been preserved.")
    current = servers.get(SERVER_NAME)
    if SERVER_NAME in servers and current != entry and not replace:
        raise InstallError(
            f"Config already has a different {SERVER_NAME} entry. "
            "Review it, then rerun with --replace to back up and replace only that entry."
        )
    servers[SERVER_NAME] = entry
    return result


def write_config(path: Path, existing: dict, updated: dict) -> Path | None:
    """Write atomically; preserve a private backup when changing an existing file."""
    if path.exists() and existing == updated:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = path.with_name(f"{path.name}.bak-{stamp}-{uuid.uuid4().hex[:8]}")
        shutil.copy2(path, backup)
        # Configs may contain other servers' credentials. Keep copies private.
        if os.name != "nt":
            backup.chmod(0o600)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(updated, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return backup


def run(args: list[str], *, cwd: Path) -> None:
    try:
        subprocess.run(args, cwd=cwd, check=True)
    except FileNotFoundError as exc:
        raise InstallError(f"Required executable not found: {args[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise InstallError(
            f"Command failed (exit {exc.returncode}); see its output above. "
            "The MCP config has not been changed."
        ) from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="config to merge; default: repository .mcp.json")
    parser.add_argument("--replace", action="store_true", help="replace an existing Rosetta entry after backup")
    parser.add_argument("--dry-run", action="store_true", help="show the plan without installation or file changes")
    options = parser.parse_args(argv)
    try:
        if sys.version_info < MIN_PYTHON:
            raise InstallError("Python 3.10 or newer is required; use python3 on macOS/Linux or py -3 on Windows.")
        repo = repository_root()
        if not (repo / "pyproject.toml").is_file():
            raise InstallError("Keep scripts/install.py inside the Rosetta repository; pyproject.toml is missing.")
        venv = repo / ".venv"
        python = venv_python(venv)
        config_path = (options.config or repo / ".mcp.json").expanduser().resolve()
        # Catch config conflicts before any installation/network activity.
        existing = read_config(config_path)
        updated = merge_config(existing, server_config(python), replace=options.replace)
        if options.dry_run:
            print(f"Would create/use virtual environment: {venv}")
            print(f"Would install and validate repository: {repo}")
            print(f"Would merge {SERVER_NAME} into: {config_path}")
            print(f"MCP command: {python}")
            return 0
        if venv.exists() and not python.is_file():
            raise InstallError(
                f"{venv} exists but has no interpreter for this platform. "
                "Move it aside after checking its contents, then rerun."
            )
        if not python.is_file():
            print(f"Creating virtual environment: {venv}", flush=True)
            try:
                run([sys.executable, "-m", "venv", str(venv)], cwd=repo)
            except InstallError as exc:
                raise InstallError(
                    "Could not create the virtual environment. On Debian/Ubuntu, install the "
                    "python3-venv package for your Python version; then rerun. "
                    "No MCP config was changed."
                ) from exc
        run([str(python), "-m", "pip", "--disable-pip-version-check", "install", "--no-input", str(repo)], cwd=repo)
        # Prove imports work outside the checkout, without PYTHONPATH or activation.
        with tempfile.TemporaryDirectory(prefix="rosetta-install-check-") as unrelated:
            run([str(python), "-c", "from plc_mcp.server import mcp; print('Server import OK:', mcp.name)"],
                cwd=Path(unrelated))
        # Do not overwrite edits made while dependencies were being installed.
        current = read_config(config_path)
        if current != existing:
            raise InstallError("Config changed during installation; rerun to merge the latest version safely.")
        backup = write_config(config_path, existing, updated)
        if backup:
            print(f"Existing config backed up to: {backup}")
        print(f"Installed {SERVER_NAME}; MCP config: {config_path}")
        print("Claude Code: open this project, approve its MCP server, then restart Claude or reconnect via /mcp.")
        print("Claude Desktop: merge the generated mcpServers entry into Desktop settings, then restart the app.")
        return 0
    except (InstallError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
