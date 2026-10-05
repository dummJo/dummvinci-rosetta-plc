"""PLCopen TC6 XML emitter, Siemens SCL seed, and structural lint.

The XML is built from typed declarations, not from text: each POU carries its
variables in <interface> (inputVars/outputVars/localVars/externalVars, with
retain/persistent attributes) and ONLY statements in its ST <body>. Putting
`PROGRAM ... VAR ... END_VAR` text inside the body — what v0.2 did — is
schema-valid but is not an importable POU.

GVL encoding depends on the target profile:
  codesys_adddata  one <addData><data name=".../plcopenxml/globalvars">
                   per GVL, as CODESYS-family IDEs exchange GVLs, plus the
                   qualified_only attribute. Reproduced from the CODESYS
                   export format; NOT yet confirmed by a real IDE import.
  tc6_resource     standard configuration/resource/globalVars + a task with
                   pouInstances (strict IEC consumers).
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional
from xml.sax.saxutils import escape, quoteattr

from .. import __version__
from ..targets import TC6_0200, Target, get_target
from .st import ELEMENTARY_TYPES, VarDecl

XSD_PATH = Path(__file__).resolve().parents[1] / "schemas" / "tc6_xml_v201.xsd"
XHTML_NS = "http://www.w3.org/1999/xhtml"
CODESYS_GVL = "http://www.3s-software.com/plcopenxml/globalvars"
CODESYS_ATTRS = "http://www.3s-software.com/plcopenxml/attributes"
FIXED_TIMESTAMP = "2000-01-01T00:00:00"   # reproducible builds by default


# ------------------------------------------------------------ tiny XML DOM

class El:
    __slots__ = ("tag", "attrs", "children", "text")

    def __init__(self, tag: str, attrs: Optional[dict] = None,
                 children: Optional[list] = None, text: Optional[str] = None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children = children or []
        self.text = text

    def add(self, *children: "El") -> "El":
        self.children.extend(c for c in children if c is not None)
        return self


def _render(el: El, depth: int, out: list[str]) -> None:
    pad = "  " * depth
    attrs = "".join(f" {k}={quoteattr(str(v))}" for k, v in el.attrs.items())
    if el.text is not None:
        out.append(f"{pad}<{el.tag}{attrs}>{escape(el.text)}</{el.tag}>")
    elif not el.children:
        out.append(f"{pad}<{el.tag}{attrs}/>")
    else:
        out.append(f"{pad}<{el.tag}{attrs}>")
        for c in el.children:
            _render(c, depth + 1, out)
        out.append(f"{pad}</{el.tag}>")


def render_xml(root: El) -> str:
    out = ['<?xml version="1.0" encoding="utf-8"?>']
    _render(root, 0, out)
    return "\n".join(out) + "\n"


def _xhtml(text: str) -> El:
    return El("xhtml", {"xmlns": XHTML_NS}, text=text)


def _type(iec_type: str) -> El:
    if iec_type in ELEMENTARY_TYPES:
        return El("type", children=[El(iec_type)])
    return El("type", children=[El("derived", {"name": iec_type})])


def _variable(v: VarDecl, address: Optional[str] = None) -> El:
    attrs = {"name": v.name}
    if address:
        attrs["address"] = address
    el = El("variable", attrs, [_type(v.iec_type)])
    if v.init is not None:
        el.add(El("initialValue", children=[El("simpleValue",
                                               {"value": v.init})]))
    if v.comment:
        el.add(El("documentation", children=[_xhtml(v.comment)]))
    return el


def _var_list(tag: str, decls, attrs: Optional[dict] = None,
              address_of: Optional[Callable] = None,
              tail: Optional[El] = None) -> El:
    el = El(tag, dict(attrs or {}),
            [_variable(v, address_of(v) if address_of else None)
             for v in decls])
    if tail is not None:
        el.add(tail)
    return el


@dataclass
class PouSpec:
    name: str
    body: str
    var_lists: list[tuple[str, list[VarDecl], dict]] = field(
        default_factory=list)          # (tag, decls, attrs)
    pou_type: str = "program"


@dataclass
class GvlSpec:
    name: str
    decls: list[VarDecl]
    retain: bool = False
    persistent: bool = False
    qualified_only: bool = True
    address_of: Optional[Callable] = None


def build_project_xml(project_name: str, pous: list[PouSpec],
                      gvls: list[GvlSpec], target: Target,
                      timestamp: Optional[str] = None,
                      task_pous: Optional[list[str]] = None,
                      task_interval: str = "T#10ms",
                      description: str = "") -> str:
    ts = timestamp or FIXED_TIMESTAMP
    root = El("project", {"xmlns": target.plcopen_ns})
    root.add(El("fileHeader", {
        "companyName": "PTTS", "productName": "dummvinci-rosetta-plc",
        "productVersion": __version__, "creationDateTime": ts,
        **({"contentDescription": description} if description else {})}))
    root.add(El("contentHeader", {"name": project_name,
                                  "modificationDateTime": ts}, [
        El("coordinateInfo", children=[
            El(k, children=[El("scaling", {"x": "1", "y": "1"})])
            for k in ("fbd", "ld", "sfc")])]))

    pou_els = []
    for p in pous:
        iface = El("interface")
        for tag, decls, attrs in p.var_lists:
            if decls:
                iface.add(_var_list(tag, decls, attrs))
        pou = El("pou", {"name": p.name, "pouType": p.pou_type})
        if iface.children:
            pou.add(iface)
        pou.add(El("body", children=[El("ST", children=[_xhtml(p.body)])]))
        pou_els.append(pou)
    root.add(El("types", children=[El("dataTypes"),
                                   El("pous", children=pou_els)]))

    def gvl_attrs(g: GvlSpec) -> dict:
        a = {"name": g.name}
        if g.retain:
            a["retain"] = "true"
        if g.persistent:
            a["persistent"] = "true"
        return a

    if target.gvl_style == "tc6_resource":
        res = El("resource", {"name": "Resource"})
        if task_pous:
            res.add(El("task", {"name": "MainTask", "interval": task_interval,
                                "priority": "1"},
                       [El("pouInstance", {"name": f"inst_{n}",
                                           "typeName": n})
                        for n in task_pous]))
        for g in gvls:
            res.add(_var_list("globalVars", g.decls, gvl_attrs(g),
                              g.address_of))
        root.add(El("instances", children=[El("configurations", children=[
            El("configuration", {"name": "Config"}, [res])])]))
    else:
        root.add(El("instances", children=[El("configurations")]))
        data = []
        for g in gvls:
            tail = None
            if g.qualified_only:
                tail = El("addData", children=[El("data", {
                    "name": CODESYS_ATTRS, "handleUnknown": "implementation"},
                    [El("Attributes", children=[El("Attribute", {
                        "Name": "qualified_only", "Value": ""})])])])
            data.append(El("data", {"name": CODESYS_GVL,
                                    "handleUnknown": "implementation"},
                           [_var_list("globalVars", g.decls, gvl_attrs(g),
                                      g.address_of, tail)]))
        if data:
            root.add(El("addData", children=data))
    return render_xml(root)


# ------------------------------------------------------------ legacy wrapper

_PROGRAM_RX = re.compile(r"^\s*PROGRAM\s+([A-Za-z_]\w*)\s*$")
_SECTION_RX = re.compile(
    r"^\s*(VAR(?:_INPUT|_OUTPUT|_IN_OUT|_EXTERNAL|_TEMP)?)"
    r"(?:\s+(RETAIN|PERSISTENT|CONSTANT))?\s*$")
_DECL_RX = re.compile(
    r"^\s*(?P<name>[A-Za-z_]\w*)(?:\s+AT\s+(?P<addr>%\S+))?\s*:\s*"
    r"(?P<type>[A-Za-z_][\w.]*)\s*(?::=\s*(?P<init>[^;]+?))?\s*;\s*"
    r"(?:\(\*\s*(?P<com>.*?)\s*\*\))?\s*$")
_SECTION_TAG = {"VAR_INPUT": "inputVars", "VAR_OUTPUT": "outputVars",
                "VAR_IN_OUT": "inOutVars", "VAR": "localVars",
                "VAR_EXTERNAL": "externalVars", "VAR_TEMP": "tempVars"}


def split_program(st_code: str):
    """Split a single PROGRAM written in this tool's ST format into
    (header comments, name, [(tag, decls, attrs)], body). Returns None when
    the text is not in that shape — the caller then falls back."""
    lines = st_code.splitlines()
    start = next((i for i, ln in enumerate(lines) if _PROGRAM_RX.match(ln)),
                 None)
    if start is None:
        return None
    try:
        end = max(i for i, ln in enumerate(lines)
                  if ln.strip() == "END_PROGRAM")
    except ValueError:
        return None
    name = _PROGRAM_RX.match(lines[start]).group(1)
    sections = []
    j = start + 1
    while j < end:
        if not lines[j].strip():
            j += 1
            continue
        m = _SECTION_RX.match(lines[j])
        if not m:
            break
        decls = []
        j += 1
        while j < end and lines[j].strip() != "END_VAR":
            dm = _DECL_RX.match(lines[j])
            if not dm:
                return None
            decls.append(VarDecl(dm["name"], dm["type"], "local",
                                 comment=dm["com"] or "",
                                 init=dm["init"].strip() if dm["init"]
                                 else None))
            j += 1
        j += 1
        qual = (m.group(2) or "").lower()
        attrs = {qual: "true"} if qual in ("retain", "persistent",
                                           "constant") else {}
        sections.append((_SECTION_TAG[m.group(1)], decls, attrs))
    header = "\n".join(lines[:start]).strip("\n")
    body = "\n".join(lines[j:end]).strip("\n")
    return header, name, sections, body


def emit_plcopen_xml(st_code: str, pou_name: str = "PRG_Converted",
                     project_name: str = "ptts_migration",
                     timestamp: Optional[str] = None,
                     target: Optional[str] = None) -> str:
    """Wrap ST from plc_emit_st into an importable PLCopen POU: declarations
    become typed interface variables, only statements go into the body.
    Foreign text that is not a single PROGRAM in this tool's format is
    wrapped as a body as-is (and will need manual declarations)."""
    tgt = get_target(target, for_emit=True)
    ts = timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    parts = split_program(st_code)
    if parts is None:
        spec = PouSpec(pou_name, st_code)
    else:
        header, _name, sections, body = parts
        spec = PouSpec(pou_name, (header + "\n" + body) if header else body,
                       sections)
    return build_project_xml(project_name, [spec], [], tgt, timestamp=ts)


def validate_plcopen(xml: str) -> tuple[bool, list[str]]:
    """Validate against the vendored official TC6 v2.01 XSD (namespace
    aligned). Requires lxml: pip install dummvinci-rosetta-plc[validate]."""
    try:
        from lxml import etree
    except ImportError as e:   # pragma: no cover - environment dependent
        raise RuntimeError("lxml is required for XSD validation: "
                           "pip install 'dummvinci-rosetta-plc[validate]'") \
            from e
    schema = etree.XMLSchema(etree.parse(str(XSD_PATH)))
    doc = etree.fromstring(xml.replace(TC6_0200, "http://www.plcopen.org/xml/"
                                                 "tc6_0201").encode("utf-8"))
    ok = schema.validate(doc)
    return ok, [str(e) for e in schema.error_log]


# ------------------------------------------------------------ Siemens SCL

def emit_scl(st_code: str, fb_name: str = "FB_Converted") -> str:
    """Siemens SCL (TIA Portal external source) from the generated ST.

    SCL and IEC ST are near-identical for the constructs we emit; the real
    differences are declaration syntax and timer FBs (TON -> IEC_TIMER /
    TON_TIME instance DBs). We emit an .scl external-source skeleton and
    flag the timer instances for TIA-side instantiation.
    """
    body = st_code
    # crude but honest transformations, each one documented in output
    body = body.replace("PROGRAM ", "FUNCTION_BLOCK ")
    body = body.replace("END_PROGRAM", "END_FUNCTION_BLOCK")
    header = (
        "// Siemens SCL external source — generated by dummvinci-rosetta-plc\n"
        "// IMPORT: TIA Portal -> External source files -> Add, then\n"
        "// 'Generate blocks from source'.\n"
        "// NOTE: TON instances declared as TON must be changed to\n"
        "//   {InstructionName := 'TON'; LibVersion := '1.0'} IEC_TIMER\n"
        "//   or multi-instance TON_TIME. Review every TODO marker —\n"
        "//   semantic hazards are identical to the ST output.\n\n")
    return header + body


# ------------------------------------------------------------ lint

_COMMENT_TOKENS = re.compile(r"\(\*|\*\)|//")
_VAR_OPEN = re.compile(
    r"^\s*VAR(?:_INPUT|_OUTPUT|_IN_OUT|_GLOBAL|_EXTERNAL|_TEMP)?"
    r"(?:\s+(?:RETAIN|PERSISTENT|CONSTANT))*\s*$", re.M)


def _strip_comments(code: str) -> tuple[str, int, int]:
    """Return (code without comments, max nesting depth, final depth).
    `//` starts a line comment only outside block comments."""
    out, depth, maxd, pos, i = [], 0, 0, 0, 0
    while True:
        m = _COMMENT_TOKENS.search(code, i)
        if not m:
            break
        tok = m.group(0)
        if tok == "//":
            if depth == 0:
                out.append(code[pos:m.start()])
                nl = code.find("\n", m.end())
                pos = i = len(code) if nl < 0 else nl
            else:
                i = m.end()
        elif tok == "(*":
            if depth == 0:
                out.append(code[pos:m.start()])
            depth += 1
            maxd = max(maxd, depth)
            pos = i = m.end()
        elif depth == 0:            # stray "*)" is code text, keep it
            i = m.end()
        else:
            depth -= 1
            pos = i = m.end()
    if depth == 0:
        out.append(code[pos:])
    return "".join(out), maxd, depth


def comment_depth(code: str) -> int:
    return _strip_comments(code)[1]


def lint_st(st_code: str) -> dict:
    """Cheap structural lint on comment-stripped code: IF/END_IF and VAR/
    END_VAR pairing, unclosed or nested comments, leftover TODO/UNTRANSLATED/
    UNMAPPED markers. Not a compiler — the vendor IDE is the authority."""
    issues, warnings = [], []
    code, maxd, open_depth = _strip_comments(st_code)
    if open_depth:
        issues.append(f"{open_depth} unclosed (* comment(s)")
    if maxd > 1:
        warnings.append(f"nested comments (depth {maxd}) — not portable to "
                        f"every IEC compiler")
    n_if = len(re.findall(r"\bIF\b", code))
    n_endif = len(re.findall(r"\bEND_IF\b", code))
    if n_if != n_endif:
        issues.append(f"IF/END_IF mismatch: {n_if} IF vs {n_endif} END_IF")
    n_var = len(_VAR_OPEN.findall(code))
    n_endvar = len(re.findall(r"\bEND_VAR\b", code))
    if n_var != n_endvar:
        issues.append(f"VAR/END_VAR mismatch: {n_var} vs {n_endvar}")
    todos = re.findall(r"\(\* ?TODO \[(H\d+[^\]]*)\]", st_code)
    for c, cnt in Counter(todos).most_common():
        warnings.append(f"{cnt}x unresolved TODO [{c}]")
    unt = st_code.count("UNTRANSLATED")
    if unt:
        issues.append(f"{unt} UNTRANSLATED instruction(s) remain")
    unmapped = st_code.count("UNMAPPED")
    if unmapped:
        issues.append(f"{unmapped} UNMAPPED device reference(s)")
    return {"ok": not issues, "issues": issues, "warnings": warnings,
            "note": "Structural lint only — final compile check must be "
                    "done in the vendor IDE (Automation Builder, CODESYS, "
                    "TwinCAT, Machine Expert, TIA Portal)."}
