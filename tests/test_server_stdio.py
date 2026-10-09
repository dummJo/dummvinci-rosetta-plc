"""The MCP server as a client sees it: `python -m plc_mcp.server` over stdio,
raw JSON-RPC (no SDK client), so the check holds for mcp 1.x and 2.x alike.
Covers main()'s transport, the advertised tools and their annotations, and
program state carried across calls in one session."""
import json
import os
import subprocess
import sys
import tempfile
import threading

from _util import ROOT, SAMPLES, SRC, run_module

# negotiable via the initialize handshake by every supported mcp (>=1.14, 2.x)
PROTOCOL = "2025-06-18"
WRITE_TOOLS = {"plc_export_project", "plc_build_project"}
READ_ONLY_TOOLS = {
    "plc_parse_melsec", "plc_device_xref", "plc_flag_semantics", "plc_map_io",
    "plc_emit_st", "plc_emit_plcopen_xml", "plc_emit_scl", "plc_lint_st",
    "plc_generate_report", "plc_list_targets", "plc_list_programs",
    "plc_emit_plcopen_project"}


class _Session:
    def __init__(self, proc):
        self.proc = proc
        self.next_id = 0

    def send(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            self.next_id += 1
            msg["id"] = self.next_id
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        return None if notify else self._recv(self.next_id)

    def _recv(self, want_id):
        while True:  # skip server notifications until our response arrives
            line = self.proc.stdout.readline()
            if not line:
                raise AssertionError("server closed stdout (crashed?)")
            msg = json.loads(line)
            if msg.get("id") == want_id:
                assert "error" not in msg, msg["error"]
                return msg["result"]

    def call(self, tool, arguments):
        res = self.send("tools/call", {"name": tool, "arguments": arguments})
        assert not res.get("isError"), res
        return res["content"][0]["text"]


def _run_session(body):
    with tempfile.TemporaryFile("w+") as err:
        proc = subprocess.Popen(
            [sys.executable, "-m", "plc_mcp.server"], cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(SRC)}, text=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err)
        watchdog = threading.Timer(60, proc.kill)
        watchdog.start()
        try:
            body(_Session(proc))
            proc.stdin.close()  # EOF on stdin = client gone: clean exit
            assert proc.wait() == 0, f"exit {proc.returncode}"
        except BaseException:
            proc.kill()
            proc.wait()
            err.seek(0)
            print("server stderr:\n" + err.read(), file=sys.stderr)
            raise
        finally:
            watchdog.cancel()


def test_stdio_handshake_tools_and_annotations():
    def body(s):
        init = s.send("initialize", {
            "protocolVersion": PROTOCOL, "capabilities": {},
            "clientInfo": {"name": "rosetta-test", "version": "0"}})
        assert init["protocolVersion"] == PROTOCOL
        assert init["serverInfo"]["name"] == "dummvinci-rosetta-plc"
        assert "tools" in init["capabilities"]
        s.send("notifications/initialized", notify=True)

        tools = {t["name"]: t for t in s.send("tools/list")["tools"]}
        assert set(tools) == READ_ONLY_TOOLS | WRITE_TOOLS, sorted(tools)
        for name, t in tools.items():
            a = t["annotations"]
            writes = name in WRITE_TOOLS
            assert a["title"], name
            assert a["readOnlyHint"] is (not writes), (name, a)
            assert a["destructiveHint"] is writes, (name, a)
            assert a["idempotentHint"] is True, (name, a)
            assert a["openWorldHint"] is False, (name, a)
            assert t["description"].strip(), name
        parse = tools["plc_parse_melsec"]["inputSchema"]
        assert parse["required"] == ["params"]
        assert tools["plc_parse_melsec"]["annotations"]["title"] == \
            "Parse MELSEC IL export"
    _run_session(body)


def test_stdio_tool_calls_share_program_store():
    def body(s):
        s.send("initialize", {
            "protocolVersion": PROTOCOL, "capabilities": {},
            "clientInfo": {"name": "rosetta-test", "version": "0"}})
        s.send("notifications/initialized", notify=True)
        out = json.loads(s.call("plc_parse_melsec", {"params": {
            "source": str(SAMPLES / "centrifugal_excerpt.il"),
            "program_id": "STDIO1"}}))
        assert out["program_id"] == "STDIO1"
        assert out["summary"]["instruction_count"] > 0
        progs = json.loads(s.call("plc_list_programs", {}))
        assert "STDIO1" in {p["program_id"] for p in progs["programs"]}
        flags = json.loads(s.call("plc_flag_semantics",
                                  {"params": {"program_id": "STDIO1"}}))
        assert flags, "flag register must not be empty for the golden sample"
        # pydantic model constraints are enforced on the wire, not bypassed
        bad = s.send("tools/call", {"name": "plc_parse_melsec", "arguments": {
            "params": {"source": "LD X0", "program_id": ""}}})
        assert bad.get("isError") is True, bad
    _run_session(body)


if __name__ == "__main__":
    run_module(globals())
