"""Exercise the installed MCP server over stdio, as Claude starts it.

The child process runs outside the checkout and cannot rely on PYTHONPATH.
These tests use the SDK's real client rather than calling tool functions.
"""

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import sys
import sysconfig
import tempfile
import xml.etree.ElementTree as ET

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


SAMPLES = Path(__file__).resolve().parents[1] / "samples"
EXPECTED_TOOLS = {
    "plc_parse_melsec",
    "plc_device_xref",
    "plc_flag_semantics",
    "plc_map_io",
    "plc_emit_st",
    "plc_emit_plcopen_xml",
    "plc_emit_scl",
    "plc_lint_st",
    "plc_generate_report",
}


def _wire(model):
    # SDK 1 uses camelCase attributes; SDK 2 uses snake_case attributes.
    # Their JSON-RPC field names remain the same.
    return model.model_dump(by_alias=True)


def _resolve_schema(root, schema):
    while "$ref" in schema:
        reference = schema["$ref"]
        assert reference.startswith("#/"), reference
        schema = root
        for part in reference[2:].split("/"):
            schema = schema[part]
    return schema


@asynccontextmanager
async def _session(cwd, command=None, args=None):
    transport_errors = []

    async def observe_message(message):
        if isinstance(message, Exception):
            transport_errors.append(message)

    parameters = StdioServerParameters(
        command=command or sys.executable,
        args=["-m", "plc_mcp.server"] if args is None else args,
        cwd=str(cwd),
        env={"PYTHONPATH": "", "PYTHONUNBUFFERED": "1"},
    )
    # stderr can contain SDK diagnostics, but stdout must stay valid JSON-RPC.
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stderr:
        try:
            with anyio.fail_after(30):
                async with stdio_client(parameters, errlog=stderr) as streams:
                    async with ClientSession(
                        *streams, message_handler=observe_message
                    ) as session:
                        initialized = await session.initialize()
                        assert _wire(initialized)["serverInfo"]["name"] == "dummvinci-rosetta-plc"
                        yield session
        except BaseException:
            stderr.seek(0)
            print("Server stderr:\n" + stderr.read(), file=sys.stderr)
            raise
        stderr.seek(0)
        assert not transport_errors, (transport_errors, stderr.read())


async def _text(session, name, params):
    result = await session.call_tool(name, {"params": params})
    assert not _wire(result)["isError"], result.content
    assert result.content and all(item.type == "text" for item in result.content)
    return "\n".join(item.text for item in result.content)


async def _json(session, name, params):
    return json.loads(await _text(session, name, params))


def test_stdio_initialize_lists_nine_tools_and_nested_params(tmp_path):
    async def exercise():
        async with _session(tmp_path) as session:
            tools = _wire(await session.list_tools())["tools"]
            assert {tool["name"] for tool in tools} == EXPECTED_TOOLS
            for tool in tools:
                assert set(tool["inputSchema"]["properties"]) == {"params"}
                assert tool["inputSchema"]["required"] == ["params"]
            parse = next(tool for tool in tools if tool["name"] == "plc_parse_melsec")
            parse_schema = parse["inputSchema"]
            params_schema = _resolve_schema(parse_schema, parse_schema["properties"]["params"])
            assert "source" in params_schema["required"]
            await session.send_ping()

    asyncio.run(exercise())


def test_stdio_installed_console_entrypoint_from_unrelated_cwd(tmp_path):
    suffix = ".exe" if os.name == "nt" else ""
    command = Path(sysconfig.get_path("scripts")) / ("dummvinci-rosetta-plc" + suffix)
    assert command.is_file(), "Install the package before running verification"

    async def exercise():
        async with _session(tmp_path, command=str(command), args=[]) as session:
            assert {tool.name for tool in (await session.list_tools()).tools} == EXPECTED_TOOLS

    asyncio.run(exercise())


