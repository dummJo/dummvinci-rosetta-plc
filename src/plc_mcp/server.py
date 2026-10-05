"""dummvinci-rosetta-plc — MCP server for cross-vendor PLC migration engineering.

Tools (production path Mitsubishi MELSEC -> CODESYS-family targets, ABB
AC500 V3 first):

  plc_parse_melsec          IL text export (+ device-comment CSV) -> IR
  plc_device_xref           IR -> cross-reference table
  plc_flag_semantics        IR -> [VERIFY] assumption register (H1-H12)
  plc_map_io                IR -> I/O channel mapping proposal
  plc_emit_st               IR -> draft single-POU IEC 61131-3 ST
  plc_emit_plcopen_xml      ST -> structured PLCopen TC6 POU
  plc_emit_scl              ST -> Siemens SCL external source (Phase 3 seed)
  plc_lint_st               structural lint of generated code
  plc_generate_report       IR -> markdown engineering-review report
  plc_list_targets          target profiles + library manifests
  plc_list_programs         programs loaded in this server session
  plc_emit_plcopen_project  IR(s) -> multi-POU PLCopen project (GVLs)
  plc_export_project        IR(s) -> full bundle written to disk
  plc_build_project         JSON project config -> full bundle on disk

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
from .parsers.symbols import load_symbols, merge_symbols
from .analysis.xref import build_xref
from .analysis.semantics import flag_semantics
from .analysis.iomap import map_io, DEFAULT_PROFILE
from .analysis.report import generate_report
from .emitters.st import emit_st
from .emitters.plcopen import emit_plcopen_xml, emit_scl, lint_st
from .emitters.project import ProjectOptions, emit_project
from .build import BuildError, build_from_config, build_programs
from .targets import DEFAULT_TARGET, list_targets

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


class ParseInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=False, extra="forbid")
    source: str = Field(..., description=(
        "Either the raw MELSEC instruction-list text, OR a filesystem path "
        "to the exported .il/.txt file. Paths are detected by existence."))
    program_id: str = Field("MAIN", description=(
        "Identifier to store the parsed program under, for chaining "
        "(e.g. 'jmr_centrifugal_1')"), min_length=1, max_length=64)
    dialect: str = Field("melsec_a", description=(
        "Source dialect: 'melsec_a' (A/AnS series) or 'melsec_q'"))
    symbols_source: Optional[str] = Field(None, description=(
        "Optional device-comment export (GX Developer/GX Works CSV/TSV, "
        "UTF-8 or Shift-JIS) as a file path or raw text. Inline [SYMBOLS] "
        "win; disagreements are reported as symbol_conflicts."))


@mcp.tool(name="plc_parse_melsec", annotations={
    "title": "Parse MELSEC IL export", "readOnlyHint": True,
    "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
async def plc_parse_melsec(params: ParseInput) -> str:
    """Parse a Mitsubishi GX Developer/GX Works instruction-list text export
    into the vendor-neutral IR. Returns a summary + stores the program under
    program_id for use by the other tools. Supports [SYMBOLS] sections,
    inline comments, compare contacts (LD= ...), applied instructions.
    Unknown mnemonics are preserved and surfaced — never dropped."""
    text = params.source
    p = Path(params.source)
    try:
        if len(params.source) < 512 and p.exists():
            text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    prog = parse_melsec_il(text, name=params.program_id,
                           dialect=params.dialect)
    sym_stats = None
    if params.symbols_source:
        syms, sym_stats = load_symbols(params.symbols_source)
        merge_symbols(prog, syms, source=(
            params.symbols_source if sym_stats["encoding"] != "text"
            else "inline text"))
    _STORE[params.program_id] = prog
    return json.dumps({
        "program_id": params.program_id,
        "summary": prog.meta,
        "symbols_loaded": len(prog.symbols),
        "symbol_import": sym_stats,
        "symbol_conflicts": len(prog.meta.get("symbol_conflicts", [])),
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
    devices. This is the table the Tsukishima dossier Section 2.1 was built
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
    target: str = Field(DEFAULT_TARGET, description=(
        "Target profile id (see plc_list_targets), e.g. abb_ac500_v3, "
        "codesys_v35, beckhoff_twincat3, schneider_machine_expert, "
        "wago_codesys35, generic_plcopen"))
    naming: str = Field("legacy", description=(
        "'legacy' (M500, xDI_X8B) or 'symbolic' (address + sanitised "
        "comment, e.g. xDI_X8B_LSW_11_vibration)"))
    timer_base_ms: int = Field(100, ge=1, le=1000, description=(
        "MELSEC timer base in ms (A-series low-speed timers: 100)"))


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
    return emit_st(prog, flags, pou_name=params.pou_name,
                   target=params.target, naming=params.naming,
                   timer_base_ms=params.timer_base_ms)


class EmitXmlInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    st_code: str = Field(..., description="ST code from plc_emit_st")
    pou_name: str = Field("PRG_Converted", max_length=64)
    project_name: str = Field("ptts_migration", max_length=64)
    target: str = Field(DEFAULT_TARGET, description="Target profile id")


@mcp.tool(name="plc_emit_plcopen_xml", annotations={
    "title": "Wrap ST into PLCopen XML", "readOnlyHint": True,
    "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
async def plc_emit_plcopen_xml(params: EmitXmlInput) -> str:
    """Wrap ST from plc_emit_st into a structured PLCopen TC6 POU:
    declarations become typed interface variables, only statements go into
    the body. For whole projects (GVLs, segmentation) prefer
    plc_emit_plcopen_project / plc_export_project."""
    return emit_plcopen_xml(params.st_code, params.pou_name,
                            params.project_name, target=params.target)


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
    target: Optional[str] = Field(None, description="Target profile id shown in the header")
    profile_json: Optional[str] = Field(None, description="Optional I/O profile JSON (see plc_map_io)")


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
    profile = json.loads(params.profile_json) if params.profile_json else None
    return generate_report(prog, project=params.project,
                           prepared_by=params.prepared_by,
                           target=params.target, io_profile=profile)


@mcp.tool(name="plc_list_targets", annotations={
    "title": "List target platforms and libraries", "readOnlyHint": True,
    "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
async def plc_list_targets() -> str:
    """List target profiles: CODESYS-family platforms (ABB AC500 V3,
    CODESYS V3.5, Beckhoff TwinCAT 3, Schneider Machine Expert, WAGO),
    the strict-IEC generic profile, and platforms that are NOT supported
    (Rockwell Logix: not CODESYS; AC500 V2: CoDeSys 2.3) with the reason.
    Each profile lists the libraries the generated code needs."""
    return json.dumps(list_targets(), indent=2)


@mcp.tool(name="plc_list_programs", annotations={
    "title": "List loaded programs", "readOnlyHint": True,
    "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
async def plc_list_programs() -> str:
    """List programs parsed in this server session (program_id, size,
    unknown mnemonics, symbol conflicts) for multi-program projects."""
    return json.dumps({"programs": [{
        "program_id": pid, "dialect": p.source_dialect,
        "instructions": p.meta.get("instruction_count", 0),
        "networks": p.meta.get("rung_count", 0),
        "unknown_mnemonics": p.meta.get("unknown_mnemonics", 0),
        "symbols": len(p.symbols),
        "symbol_conflicts": len(p.meta.get("symbol_conflicts", [])),
    } for pid, p in _STORE.items()]}, indent=2)


class ProjectEmitInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    program_ids: list[str] = Field(..., min_length=1, description=(
        "Programs to put in one project, in task order (ids from "
        "plc_parse_melsec). Several ids = consolidation with prefixed GVLs."))
    target: str = Field(DEFAULT_TARGET, description="Target profile id")
    naming: str = Field("legacy", description="'legacy' or 'symbolic'")
    max_networks_per_pou: int = Field(0, ge=0, description=(
        "Split each program into POUs of at most N networks (0 = one POU). "
        "Never splits inside an MC zone."))
    timer_base_ms: int = Field(100, ge=1, le=1000)
    project_name: str = Field("ptts_migration", max_length=64)


def _project_items(ids: list[str]):
    return [(p, flag_semantics(p)) for p in (_get_program(i) for i in ids)]


@mcp.tool(name="plc_emit_plcopen_project", annotations={
    "title": "Emit multi-POU PLCopen project", "readOnlyHint": True,
    "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
async def plc_emit_plcopen_project(params: ProjectEmitInput) -> str:
    """Emit a structured PLCopen TC6 project for a CODESYS-family target:
    GVL_IO / GVL_MEM / GVL_RETAIN, one PROGRAM per segment plus a main
    PRG calling them, typed interfaces, safety banner and TODO [Hx] markers
    preserved. Returns the XML; for large projects use plc_export_project
    so files go to disk instead of the conversation."""
    model = emit_project(_project_items(params.program_ids), ProjectOptions(
        target=params.target, naming=params.naming,
        max_networks_per_pou=params.max_networks_per_pou,
        timer_base_ms=params.timer_base_ms,
        project_name=params.project_name))
    return model.plcopen_xml()


class ExportInput(ProjectEmitInput):
    output_dir: str = Field(..., description=(
        "Directory for the bundle. Must be empty, new, or a previous "
        "rosetta-plc build (its own stale files are removed)."))
    force: bool = Field(False, description=(
        "Allow writing into a non-empty directory that is not a previous "
        "build. Leave False unless the user asked for it."))
    profile_json: Optional[str] = Field(None, description=(
        "Optional I/O profile JSON for the iomap worksheet"))


_WRITE_ANNOTATIONS = {
    "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True,
    "openWorldHint": False}


@mcp.tool(name="plc_export_project", annotations={
    "title": "Export project bundle to disk", **_WRITE_ANNOTATIONS})
async def plc_export_project(params: ExportInput) -> str:
    """Write the full bundle for loaded programs: PLCopen XML, per-POU ST,
    per-program analysis (flags, xref, iomap CSV, report), libraries.json,
    IMPORT.md, MANIFEST.json with sha256. exit_code: 0 no BLOCKER,
    2 BLOCKERs, 3 safety boundary hit."""
    profile = json.loads(params.profile_json) if params.profile_json else None
    try:
        res = build_programs(
            [_get_program(i) for i in params.program_ids],
            project=params.project_name, target=params.target,
            naming=params.naming,
            max_networks_per_pou=params.max_networks_per_pou,
            timer_base_ms=params.timer_base_ms,
            output_dir=params.output_dir, force=params.force,
            io_profile=profile)
    except BuildError as e:
        return json.dumps({"error": str(e)})
    return _build_summary(res)


def _build_summary(res) -> str:
    m = res.manifest
    return json.dumps({
        "exit_code": res.exit_code, "output_dir": res.output_dir,
        "target": m["target"], "main_pous": m["main_pous"],
        "safety_boundary": m["safety_boundary"],
        "programs": m["programs"], "files_written": len(m["outputs"]),
        "next_steps": ["Follow IMPORT.md in the bundle",
                       "Resolve BLOCKERs in analysis/<PID>/report.md",
                       "Compile in the vendor IDE; FAT + cold I/O checkout"],
    }, indent=2)


class BuildInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config_path: str = Field(..., description=(
        "Path to a project JSON config (see samples/project_jmr_example.json)"))
    output_dir: Optional[str] = Field(None, description=(
        "Override the config's output_dir"))
    force: bool = Field(False, description=(
        "Allow writing into a non-empty foreign directory"))


@mcp.tool(name="plc_build_project", annotations={
    "title": "Build project bundle from config", **_WRITE_ANNOTATIONS})
async def plc_build_project(params: BuildInput) -> str:
    """Batch path for big projects: read a JSON project config (programs,
    device-comment files, target, naming, segmentation, library pins) and
    write the deterministic bundle. Same engine as the rosetta-plc CLI."""
    try:
        res = build_from_config(params.config_path, out_dir=params.output_dir,
                                force=params.force)
    except BuildError as e:
        return json.dumps({"error": str(e)})
    return _build_summary(res)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
