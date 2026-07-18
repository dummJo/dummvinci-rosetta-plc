"""Parser for Mitsubishi MELSEC instruction-list (IL) text exports.

Supported input: the plain-text "list" export from GX Developer / GX Works2
(Project -> Export / print-to-text), one instruction per line:

    0    LD      M500
    1    ANI     T99
    2    OUT     L1000
    5    OUT     T99 K1          ; timer with constant preset
    95   LD=     R5119 K1374     ; inline compare contact
    113  MOV     K1000 R5001

Also tolerated: lines with only "STEP INSTR OPERANDS", comment lines starting
with ';' or '//', symbol lines of the form "X8B  LSW-11" in an optional
[SYMBOLS] section, and blank lines.

This parser targets the A-series dialect first (the pilot retrofit program) but the
grammar is shared with Q/FX list exports for the instruction subset we
support. Unknown instructions are preserved as Op.UNKNOWN so nothing is
silently dropped — they surface in flag_semantics.
"""
from __future__ import annotations

import re
from ..ir import (Program, Rung, Instruction, Device, Op,
                 CONTACT_OPS, COIL_OPS, CMP_OPS, APPLIED_OPS)

# Ops that only build the rung condition (stack machine) — an LD seen while
# the current network still has ONLY these is a block-open (for a later
# ANB/ORB/MPS), NOT a new network.
_CONDITION_OPS = CONTACT_OPS | CMP_OPS | {Op.ANB, Op.ORB,
                                          Op.MPS, Op.MRD, Op.MPP}

_CMP_MAP = {
    "LD=": Op.CMP_EQ, "LD<>": Op.CMP_NE, "LD>": Op.CMP_GT,
    "LD<": Op.CMP_LT, "LD>=": Op.CMP_GE, "LD<=": Op.CMP_LE,
    "AND=": Op.CMP_EQ, "AND<>": Op.CMP_NE, "AND>": Op.CMP_GT,
    "AND<": Op.CMP_LT, "AND>=": Op.CMP_GE, "AND<=": Op.CMP_LE,
    "OR=": Op.CMP_EQ, "OR<>": Op.CMP_NE, "OR>": Op.CMP_GT,
    "OR<": Op.CMP_LT, "OR>=": Op.CMP_GE, "OR<=": Op.CMP_LE,
    # 32-bit variants
    "LDD=": Op.CMP_EQ, "LDD>": Op.CMP_GT, "LDD<": Op.CMP_LT,
    "ANDD=": Op.CMP_EQ, "ANDD>": Op.CMP_GT, "ANDD<": Op.CMP_LT,
}

_OP_ALIASES = {
    "D+P": Op.DADD, "D-P": Op.DSUB, "MOVP": Op.MOV, "DMOVP": Op.DMOV,
    "BMOVP": Op.BMOV, "FMOVP": Op.FMOV, "TOP": Op.TO, "FROMP": Op.FROM,
    "*P": Op.MUL, "/P": Op.DIV, "+P": Op.ADD, "-P": Op.SUB,
}

_LINE_RX = re.compile(r"^\s*(\d+)\s+(\S+)(?:\s+(.*))?$")
_SYMBOL_RX = re.compile(r"^\s*([A-Z]{1,2}[0-9A-F]+V?)\s+(\S.*)$")


def _to_op(mnemonic: str) -> Op:
    mnemonic = mnemonic.upper()
    if mnemonic in _CMP_MAP:
        return _CMP_MAP[mnemonic]
    if mnemonic in _OP_ALIASES:
        return _OP_ALIASES[mnemonic]
    try:
        return Op(mnemonic)
    except ValueError:
        return Op.UNKNOWN


def parse_melsec_il(text: str, name: str = "MAIN",
                    dialect: str = "melsec_a") -> Program:
    prog = Program(name=name, source_dialect=dialect)
    current: Rung | None = None
    in_symbols = False
    pending_comment = ""
    unknown_count = 0

    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.upper().startswith("[SYMBOLS]"):
            in_symbols = True
            continue
        if stripped.upper().startswith("[PROGRAM]"):
            in_symbols = False
            continue
        # comments
        if stripped.startswith(";") or stripped.startswith("//"):
            pending_comment = stripped.lstrip(";/ ").strip()
            continue
        if in_symbols:
            sm = _SYMBOL_RX.match(stripped)
            if sm:
                prog.symbols[sm.group(1).upper()] = sm.group(2).strip()
            continue

        # strip trailing inline comment
        for marker in (";", "//"):
            if marker in stripped:
                head, _, tail = stripped.partition(marker)
                if head.strip():
                    stripped, inline = head.strip(), tail.strip()
                else:
                    inline = ""
                if inline:
                    pending_comment = inline
                break

        m = _LINE_RX.match(stripped)
        if not m:
            prog.meta.setdefault("unparsed_lines", []).append(
                {"line": lineno, "text": stripped})
            continue
        step = int(m.group(1))
        op = _to_op(m.group(2))
        if op is Op.UNKNOWN:
            unknown_count += 1
        operand_str = (m.group(3) or "").strip()
        operands = [Device.parse(t) for t in operand_str.split()] if operand_str else []
        instr = Instruction(step=step, op=op, operands=operands, raw=stripped)
        if op is Op.UNKNOWN:
            instr.raw = f"{stripped}  <<UNKNOWN MNEMONIC '{m.group(2)}'>>"

        ld_form = op in (Op.LD, Op.LDI, Op.LDP, Op.LDF) or (
            op in CMP_OPS and m.group(2).upper().startswith(("LD",)))
        # An LD only starts a NEW network if the current one already has an
        # action (coil/applied/MC). An LD before any action opens a block
        # that a later ANB/ORB/MPS consumes — splitting there loses the
        # block combine (audit H-1).
        rung_start = ld_form and current is not None and any(
            i.op not in _CONDITION_OPS for i in current.instructions)
        # MCR/END always start their own network
        if op in (Op.MCR, Op.END):
            rung_start = True
        if rung_start or current is None:
            current = Rung(start_step=step, comment=pending_comment)
            pending_comment = ""
            prog.rungs.append(current)
        current.instructions.append(instr)

    prog.meta["instruction_count"] = sum(
        len(r.instructions) for r in prog.rungs)
    prog.meta["rung_count"] = len(prog.rungs)
    prog.meta["unknown_mnemonics"] = unknown_count
    return prog
