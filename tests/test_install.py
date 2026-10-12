"""Installer regressions: safe config merges and launch paths without network."""

import importlib.util
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("rosetta_install", REPO / "scripts" / "install.py")
install = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(install)


class InstallerTests(unittest.TestCase):
    def test_absolute_command_with_spaces_and_no_relative_environment(self):
        python = Path(tempfile.gettempdir()) / "project with spaces" / ".venv" / "bin" / "python"
        entry = install.server_config(python)
        self.assertTrue(Path(entry["command"]).is_absolute())
        self.assertIn("project with spaces", entry["command"])
        self.assertEqual(entry["args"], ["-m", "plc_mcp.server"])
        self.assertNotIn("env", entry)
        self.assertNotIn('"', entry["command"])

    def test_windows_and_posix_venv_paths(self):
        venv = Path("checkout") / ".venv"
        self.assertEqual(install.venv_python(venv, windows=True), venv / "Scripts" / "python.exe")
        self.assertEqual(install.venv_python(venv, windows=False), venv / "bin" / "python")

    def test_merge_preserves_other_servers_settings_and_original(self):
        original = {"mcpServers": {"other": {"command": "other", "env": {"TOKEN": "fixture"}}},
                    "settings": {"theme": "dark"}}
        updated = install.merge_config(original, {"command": "python", "args": ["-m", "plc_mcp.server"]})
        self.assertEqual(updated["mcpServers"]["other"], original["mcpServers"]["other"])
        self.assertEqual(updated["settings"], original["settings"])
        self.assertNotIn(install.SERVER_NAME, original["mcpServers"])

    def test_conflicting_entry_is_not_overwritten(self):
        original = {"mcpServers": {install.SERVER_NAME: {"command": "custom"}}}
        with self.assertRaisesRegex(install.InstallError, "--replace"):
            install.merge_config(original, {"command": "new"})
        self.assertEqual(original["mcpServers"][install.SERVER_NAME]["command"], "custom")

    def test_replace_preserves_backup_and_other_server(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "claude.json"
            raw = '{"mcpServers":{"other":{"command":"other"},"dummvinci-rosetta-plc":{"command":"old"}}}\n'
            config.write_text(raw, encoding="utf-8")
            original = install.read_config(config)
            updated = install.merge_config(original, {"command": "new"}, replace=True)
            backup = install.write_config(config, original, updated)
            self.assertEqual(backup.read_text(encoding="utf-8"), raw)
            self.assertEqual(install.read_config(config)["mcpServers"]["other"], {"command": "other"})
            self.assertEqual(install.read_config(config)["mcpServers"][install.SERVER_NAME], {"command": "new"})

    def test_unchanged_config_is_not_rewritten_or_backed_up(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / ".mcp.json"
            original = {"mcpServers": {install.SERVER_NAME: {"command": "python"}}}
            config.write_text(json.dumps(original), encoding="utf-8")
            before = config.stat().st_mtime_ns
            self.assertIsNone(install.write_config(config, original, original))
            self.assertEqual(config.stat().st_mtime_ns, before)
            self.assertEqual(list(Path(directory).iterdir()), [config])

    def test_invalid_json_and_duplicate_keys_fail_without_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "claude.json"
            for raw in ('{invalid}', '{"mcpServers":{},"mcpServers":{}}', '[{}]',
                        '{"mcpServers":{},"number":NaN}', '{"mcpServers":{},"number":Infinity}'):
                with self.subTest(raw=raw):
                    config.write_text(raw, encoding="utf-8")
                    with self.assertRaises(install.InstallError):
                        install.read_config(config)
                    self.assertEqual(config.read_text(encoding="utf-8"), raw)

    def test_failed_install_preserves_existing_config(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "pyproject.toml").touch()
            python = install.venv_python(repo / ".venv")
            python.parent.mkdir(parents=True)
            python.touch()
            config = repo / ".mcp.json"
            raw = '{"mcpServers":{"other":{"command":"keep"}}}'
            config.write_text(raw, encoding="utf-8")
            error = io.StringIO()
            with patch.object(install, "repository_root", return_value=repo), \
                 patch.object(install, "run", side_effect=install.InstallError("fixture install failure")), \
                 redirect_stderr(error), redirect_stdout(io.StringIO()):
                self.assertEqual(install.main([]), 1)
            self.assertIn("fixture install failure", error.getvalue())
            self.assertEqual(config.read_text(encoding="utf-8"), raw)
            self.assertFalse(list(repo.glob(".mcp.json.bak-*")))

    def test_config_edit_during_install_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "pyproject.toml").touch()
            python = install.venv_python(repo / ".venv")
            python.parent.mkdir(parents=True)
            python.touch()
            config = repo / ".mcp.json"
            config.write_text('{"mcpServers":{}}', encoding="utf-8")
            changed = '{"mcpServers":{"concurrent":{"command":"keep"}}}'
            error = io.StringIO()
            with patch.object(install, "repository_root", return_value=repo), \
                 patch.object(install, "run", side_effect=lambda *args, **kwargs:
                              config.write_text(changed, encoding="utf-8")), \
                 redirect_stderr(error), redirect_stdout(io.StringIO()):
                self.assertEqual(install.main([]), 1)
            self.assertIn("changed during installation", error.getvalue())
            self.assertEqual(config.read_text(encoding="utf-8"), changed)

    def test_invalid_mcp_servers_fails(self):
        for servers in ([], None, "invalid"):
            with self.subTest(servers=servers):
                with self.assertRaises(install.InstallError):
                    install.merge_config({"mcpServers": servers}, {"command": "python"})

    def test_dry_run_from_unrelated_directory_makes_no_output_file(self):
        with tempfile.TemporaryDirectory(prefix="rosetta test with spaces ") as directory:
            config = Path(directory) / "config with spaces.json"
            result = subprocess.run([sys.executable, str(REPO / "scripts" / "install.py"),
                                     "--dry-run", "--config", str(config)], cwd=directory,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(str(REPO / ".venv"), result.stdout)
            self.assertFalse(config.exists())

    def test_dry_run_conflict_fails_before_install_and_preserves_file(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            raw = json.dumps({"mcpServers": {install.SERVER_NAME: {"command": "custom"}}})
            config.write_text(raw, encoding="utf-8")
            result = subprocess.run([sys.executable, str(REPO / "scripts" / "install.py"),
                                     "--dry-run", "--config", str(config)], cwd=directory,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn("--replace", result.stderr)
            self.assertEqual(config.read_text(encoding="utf-8"), raw)


if __name__ == "__main__":
    unittest.main(verbosity=2)
