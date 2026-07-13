"""dummvinci-rosetta-plc — MCP server for cross-vendor PLC migration engineering.

Tools (Phase 1, production path Mitsubishi MELSEC -> ABB AC500 V3):

  plc_parse_melsec       IL text export -> IR JSON
  plc_device_xref        IR -> cross-reference table
  plc_flag_semantics     IR -> [VERIFY] assumption register
  plc_map_io             IR -> AC500 S500 channel mapping proposal
  plc_emit_st            IR -> draft IEC 61131-3 ST (AC500/CODESYS)
  plc_emit_plcopen_xml   ST -> PLCopen TC6 XML for Automation Builder
  plc_emit_scl           ST -> Siemens SCL external source (Phase 3 seed)
  plc_lint_st            structural lint of generated code

Design contract: deterministic/mechanical work lives here; semantic
judgment stays with the engineer + AI. The server never resolves an
ALWAYS_OFF rung, an MC/MCR zone, or a TO/FROM fieldbus block on its own —
it flags them.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict
from mcp.server.fastmcp import FastMCP

from .ir import Program, Rung, Instruction, Device, Op
from .parsers.melsec import parse_melsec_il
from .analysis.xref import build_xref
from .analysis.semantics import flag_semantics
from .analysis.iomap import map_io, DEFAULT_PROFILE
from .analysis.report import generate_report
from .emitters.st import emit_st
from .emitters.plcopen import emit_plcopen_xml, emit_scl, lint_st

mcp = FastMCP("dummvinci-rosetta-plc")

# In-memory program store so tools can chain without re-passing megabytes.
_STORE: dict[str, Program] = {}


def _program_from_json(data: dict) -> Program:
    prog = Program(name=data["name"], source_dialect=data["source_dialect"],
                   symbols=data.get("symbols", {}), meta=data.get("meta", {}))
    for r in data["rungs"]:
        rung = Rung(start_step=r["start_step"], comment=r.get("comment", ""))
        for i in r["instructions"]:
            rung.instructions.append(Instruction(
                step=i["step"], op=Op(i["op"]),
                operands=[Device.parse(x) for x in i["operands"]],
                raw=i.get("raw", "")))
        prog.rungs.append(rung)
    return prog


def _get_program(program_id: str) -> Program:
    if program_id not in _STORE:
        raise ValueError(
            f"program_id '{program_id}' not found. Available: "
            f"{list(_STORE) or 'none — call plc_parse_melsec first'}")
    return _STORE[program_id]


def _resolve_source(source: str, is_path: bool) -> str:
    """Resolve the parse input. File reads are an explicit opt-in
    (audit MED-2): with is_path False the string is ALWAYS literal IL
    text — never silently promoted to a filesystem read."""
    if not is_path:
        return source
    p = Path(source)
    if ".." in p.parts:
        raise ValueError("Refusing path containing '..' segments")
    return p.read_text(encoding="utf-8", errors="replace")


class ParseInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=False, extra="forbid")
    source: str = Field(..., description=(
        "The raw MELSEC instruction-list text. To read from a file "
        "instead, set source_is_path=true and pass the path here."))
    source_is_path: bool = Field(False, description=(
        "When true, 'source' is treated as a filesystem path to the "
        "exported .il/.txt ('..' segments rejected). Default false: "
        "'source' is always literal text, never a file read."))
    program_id: str = Field("MAIN", description=(
        "Identifier to store the parsed program under, for chaining "
        "(e.g. 'centrifugal_1')"), min_length=1, max_length=64)
    dialect: str = Field("melsec_a", description=(
        "Source dialect: 'melsec_a' (A/AnS series) or 'melsec_q'"))


@mcp.tool(name="plc_parse_melsec", annotations={
    "title": "Parse MELSEC IL export", "readOnlyHint": True,
    "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
async def plc_parse_melsec(params: ParseInput) -> str:
    """Parse a Mitsubishi GX Developer/GX Works instruction-list text export
    into the vendor-neutral IR. Returns a summary + stores the program under
    program_id for use by the other tools. Supports [SYMBOLS] sections,
    inline comments, compare contacts (LD= ...), applied instructions.
    Unknown mnemonics are preserved and surfaced — never dropped."""
    text = _resolve_source(params.source, params.source_is_path)
    prog = parse_melsec_il(text, name=params.program_id,
                           dialect=params.dialect)
    _STORE[params.program_id] = prog
    return json.dumps({
        "program_id": params.program_id,
        "summary": prog.meta,
        "symbols_loaded": len(prog.symbols),
        "next_steps": ["plc_flag_semantics (mandatory before any emit)",
                       "plc_device_xref", "plc_map_io", "plc_emit_st"],
    }, indent=2)


class ProgramRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    program_id: str = Field("MAIN", description="Id from plc_parse_melsec")


@mcp.tool(name="plc_device_xref", annotations={
    "title": "Device cross-reference", "readOnlyHint": True,
    "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
async def plc_device_xref(params: ProgramRef) -> str:
    """Build the full device cross-reference: for every device, where it is
    read, written (OUT/SET/RST/MOV...), symbol name, plus automatic
    detection of double coils, write-only and read-only-never-written
    devices. This is the table the reference migration dossier was built
    from — automated."""
    return json.dumps(build_xref(_get_program(params.program_id)), indent=2)


@mcp.tool(name="plc_flag_semantics", annotations={
    "title": "Semantic hazard flags ([VERIFY] register)", "readOnlyHint": True,
    "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
async def plc_flag_semantics(params: ProgramRef) -> str:
    """Generate the assumption register: every construct whose meaning does
    not survive mechanical translation (always-OFF disables, MC/MCR zones,
    accumulators A0/A1, index registers, TO/FROM fieldbus buffers, CJ jumps,
    double coils, unknown mnemonics, bulk wipes). BLOCKER items must be
    resolved by an engineer before final code is emitted."""
    return json.dumps(flag_semantics(_get_program(params.program_id)),
                      indent=2)


class MapIoInput(ProgramRef):
    profile_json: Optional[str] = Field(None, description=(
        "Optional JSON overriding the default AC500 hardware profile "
        "(DI524x2/DO524x1/DX522x4, relay Y ranges). Keys: di_modules, "
        "do_dc_modules, do_relay_modules, relay_y_ranges."))


@mcp.tool(name="plc_map_io", annotations={
    "title": "Map I/O to AC500 modules", "readOnlyHint": True,
    "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
async def plc_map_io(params: MapIoInput) -> str:
    """Propose a channel-by-channel mapping of legacy X/Y devices onto ABB
    S500 modules, with automatic electrical hazard notes (sink->source
    polarity flip, high-cycle relay outputs needing SSR interposing, DI524
    PNP wiring). Output is a proposal — cold I/O checkout stays mandatory."""
    profile = json.loads(params.profile_json) if params.profile_json else None
    return json.dumps(map_io(_get_program(params.program_id), profile),
                      indent=2)


class EmitStInput(ProgramRef):
    pou_name: str = Field("PRG_Converted", max_length=64)


@mcp.tool(name="plc_emit_st", annotations={
    "title": "Emit draft Structured Text", "readOnlyHint": True,
    "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
async def plc_emit_st(params: EmitStInput) -> str:
    """Emit draft IEC 61131-3 Structured Text for ABB AC500 V3 / CODESYS.
    Evaluates the MELSEC stack machine into boolean expressions; converts
    OUT/SET/RST/PLS, TON timers (K*0.1s and register presets), MOV/math.
    Every semantic hazard is injected as a (*TODO [Hx]*) marker at the
    exact location — the draft is engineering input, not final code."""
    prog = _get_program(params.program_id)
    flags = flag_semantics(prog)
    return emit_st(prog, flags, pou_name=params.pou_name)


class EmitXmlInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    st_code: str = Field(..., description="ST code from plc_emit_st")
    pou_name: str = Field("PRG_Converted", max_length=64)
    project_name: str = Field("ptts_migration", max_length=64)


@mcp.tool(name="plc_emit_plcopen_xml", annotations={
    "title": "Wrap ST into PLCopen XML", "readOnlyHint": True,
    "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
async def plc_emit_plcopen_xml(params: EmitXmlInput) -> str:
    """Wrap generated ST into a PLCopen TC6 XML document for import into
    ABB Automation Builder (or any PLCopen-capable IEC IDE)."""
    return emit_plcopen_xml(params.st_code, params.pou_name,
                            params.project_name)


class EmitSclInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    st_code: str = Field(..., description="ST code from plc_emit_st")
    fb_name: str = Field("FB_Converted", max_length=64)


@mcp.tool(name="plc_emit_scl", annotations={
    "title": "Emit Siemens SCL external source", "readOnlyHint": True,
    "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
async def plc_emit_scl(params: EmitSclInput) -> str:
    """Convert generated ST into a Siemens SCL external-source skeleton for
    TIA Portal ('Generate blocks from source'). SCL differences that cannot
    be handled mechanically (IEC_TIMER instance DBs) are documented in the
    output header. Phase-3 seed — review required."""
    return emit_scl(params.st_code, params.fb_name)


class LintInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    st_code: str = Field(..., description="ST/SCL code to lint")


@mcp.tool(name="plc_lint_st", annotations={
    "title": "Structural lint of generated code", "readOnlyHint": True,
    "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
async def plc_lint_st(params: LintInput) -> str:
    """Structural checks on generated code: IF/END_IF and VAR/END_VAR
    pairing, unresolved TODO [Hx] markers, UNTRANSLATED/UNMAPPED leftovers.
    Not a compiler — Automation Builder / TIA remain the final authority."""
    return json.dumps(lint_st(params.st_code), indent=2)



class ReportInput(ProgramRef):
    project: str = Field("", description="Project label for the report header", max_length=120)
    prepared_by: str = Field("dummvinci-rosetta-plc", max_length=64)


@mcp.tool(name="plc_generate_report", annotations={
    "title": "Generate migration analysis report", "readOnlyHint": True,
    "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
async def plc_generate_report(params: ReportInput) -> str:
    """Generate the full markdown migration analysis report: parse summary,
    semantic hazard register with per-item required actions, I/O mapping
    proposal with electrical warnings, xref anomalies, and a BLOCKER
    sign-off table. This is the engineering-review deliverable that turns
    the [VERIFY] set into a formal checklist."""
    prog = _get_program(params.program_id)
    return generate_report(prog, project=params.project,
                           prepared_by=params.prepared_by)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
