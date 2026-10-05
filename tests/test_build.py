"""Big-project build path: JSON project config -> deterministic bundle on disk
(PLCopen XML, per-POU ST, analysis, library manifest, MANIFEST with hashes),
CLI exit codes, overwrite guard, and the MCP tools that drive it."""
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from _util import ROOT, SAMPLES, SRC, run_module, skip

from plc_mcp.build import (BuildError, EXIT_BLOCKERS, EXIT_SAFETY,
                           build_from_config)


def _cfg(tmp, programs=None, **over):
    cfg = json.loads((SAMPLES / "project_jmr_example.json").read_text())
    if programs is not None:
        cfg["programs"] = programs
    for p in cfg["programs"]:
        p["source"] = str(SAMPLES / p["source"])
        if "symbols" in p:
            p["symbols"] = str(SAMPLES / p["symbols"])
    cfg["output_dir"] = str(Path(tmp) / "out")
    cfg.update(over)
    path = Path(tmp) / "project.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return path


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _env():
    return {**os.environ, "PYTHONPATH": str(SRC)}


def test_build_bundle_layout_and_manifest_hashes():
    with tempfile.TemporaryDirectory() as d:
        res = build_from_config(_cfg(d))
        out = Path(res.output_dir)
        assert res.exit_code == EXIT_BLOCKERS      # golden sample has BLOCKERs
        for rel in ("plcopen/JMR_DSI_Retrofit_demo.xml", "libraries.json",
                    "MANIFEST.json", "IMPORT.md",
                    "st/GVL_CENTRIFUGAL1_IO.st", "st/GVL_BLOCKS_MEM.st",
                    "st/PRG_CENTRIFUGAL1.st", "st/PRG_CENTRIFUGAL1_S01.st",
                    "analysis/CENTRIFUGAL1/flags.json",
                    "analysis/CENTRIFUGAL1/xref.json",
                    "analysis/CENTRIFUGAL1/iomap.csv",
                    "analysis/CENTRIFUGAL1/report.md",
                    "analysis/BLOCKS/flags.json"):
            assert (out / rel).is_file(), rel
        man = json.loads((out / "MANIFEST.json").read_text())
        assert man["target"] == "abb_ac500_v3"
        assert man["exit_code"] == EXIT_BLOCKERS
        assert man["programs"]["CENTRIFUGAL1"]["symbols_loaded"] >= 15
        assert man["programs"]["CENTRIFUGAL1"]["symbol_conflicts"] == 1
        assert {i["role"] for i in man["inputs"]} >= {"source", "symbols"}
        for rel, digest in man["outputs"].items():
            assert _sha(out / rel) == digest, rel
        lib = json.loads((out / "libraries.json").read_text())
        assert any(x["name"] == "Standard" for x in lib["libraries"])
        imp = (out / "IMPORT.md").read_text()
        assert "Automation Builder" in imp
        assert "I/O channel overlap" in imp      # two programs, one profile


def test_per_program_io_profile_removes_overlap_warning():
    progs = [{"id": "P1", "source": "centrifugal_excerpt.il",
              "io_profile": {"di_modules": [{"name": "DI524_A",
                                             "channels": 32}]}},
             {"id": "P2", "source": "stack_blocks.il",
              "io_profile": {"di_modules": [{"name": "DI524_B",
                                             "channels": 32}]}}]
    with tempfile.TemporaryDirectory() as d:
        out = Path(build_from_config(_cfg(d, programs=progs)).output_dir)
        assert "I/O channel overlap" not in (out / "IMPORT.md").read_text()
        assert "DI524_A" in (out / "analysis/P1/iomap.csv").read_text()
        assert "DI524_B" in (out / "analysis/P2/iomap.csv").read_text()


def test_build_is_byte_deterministic():
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        ma = build_from_config(_cfg(a)).manifest["outputs"]
        mb = build_from_config(_cfg(b)).manifest["outputs"]
        assert ma == mb


def test_overwrite_guard_and_stale_file_cleanup():
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "out"
        out.mkdir()
        (out / "precious.txt").write_text("not ours")
        try:
            build_from_config(_cfg(d))
        except BuildError as e:
            assert "not a rosetta-plc build" in str(e)
        else:
            raise AssertionError("must refuse to write into a foreign dir")
        (out / "precious.txt").unlink()
        build_from_config(_cfg(d, max_networks_per_pou=2))
        assert (out / "st/PRG_CENTRIFUGAL1_S05.st").exists()
        # rebuilding into our own dir needs no force and removes stale files
        build_from_config(_cfg(d, max_networks_per_pou=0))
        assert not (out / "st/PRG_CENTRIFUGAL1_S05.st").exists()
        assert (out / "st/PRG_CENTRIFUGAL1.st").exists()


def test_sample_config_builds_with_relative_paths():
    with tempfile.TemporaryDirectory() as d:
        res = build_from_config(SAMPLES / "project_jmr_example.json",
                                out_dir=Path(d) / "jmr")
        assert (Path(res.output_dir) / "MANIFEST.json").is_file()


