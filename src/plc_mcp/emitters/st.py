"""Emit IEC 61131-3 Structured Text (ABB AC500 / CODESYS dialect) from IR.

The MELSEC instruction list is a stack machine. This module evaluates each
rung's contact logic into a boolean expression (LD/AND/OR/ANB/ORB/MPS/MRD/
MPP), then renders coils:

    OUT M10        ->  M10 := <expr>;
    OUT T40 K70    ->  tT40(IN := <expr>, PT := T#7s);   (TON instance)
    OUT T42 D4     ->  tT42(IN := <expr>, PT := DINT_TO_TIME(D4)*100);
    SET/RST L1000  ->  IF <expr> THEN L1000 := TRUE/FALSE; END_IF;
    PLS M119       ->  trigM119(CLK := <expr>); M119 := trigM119.Q;
    MOV K1000 D286 ->  IF <expr> THEN D286 := 1000; END_IF;

Output is a DRAFT: every semantic hazard found by flag_semantics is injected
as a (*TODO [Hx] ...*) comment at the exact location. The emitter never
silently "fixes" ALWAYS_OFF rungs, MC zones, or accumulators.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..ir import (Program, Rung, Instruction, Device, Op, DeviceClass,
                  CMP_OPS, validate_iec_identifier, numeric_review_reason)
from ..analysis.semantics import flag_semantics, reject_safety_text

_CMP_SYM = {Op.CMP_EQ: "=", Op.CMP_NE: "<>", Op.CMP_GT: ">",
            Op.CMP_LT: "<", Op.CMP_GE: ">=", Op.CMP_LE: "<="}


def _comment_text(text: str) -> str:
    """Keep source labels from opening or closing generated IEC comments."""
    return text.replace("(*", "( *").replace("*)", "* )")


@dataclass
class _Decls:
    bools: set = field(default_factory=set)
    retain_bools: set = field(default_factory=set)
    ints: set = field(default_factory=set)
    retain_ints: set = field(default_factory=set)
    timers: set = field(default_factory=set)
    counters: set = field(default_factory=set)
    trigs: dict = field(default_factory=dict)
    inputs: set = field(default_factory=set)
    outputs: set = field(default_factory=set)


def _var(d: Device, decls: _Decls) -> str:
    """Map a MELSEC device to an IEC identifier and register declaration."""
    c = d.dclass
    if c == DeviceClass.INPUT:
        name = f"xDI_{d.raw}"
        decls.inputs.add(name)
        return name
    if c == DeviceClass.OUTPUT:
        name = f"xDO_{d.raw}"
        decls.outputs.add(name)
        return name
    if c == DeviceClass.SPECIAL:
        return {"ALWAYS_ON": "TRUE", "ALWAYS_OFF": "FALSE",
                "FIRST_SCAN_ON": "bInitFirstScan",
                "RUN_AFTER_FIRST": "NOT bInitFirstScan"}[d.special]
    if c == DeviceClass.INTERNAL:
        decls.bools.add(d.raw)
        return d.raw
    if c == DeviceClass.LATCH:
        decls.retain_bools.add(d.raw)
        return d.raw
    if c == DeviceClass.TIMER:
        decls.timers.add(f"t{d.raw}")
        return f"t{d.raw}.Q"
    if c == DeviceClass.COUNTER:
        decls.counters.add(f"c{d.raw}")
        return f"c{d.raw}.Q"
    if c == DeviceClass.DATA:
        decls.ints.add(d.raw)
        return d.raw
    if c == DeviceClass.FILE:
        decls.retain_ints.add(d.raw)
        return d.raw
    if c == DeviceClass.CONST:
        return str(d.number)
    if c == DeviceClass.HCONST:
        return f"16#{d.number:X}"
    if c == DeviceClass.ACCUM:
        decls.ints.add(f"iAcc{d.raw}")
        return f"iAcc{d.raw}  (*TODO [H3] accumulator — collapse pair*)"
    return f"(*UNMAPPED {d.raw}*)"


def _timer_pt(preset: Device, decls: _Decls) -> str:
    if preset.dclass == DeviceClass.CONST:
        ms = preset.number * 100  # A-series 100 ms timers
        return f"T#{ms}ms"
    # register-driven preset: value is in 0.1 s units
    reg = _var(preset, decls)
    return f"DINT_TO_TIME(TO_DINT({reg}) * 100)"


# Ops that only build the rung condition. Anything else is an action and
# is emitted against the condition stack's CURRENT top — this keeps each
# MPS/MRD/MPP branch output on its own branch condition (audit H-2).
_COND_OPS = (set(CMP_OPS)
             | {Op.LD, Op.LDI, Op.AND, Op.ANI, Op.OR, Op.ORI,
                Op.LDP, Op.LDF, Op.ANDP, Op.ANDF, Op.ORP, Op.ORF,
                Op.ANB, Op.ORB, Op.MPS, Op.MRD, Op.MPP})


class _CondStack:
    """Stateful MELSEC condition stack machine, fed in program order."""

    def __init__(self, decls: _Decls, notes: list[str]):
        self.stack: list[str] = []
        self.mps: list[list[str]] = []
        self.decls = decls
        self.notes = notes
        self.statements: list[str] = []

    def top(self) -> str:
        if not self.stack:
            raise ValueError("H9 action has no LD condition")
        return self.stack[-1]

    def feed(self, ins: Instruction) -> None:
        op, ops = ins.op, ins.operands
        decls, stack = self.decls, self.stack
        if op in (Op.LD, Op.LDI):
            e = _var(ops[0], decls)
            stack.append(f"NOT {e}" if op is Op.LDI else e)
        elif op in (Op.LDP, Op.LDF):
            e = _var(ops[0], decls)
            stack.append(self.edge(ins, e))
        elif op in (Op.AND, Op.ANI, Op.ANDP, Op.ANDF):
            e = _var(ops[0], decls)
            if op in (Op.ANI,):
                e = f"NOT {e}"
            elif op in (Op.ANDP, Op.ANDF):
                e = self.edge(ins, e)
            stack.append(f"({stack.pop()} AND {e})" if stack else e)
        elif op in (Op.OR, Op.ORI, Op.ORP, Op.ORF):
            e = _var(ops[0], decls)
            if op is Op.ORI:
                e = f"NOT {e}"
            elif op in (Op.ORP, Op.ORF):
                e = self.edge(ins, e)
            stack.append(f"({stack.pop()} OR {e})" if stack else e)
        elif op in CMP_OPS:
            a = _var(ops[0], decls)
            b = _var(ops[1], decls)
            cmp_e = f"({a} {_CMP_SYM[op]} {b})"
            # LD-form pushes; AND/OR-form combines. Heuristic: combine if
            # stack non-empty and raw mnemonic started with AND/OR.
            raw0 = ins.raw.split()[1].upper() if len(ins.raw.split()) > 1 else ""
            if raw0.startswith("AND") and stack:
                stack.append(f"({stack.pop()} AND {cmp_e})")
            elif raw0.startswith("OR") and stack:
                stack.append(f"({stack.pop()} OR {cmp_e})")
            else:
                stack.append(cmp_e)
        elif op is Op.ANB:
            if len(stack) >= 2:
                b, a = stack.pop(), stack.pop()
                stack.append(f"({a} AND {b})")
            else:
                raise ValueError(f"H9 invalid ANB stack at step {ins.step}")
        elif op is Op.ORB:
            if len(stack) >= 2:
                b, a = stack.pop(), stack.pop()
                stack.append(f"({a} OR {b})")
            else:
                raise ValueError(f"H9 invalid ORB stack at step {ins.step}")
        elif op is Op.MPS:
            self.mps.append(stack.copy())
        elif op is Op.MRD:
            if self.mps:
                stack[:] = self.mps[-1]
            else:
                raise ValueError(f"H9 MRD without MPS at step {ins.step}")
        elif op is Op.MPP:
            if self.mps:
                stack[:] = self.mps.pop()
            else:
                raise ValueError(f"H9 MPP without MPS at step {ins.step}")

    def edge(self, ins: Instruction, expression: str) -> str:
        """One edge instance per source instruction, with the actual type."""
        name = f"edge{ins.op.value}_{ins.step}"
        falling = ins.op in (Op.LDF, Op.ANDF, Op.ORF)
        self.decls.trigs[name] = "F_TRIG" if falling else "R_TRIG"
        self.statements.append(f"{name}(CLK := {expression});")
        return f"{name}.Q"


def emit_st(prog: Program, flags: dict | None = None,
            pou_name: str = "PRG_Converted") -> str:
    validate_iec_identifier(pou_name, "pou_name")
    reject_safety_text("", pou_name)
    # Mandatory screening cannot be bypassed by omitting flags or supplying
    # a stale/empty register. This tool never generates detected SIF code.
    mandatory = flag_semantics(prog)
    effective = list(mandatory["flags"])
    for item in (flags or {}).get("flags", []):
        if item not in effective:
            effective.append(item)
    if (mandatory["summary"]["safety_boundary"]
            or (flags or {}).get("summary", {}).get("safety_boundary")
            or any(f["code"] == "H11-SAFETY-BOUNDARY" for f in effective)):
        raise ValueError(
            "SAFETY BOUNDARY HIT [H11-SAFETY-BOUNDARY]: code emission "
            "refused. Route the function to a CERTIFIED IEC 61508/61511 "
            "toolchain (e.g. ABB AC500-S / PROFIsafe).")
    invalid_codes = {"H9-EMPTY-PROGRAM", "H9-UNPARSED-LINE",
                     "H9-MALFORMED-INSTRUCTION", "H9-UNKNOWN-OPERAND",
                     "H9-INVALID-CONDITION-STACK", "H9-SYMBOL-CONFLICT",
                     "H9-UNSUPPORTED-PULSE", "H9-NUMERIC-CONDITION"}
    invalid = [f for f in effective if f["code"] in invalid_codes]
    if invalid:
        raise ValueError("Code emission refused: " + "; ".join(
            f"{f['code']} at step {f['step']}: {f['detail']}"
            for f in invalid))
    decls = _Decls()
    body: list[str] = []
    flag_by_step: dict[int, list[dict]] = {}
    for f in effective:
        flag_by_step.setdefault(f["step"], []).append(f)

    # Audit H-3: counters that are RST somewhere get their CTU RESET input
    # wired from a helper bool that is assigned at the RST rung.
    rst_counters = {i.operands[0].raw for i in prog.all_instructions()
                    if i.op is Op.RST and i.operands
                    and i.operands[0].dclass == DeviceClass.COUNTER}

    for rung in prog.rungs:
        notes: list[str] = []
        cs = _CondStack(decls, notes)

        hdr = [f"(* ---- step {rung.start_step}"
               + (f" — {_comment_text(rung.comment)}" if rung.comment else "")
               + " ---- *)"]
        for i in rung.instructions:
            for f in flag_by_step.get(i.step, []):
                hdr.append(f"(* TODO [{f['code']}|{f['severity']}] "
                           f"{_comment_text(f['required_action'])} *)")
        body.extend(hdr)

        has_action = False
        for a in rung.instructions:
            if a.op is Op.NOP:
                continue
            if a.op in _COND_OPS:
                cs.feed(a)
                body.extend(cs.statements)
                cs.statements.clear()
                while notes:
                    body.append(f"(* NOTE {notes.pop(0)} *)")
                continue
            has_action = True
            cond = cs.top() if a.op not in (Op.END, Op.MCR, Op.RET) else ""
            ops = a.operands
            sym = prog.symbols.get(ops[0].raw, "") if ops else ""
            symc = f"  (* {_comment_text(sym)} *)" if sym else ""
            review = numeric_review_reason(a)
            if review:
                body.append(f"(* TODO [H9-NUMERIC-VALUE-ACCESS] "
                            f"{_comment_text(a.raw)} — {review} *)")
                continue
            if a.op is Op.OUT and ops and ops[0].dclass == DeviceClass.COUNTER:
                cname = f"c{ops[0].raw}"
                decls.counters.add(cname)
                pv = _var(ops[1], decls) if len(ops) > 1 else "0"
                if ops[0].raw in rst_counters:
                    helper = f"xRst{ops[0].raw}"
                    decls.bools.add(helper)
                    body.append(f"{cname}(CU := {cond}, RESET := {helper}, "
                                f"PV := {pv});{symc}  (* RESET assigned at "
                                f"the RST rung of {ops[0].raw} — verify "
                                f"scan order *)")
                else:
                    body.append(f"{cname}(CU := {cond}, PV := {pv});{symc}  "
                                f"(* no RST for {ops[0].raw} in parsed "
                                f"scope — verify reset source *)")
            elif a.op is Op.OUT and ops and ops[0].dclass == DeviceClass.TIMER:
                tname = f"t{ops[0].raw}"
                decls.timers.add(tname)
                pt = _timer_pt(ops[1], decls) if len(ops) > 1 else "T#0s"
                body.append(f"{tname}(IN := {cond}, PT := {pt});{symc}")
            elif a.op is Op.OUT:
                body.append(f"{_var(ops[0], decls)} := {cond};{symc}")
            elif a.op in (Op.SET, Op.RST):
                d = ops[0]
                if a.op is Op.RST and d.dclass == DeviceClass.COUNTER:
                    helper = f"xRst{d.raw}"
                    decls.bools.add(helper)
                    body.append(f"{helper} := {cond};{symc}  (* wired to "
                                f"c{d.raw} RESET input *)")
                elif a.op is Op.RST and d.dclass == DeviceClass.TIMER:
                    body.append(f"(* TODO [RST-TIMER] RST {d.raw}: TON has "
                                f"no reset input — it clears when IN goes "
                                f"FALSE. Verify legacy RST semantics "
                                f"(A-series RST clears contact + elapsed) "
                                f"and restructure the IN condition; do not "
                                f"assign t{d.raw}.Q. *)")
                elif a.op is Op.RST and d.dclass in (DeviceClass.DATA,
                                                     DeviceClass.FILE):
                    body.append(f"IF {cond} THEN {_var(d, decls)} := 0; "
                                f"END_IF;{symc}")
                else:
                    val = "TRUE" if a.op is Op.SET else "FALSE"
                    body.append(f"IF {cond} THEN {_var(d, decls)} := {val}; "
                                f"END_IF;{symc}")
            elif a.op in (Op.PLS, Op.PLF):
                trig = f"trig{ops[0].raw}_{a.op.value}_{a.step}"
                fb = "R_TRIG" if a.op is Op.PLS else "F_TRIG"
                decls.trigs[trig] = fb
                body.append(f"{trig}(CLK := {cond});  (* {fb} *)")
                body.append(f"{_var(ops[0], decls)} := {trig}.Q;{symc}")
            elif a.op in (Op.MOV, Op.DMOV):
                src, dst = _var(ops[0], decls), _var(ops[1], decls)
                body.append(f"IF {cond} THEN {dst} := {src}; END_IF;{symc}")
            elif a.op.value in ("+", "-", "*", "/", "D*", "D/"):
                a1, a2 = _var(ops[0], decls), _var(ops[1], decls)
                dst = _var(ops[2], decls) if len(ops) > 2 else \
                    "iAccA0  (*TODO [H3]*)"
                sym_op = a.op.value.lstrip("D")
                body.append(f"IF {cond} THEN {dst} := {a1} {sym_op} {a2}; "
                            f"END_IF;")
            elif a.op.value in ("D+", "D-", "INC", "DEC", "INCP", "DECP"):
                tgt = _var(ops[-1], decls)
                delta = _var(ops[0], decls) if len(ops) > 1 else "1"
                sgn = "-" if "-" in a.op.value or "DEC" in a.op.value else "+"
                body.append(f"IF {cond} THEN {tgt} := {tgt} {sgn} {delta}; "
                            f"END_IF;{symc}")
            elif a.op in (Op.BMOV, Op.FMOV):
                args = " ".join(o.raw for o in ops)
                body.append(f"(* TODO block op {a.op.value} {args} — "
                            f"rewrite as array copy/fill under IF {cond} *)")
            elif a.op in (Op.TO, Op.FROM):
                args = " ".join(o.raw for o in ops)
                body.append(f"(* TODO [H6] {a.op.value} {args} — legacy "
                            f"module buffer, redesign on new fieldbus *)")
            elif a.op is Op.MC:
                ctl = ops[1].raw if len(ops) > 1 else "?"
                body.append(f"(* TODO [H4] MC zone start, control {ctl}: "
                            f"wrap following networks in IF {ctl} THEN ... "
                            f"ELSE explicit output resets END_IF *)")
            elif a.op is Op.MCR:
                body.append("(* TODO [H4] MCR — zone end *)")
            elif a.op is Op.CJ:
                body.append(f"(* TODO [H7] CJ {ops[0].raw if ops else ''} — "
                            f"restructure as conditional POU call *)")
            elif a.op is Op.END:
                body.append("(* END of source program *)")
            else:
                body.append(f"(* UNTRANSLATED: {_comment_text(a.raw)} *)")
        if not has_action:
            body.append(f"(* condition-only network: {cs.top()} *)")
        body.append("")

    # ---- render POU ----
    def sect(kw: str, names, typ: str) -> str:
        if not names:
            return ""
        lines = "\n".join(f"    {n} : {typ};" for n in sorted(names))
        return f"{kw}\n{lines}\nEND_VAR\n"

    decl_txt = (
        f"PROGRAM {pou_name}\n"
        + sect("VAR_INPUT", decls.inputs, "BOOL")
        + sect("VAR_OUTPUT", decls.outputs, "BOOL")
        + sect("VAR", decls.bools | {"bInitFirstScan"}, "BOOL")
        + sect("VAR RETAIN", decls.retain_bools, "BOOL")
        + sect("VAR", decls.ints, "INT")
        + sect("VAR RETAIN", decls.retain_ints, "INT")
        + sect("VAR", decls.timers, "TON")
        + sect("VAR", decls.counters, "CTU")
        + sect("VAR", [name for name, typ in decls.trigs.items()
                       if typ == "R_TRIG"], "R_TRIG")
        + sect("VAR", [name for name, typ in decls.trigs.items()
                       if typ == "F_TRIG"], "F_TRIG")
    )
    header = (
        "(* =============================================================\n"
        f"   {pou_name} — DRAFT auto-conversion, {prog.source_dialect} -> "
        "IEC 61131-3 ST\n"
        "   Generated by dummvinci-rosetta-plc. NOT commissioning-ready:\n"
        "   every (*TODO [Hx]*) marker requires an engineering decision.\n"
        "   NOT for safety-instrumented functions / SIL-rated loops.\n"
        "   ============================================================= *)\n")
    return (header + decl_txt + "\n" + "\n".join(body)
            + "\nEND_PROGRAM\n")