def test_stdio_process_sample_full_tool_chain(tmp_path):
    async def exercise():
        async with _session(tmp_path) as session:
            parsed = await _json(session, "plc_parse_melsec", {
                "source": (SAMPLES / "process_excerpt.il").read_text(encoding="utf-8"),
                "program_id": "PROCESS",
            })
            assert parsed["program_id"] == "PROCESS"
            assert parsed["summary"]["instruction_count"] > 30
            reference = {"program_id": "PROCESS"}
            flags = await _json(session, "plc_flag_semantics", reference)
            codes = {flag["code"] for flag in flags["flags"]}
            assert {"H1-ALWAYS-OFF-SERIES", "H4-MC-ZONE", "H6-MODULE-BUFFER"} <= codes
            assert flags["summary"]["safety_boundary"] is False
            xref = await _json(session, "plc_device_xref", reference)
            assert xref["devices"]["L1000"]["write_count"] == 1
            mapping = await _json(session, "plc_map_io", reference)
            assert any(item["legacy"] == "Y0C1" for item in mapping["mapping"])
            st = await _text(session, "plc_emit_st", {
                **reference, "pou_name": "PRG_MCP_Test",
            })
            assert "PROGRAM PRG_MCP_Test" in st
            assert "TODO [H1-ALWAYS-OFF-SERIES" in st
            lint = await _json(session, "plc_lint_st", {"st_code": st})
            assert not [issue for issue in lint["issues"] if "mismatch" in issue]
            xml = await _text(session, "plc_emit_plcopen_xml", {
                "st_code": st, "pou_name": "PRG_MCP_Test", "project_name": "MCP test",
            })
            assert ET.fromstring(xml).tag.endswith("project")
            scl = await _text(session, "plc_emit_scl", {
                "st_code": st, "fb_name": "FB_MCP_Test",
            })
            assert "FUNCTION_BLOCK" in scl and "FB_MCP_Test" in scl
            report = await _text(session, "plc_generate_report", {
                **reference, "project": "MCP integration test",
            })
            assert "MCP integration test" in report and "Sign-off" in report
            assert "H1-ALWAYS-OFF-SERIES" in report

    asyncio.run(exercise())


def test_stdio_safety_boundary_preserved(tmp_path):
    async def exercise():
        async with _session(tmp_path) as session:
            # Absolute paths must work when Claude's cwd is unrelated to the repo.
            await _json(session, "plc_parse_melsec", {
                "source": str(SAMPLES / "marine_crane_safety_excerpt.il"),
                "source_is_path": True,
                "program_id": "SAFETY",
            })
            reference = {"program_id": "SAFETY"}
            flags = await _json(session, "plc_flag_semantics", reference)
            safety_flags = [item for item in flags["flags"] if item["code"] == "H11-SAFETY-BOUNDARY"]
            assert safety_flags and all(item["severity"] == "BLOCKER" for item in safety_flags)
            assert flags["summary"]["safety_boundary"] is True
            assert "SAFETY BOUNDARY HIT" in flags["summary"]["verdict"]
            report = await _text(session, "plc_generate_report", reference)
            assert "H11-SAFETY-BOUNDARY" in report and "AC500-S" in report
            refused = await session.call_tool("plc_emit_st", {"params": reference})
            assert _wire(refused)["isError"], refused
            message = "\n".join(item.text for item in refused.content if item.type == "text")
            assert "SAFETY BOUNDARY" in message and "AC500-S" in message
            # Old warning-only outputs must not bypass the boundary via wrappers.
            for name in ("plc_emit_plcopen_xml", "plc_emit_scl"):
                result = await session.call_tool(name, {
                    "params": {"st_code": "(* SAFETY BOUNDARY HIT [H11-SAFETY-BOUNDARY] *)"},
                })
                assert _wire(result)["isError"], (name, result)
                message = "\n".join(item.text for item in result.content if item.type == "text")
                assert "SAFETY BOUNDARY" in message and "AC500-S" in message
            await session.send_ping()

    asyncio.run(exercise())


def test_stdio_errors_are_tool_errors_and_server_survives(tmp_path):
    async def exercise():
        async with _session(tmp_path) as session:
            invalid_calls = [
                ("plc_parse_melsec", {"source": "0 LD X0\n1 OUT Y0"}),
                ("plc_parse_melsec", {"params": {"source": None}}),
                ("plc_parse_melsec", {"params": {"source": "0 LD X0", "extra": True}}),
                ("plc_parse_melsec", {"params": {"source": "0 LD X0", "dialect": "unsupported"}}),
                ("plc_parse_melsec", {"params": {"source": str(tmp_path / "missing.il"), "source_is_path": True}}),
                ("plc_device_xref", {"params": {"program_id": "MISSING"}}),
                ("tool_does_not_exist", {"params": {}}),
            ]
            for name, arguments in invalid_calls:
                result = await session.call_tool(name, arguments)
                assert _wire(result)["isError"], (name, result)
                assert result.content
                assert {tool.name for tool in (await session.list_tools()).tools} == EXPECTED_TOOLS
            parsed = await _json(session, "plc_parse_melsec", {
                "source": "0 LD X0\n1 OUT Y0\n2 END", "program_id": "AFTER_ERRORS",
            })
            assert parsed["program_id"] == "AFTER_ERRORS"
            result = await session.call_tool("plc_map_io", {
                "params": {"program_id": "AFTER_ERRORS", "profile_json": "{bad json"},
            })
            assert _wire(result)["isError"]
            await session.send_ping()

    asyncio.run(exercise())
