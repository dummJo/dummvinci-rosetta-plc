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
                  CMP_OPS)

_CMP_SYM = {Op.CMP_EQ: "=", Op.CMP_NE: "<>", Op.CMP_GT: ">",
            Op.CMP_LT: "<", Op.CMP_GE: ">=", Op.CMP_LE: "<="}


@dataclass
class _Decls:
    bools: set = field(default_factory=set)
    retain_bools: set = field(default_factory=set)
    ints: set = field(default_factory=set)
    retain_ints: set = field(default_factory=set)
    timers: set = field(default_factory=set)
    counters: set = field(default_factory=set)
    trigs: set = field(default_factory=set)
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


def _eval_contacts(instrs: list[Instruction], decls: _Decls,
                   notes: list[str]):
    """Run the MELSEC stack machine over contact instructions.

    Returns final condition expression string (or 'TRUE' if rung has no
    contacts, e.g. unconditional applied instruction).
    """
    stack: list[str] = []
    mps: list[str] = []

    def top() -> str:
        return stack[-1] if stack else "TRUE"

    for ins in instrs:
        op, ops = ins.op, ins.operands
        if op in (Op.LD, Op.LDI):
            e = _var(ops[0], decls)
            stack.append(f"NOT {e}" if op is Op.LDI else e)
        elif op in (Op.LDP, Op.LDF):
            e = _var(ops[0], decls)
            notes.append(f"step {ins.step}: edge contact {op.value} "
                         f"{ops[0].raw} — needs R_TRIG/F_TRIG instance")
            stack.append(f"(*EDGE {op.value}*){e}")
        elif op in (Op.AND, Op.ANI, Op.ANDP, Op.ANDF):
            e = _var(ops[0], decls)
            if op in (Op.ANI,):
                e = f"NOT {e}"
            stack.append(f"({stack.pop()} AND {e})" if stack else e)
        elif op in (Op.OR, Op.ORI, Op.ORP, Op.ORF):
            e = _var(ops[0], decls)
            if op is Op.ORI:
                e = f"NOT {e}"
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
        elif op is Op.ORB:
            if len(stack) >= 2:
                b, a = stack.pop(), stack.pop()
                stack.append(f"({a} OR {b})")
        elif op is Op.MPS:
            mps.append(top())
        elif op is Op.MRD:
            if mps:
                stack.append(mps[-1])
        elif op is Op.MPP:
            if mps:
                stack.append(mps.pop())
        else:
            break  # first non-contact instruction ends condition evaluation
    return top()


def emit_st(prog: Program, flags: dict | None = None,
            pou_name: str = "PRG_Converted") -> str:
    decls = _Decls()
    body: list[str] = []
    flag_by_step: dict[int, list[dict]] = {}
    for f in (flags or {}).get("flags", []):
        flag_by_step.setdefault(f["step"], []).append(f)

    for rung in prog.rungs:
        contacts = [i for i in rung.instructions
                    if i.op not in (Op.OUT, Op.SET, Op.RST, Op.PLS, Op.PLF,
                                    Op.MC, Op.MCR, Op.END)
                    and i.op.value not in ("MOV", "DMOV", "BMOV", "FMOV")
                    and not i.op.value in ("+", "-", "*", "/", "D*", "D/",
                                           "D+", "D-", "INC", "DEC", "INCP",
                                           "DECP", "TO", "FROM", "CJ")]
        actions = [i for i in rung.instructions if i not in contacts]
        notes: list[str] = []
        cond = _eval_contacts(contacts, decls, notes)

        hdr = [f"(* ---- step {rung.start_step}"
               + (f" — {rung.comment}" if rung.comment else "") + " ---- *)"]
        for i in rung.instructions:
            for f in flag_by_step.get(i.step, []):
                hdr.append(f"(* TODO [{f['code']}|{f['severity']}] "
                           f"{f['required_action']} *)")
        for n in notes:
            hdr.append(f"(* NOTE {n} *)")
        body.extend(hdr)

        if not actions:
            body.append(f"(* condition-only network: {cond} *)")
            body.append("")
            continue

        for a in actions:
            ops = a.operands
            sym = prog.symbols.get(ops[0].raw, "") if ops else ""
            symc = f"  (* {sym} *)" if sym else ""
            if a.op is Op.OUT and ops and ops[0].dclass == DeviceClass.COUNTER:
                cname = f"c{ops[0].raw}"
                decls.counters.add(cname)
                pv = _var(ops[1], decls) if len(ops) > 1 else "0"
                body.append(f"{cname}(CU := {cond}, PV := {pv});{symc}  "
                            f"(* RESET input must be wired from the RST "
                            f"rung of {ops[0].raw} *)")
            elif a.op is Op.OUT and ops and ops[0].dclass == DeviceClass.TIMER:
                tname = f"t{ops[0].raw}"
                decls.timers.add(tname)
                pt = _timer_pt(ops[1], decls) if len(ops) > 1 else "T#0s"
                body.append(f"{tname}(IN := {cond}, PT := {pt});{symc}")
            elif a.op is Op.OUT:
                body.append(f"{_var(ops[0], decls)} := {cond};{symc}")
            elif a.op in (Op.SET, Op.RST):
                val = "TRUE" if a.op is Op.SET else "FALSE"
                body.append(f"IF {cond} THEN {_var(ops[0], decls)} := {val}; "
                            f"END_IF;{symc}")
            elif a.op in (Op.PLS, Op.PLF):
                trig = f"trig{ops[0].raw}"
                decls.trigs.add(trig)
                fb = "R_TRIG" if a.op is Op.PLS else "F_TRIG"
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
                body.append(f"(* UNTRANSLATED: {a.raw} *)")
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
        + sect("VAR", decls.trigs, "R_TRIG")
    )
    header = (
        "(* =============================================================\n"
        f"   {pou_name} — DRAFT auto-conversion, {prog.source_dialect} -> "
        "IEC 61131-3 ST\n"
        "   Generated by dummvinci-rosetta-plc. NOT commissioning-ready:\n"
        "   every (*TODO [Hx]*) marker requires an engineering decision.\n"
        "   ============================================================= *)\n")
    return header + decl_txt + "\n" + "\n".join(body) + "\nEND_PROGRAM\n"