def test_project_library_pins_are_recorded():
    pins = [{"name": "Standard", "version": "3.5.17.0"},
            {"name": "ABB_ModbusTcp_AC500 (namespace AC500_ModbusTcp)",
             "version": "1.1.10.3", "required": True,
             "purpose": "Modbus TCP to ACS880 (H6 redesign)"},
            {"name": "OSCAT BASIC", "version": "3.3.4", "required": False,
             "purpose": "site standard"}]
    with tempfile.TemporaryDirectory() as d:
        res = build_from_config(_cfg(d, libraries=pins))
        lib = json.loads((Path(res.output_dir) / "libraries.json")
                         .read_text())["libraries"]
        by = {x["name"]: x for x in lib}
        assert by["Standard"]["version"] == "3.5.17.0"
        assert by["Standard"]["source"] == "project config"
        mb = by["ABB_ModbusTcp_AC500 (namespace AC500_ModbusTcp)"]
        assert mb["required"] is True and mb["version"] == "1.1.10.3"
        assert by["OSCAT BASIC"]["source"] == "project config"
        imp = (Path(res.output_dir) / "IMPORT.md").read_text()
        assert "3.5.17.0" in imp and "help.plc.abb.com" in imp
        try:
            build_from_config(_cfg(d, libraries=[{"nam": "typo"}]))
        except Exception as e:
            assert "name" in str(e)
        else:
            raise AssertionError("library entry without name must fail")


def test_unknown_config_key_is_rejected():
    with tempfile.TemporaryDirectory() as d:
        try:
            build_from_config(_cfg(d, max_network_per_pou=3))   # typo
        except Exception as e:
            assert "max_network_per_pou" in str(e)
        else:
            raise AssertionError("typo in config must not be ignored")


def test_cli_targets_json():
    r = subprocess.run([sys.executable, "-m", "plc_mcp.cli", "targets",
                        "--json"], capture_output=True, text=True, env=_env(),
                       cwd=ROOT, check=True)
    ids = {t["id"] for t in json.loads(r.stdout)}
    assert {"abb_ac500_v3", "beckhoff_twincat3", "rockwell_logix5000"} <= ids


def test_cli_build_exit_code_on_safety_boundary():
    with tempfile.TemporaryDirectory() as d:
        cfg = _cfg(d, programs=[{"id": "CRANE",
                                 "source": "marine_crane_safety_excerpt.il"}])
        r = subprocess.run([sys.executable, "-m", "plc_mcp.cli", "build",
                            str(cfg)], capture_output=True, text=True,
                           env=_env(), cwd=ROOT)
        assert r.returncode == EXIT_SAFETY, (r.returncode, r.stderr)
        assert "SAFETY BOUNDARY HIT" in r.stdout
        out = Path(d) / "out"
        assert (out / "SAFETY_BOUNDARY.md").is_file()


def test_cli_validate_xml():
    try:
        import lxml  # noqa: F401
    except ImportError:
        skip("lxml not installed (pip install -e .[dev])")
    with tempfile.TemporaryDirectory() as d:
        res = build_from_config(_cfg(d))
        xml = Path(res.output_dir) / "plcopen/JMR_DSI_Retrofit_demo.xml"
        r = subprocess.run([sys.executable, "-m", "plc_mcp.cli",
                            "validate-xml", str(xml)], capture_output=True,
                           text=True, env=_env(), cwd=ROOT)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "XSD-valid" in r.stdout


def test_server_registers_all_tools():
    from plc_mcp.server import mcp
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert names >= {
        "plc_parse_melsec", "plc_device_xref", "plc_flag_semantics",
        "plc_map_io", "plc_emit_st", "plc_emit_plcopen_xml", "plc_emit_scl",
        "plc_lint_st", "plc_generate_report", "plc_list_targets",
        "plc_list_programs", "plc_emit_plcopen_project",
        "plc_export_project", "plc_build_project"}


def test_server_parse_with_symbols_then_export():
    from plc_mcp import server
    with tempfile.TemporaryDirectory() as d:
        out = json.loads(asyncio.run(server.plc_parse_melsec(server.ParseInput(
            source=str(SAMPLES / "centrifugal_excerpt.il"), program_id="T1",
            symbols_source=str(SAMPLES / "centrifugal_comments.csv")))))
        assert out["symbol_conflicts"] == 1
        progs = json.loads(asyncio.run(server.plc_list_programs()))
        assert "T1" in {p["program_id"] for p in progs["programs"]}
        res = json.loads(asyncio.run(server.plc_export_project(
            server.ExportInput(program_ids=["T1"], target="codesys_v35",
                               output_dir=str(Path(d) / "bundle"),
                               project_name="T1_demo"))))
        assert res["exit_code"] == EXIT_BLOCKERS
        assert (Path(d) / "bundle/plcopen/T1_demo.xml").is_file()
        xml = asyncio.run(server.plc_emit_plcopen_project(
            server.ProjectEmitInput(program_ids=["T1"], target="codesys_v35")))
        assert xml.startswith("<?xml") and "globalVars" in xml


if __name__ == "__main__":
    run_module(globals())
