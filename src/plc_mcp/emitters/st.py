"""Emit IEC 61131-3 Structured Text (CODESYS-family dialect) from IR.

The MELSEC instruction list is a stack machine. Each network is walked in
order: contacts build the current logic result (LD/AND/OR/ANB/ORB, MPS/MRD/
MPP restore a stored branch point), and every output instruction is rendered
with the result *at its own position* — so outputs in series and multi-output
MPS rungs keep their individual conditions.

    OUT M10        ->  M10 := <expr>;
    OUT T40 K70    ->  tT40(IN := <expr>, PT := T#7000ms);   (TON instance)
    OUT T42 D4     ->  tT42(IN := <expr>,
                            PT := DINT_TO_TIME(INT_TO_DINT(D4) * 100));
    OUT C10 D279   ->  cC10(CU := <expr>, RESET := FALSE, PV := ...);
    RST T40 / C10  ->  tT40(IN := FALSE) / cC10(RESET := TRUE)
    SET/RST L1000  ->  IF <expr> THEN L1000 := TRUE/FALSE; END_IF;
    LDP/ANDP X0    ->  rtS<step>(CLK := xDI_X0);  ... rtS<step>.Q
    PLS/PLF M119   ->  trigM119(CLK := <expr>); M119 := trigM119.Q;
    MOV T40 D80    ->  D80 := DINT_TO_INT(TIME_TO_DINT(tT40.ET) / 100);

Output is a DRAFT: every semantic hazard found by flag_semantics is injected
as a (* TODO [Hx] ... *) comment at the exact location. The emitter never
silently "fixes" ALWAYS_OFF rungs, MC zones, accumulators or wide results.
Generated text never nests comments (portability across CODESYS-family and
strict IEC compilers).

The Translator below is shared by the single-POU emitter (emit_st) and the
multi-POU project emitter (emitters/project.py).
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Optional

from ..ir import Program, Rung, Instruction, Device, Op, DeviceClass, CMP_OPS
from ..targets import Target, get_target

_CMP_SYM = {Op.CMP_EQ: "=", Op.CMP_NE: "<>", Op.CMP_GT: ">",
            Op.CMP_LT: "<", Op.CMP_GE: ">=", Op.CMP_LE: "<="}
_EDGE_RISE = {Op.LDP, Op.ANDP, Op.ORP}
_EDGE_FALL = {Op.LDF, Op.ANDF, Op.ORF}
_LOADS = {Op.LD, Op.LDI, Op.LDP, Op.LDF}
_ANDS = {Op.AND, Op.ANI, Op.ANDP, Op.ANDF}
_ORS = {Op.OR, Op.ORI, Op.ORP, Op.ORF}
_NEGATED = {Op.LDI, Op.ANI, Op.ORI}
_CONTACTS = _LOADS | _ANDS | _ORS
_ARITH3 = {Op.ADD, Op.SUB, Op.MUL, Op.DIV, Op.DMUL, Op.DDIV}
_INCDEC = {Op.DADD, Op.DSUB, Op.INC, Op.DEC, Op.INCP, Op.DECP}

ELEMENTARY_TYPES = {"BOOL", "BYTE", "WORD", "DWORD", "LWORD", "SINT", "INT",
                    "DINT", "LINT", "USINT", "UINT", "UDINT", "ULINT",
                    "REAL", "LREAL", "TIME", "DATE", "DT", "TOD"}

FIRST_SCAN = "bInitFirstScan"


def neutralize_comment(text: str) -> str:
    """Make text safe to place inside (* ... *): no nested delimiters."""
    return text.replace("(*", "(.").replace("*)", ".)")


def sanitize_ident(text: str, max_len: int = 40) -> str:
    """ASCII IEC identifier fragment: letters/digits joined by single '_'."""
    s = re.sub(r"[^A-Za-z0-9]+", "_", text)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:max_len].rstrip("_")


@dataclass
class VarDecl:
    name: str
    iec_type: str
    kind: str              # input | output | mem | retain | fb | local
    legacy: str = ""       # MELSEC device, e.g. "X8B"
    comment: str = ""
    init: Optional[str] = None

    @property
    def is_fb(self) -> bool:
        return self.iec_type not in ELEMENTARY_TYPES


def _natural_key(v: VarDecl):
    if v.legacy:
        d = Device.parse(v.legacy)
        return (0, d.dclass.value, d.number if d.number is not None else -1,
                v.name)
    return (1, "", -1, v.name)


def sorted_decls(decls) -> list[VarDecl]:
    return sorted(decls, key=_natural_key)


def _mnemonic(ins: Instruction) -> str:
    parts = ins.raw.split()
    return parts[1].upper() if len(parts) > 1 else ins.op.value


class Translator:
    """Turns IR networks into ST statements and collects declarations.

    qualify(kind) -> GVL name, or None for unqualified access. Global
    declarations accumulate across calls; `locals` (edge/pulse trigger
    instances, which are per-instruction state) is reset per POU via
    begin_pou().
    """

    def __init__(self, prog: Program, target: Target,
                 naming: str = "legacy", timer_base_ms: int = 100,
                 qualify: Optional[Callable[[str], Optional[str]]] = None):
        if naming not in ("legacy", "symbolic"):
            raise ValueError("naming must be 'legacy' or 'symbolic'")
        self.prog = prog
        self.t = target
        self.naming = naming
        self.base = int(timer_base_ms)
        self.qualify = qualify
        self.decls: dict[str, VarDecl] = {}
        self.locals: dict[str, VarDecl] = {}
        self.used: set[str] = set()
        self.fb_types: set[str] = set()
        self._pulse_count = Counter(
            i.operands[0].raw for i in prog.all_instructions()
            if i.op in (Op.PLS, Op.PLF) and i.operands)
        self.first_scan_ref()   # always declared, always driven

    # ---------------------------------------------------------- naming
    def begin_pou(self) -> None:
        self.locals = {}
        self.used = set()

    def _base_name(self, d: Device) -> str:
        c = d.dclass
        name = {DeviceClass.INPUT: f"xDI_{d.raw}",
                DeviceClass.OUTPUT: f"xDO_{d.raw}",
                DeviceClass.TIMER: f"t{d.raw}",
                DeviceClass.COUNTER: f"c{d.raw}"}.get(c, d.raw)
        if self.naming == "symbolic":
            sym = sanitize_ident(self.prog.symbols.get(d.raw, ""))
            if sym:
                name = f"{name}_{sym}"
        return name

    def _q(self, name: str, kind: str) -> str:
        self.used.add(name)
        gvl = self.qualify(kind) if self.qualify else None
        return f"{gvl}.{name}" if gvl else name

    def _declare(self, name: str, iec_type: str, kind: str, legacy: str = "",
                 init: Optional[str] = None, comment: str = "") -> None:
        if name not in self.decls:
            sym = self.prog.symbols.get(legacy, "") if legacy else ""
            self.decls[name] = VarDecl(
                name, iec_type, kind, legacy=legacy, init=init,
                comment=comment or f"{legacy} {sym}".strip())
        if iec_type not in ELEMENTARY_TYPES:
            self.fb_types.add(iec_type)

    def _ref(self, d: Device, kind: str, iec_type: str) -> str:
        name = self._base_name(d)
        self._declare(name, iec_type, kind, legacy=d.raw)
        return self._q(name, kind)

    def _local_fb(self, name: str, fb: str, comment: str) -> str:
        if name not in self.locals:
            self.locals[name] = VarDecl(name, fb, "local", comment=comment)
        self.fb_types.add(fb)
        return name

    def first_scan_ref(self) -> str:
        self._declare(FIRST_SCAN, "BOOL", "mem", init="TRUE",
                      comment="first-scan flag for M9038/M9039; cleared at "
                              "end of scan")
        return self._q(FIRST_SCAN, "mem")

    @staticmethod
    def unmapped(d: Device, use: str = "") -> str:
        return f"(*UNMAPPED {d.raw}{' ' + use if use else ''}*)"

    def _acc(self, d: Device) -> str:
        name = f"iAcc{d.raw}"
        self._declare(name, "INT", "mem", comment=f"{d.raw} accumulator")
        return f"{self._q(name, 'mem')} (*TODO [H3] accumulator - collapse pair*)"

    def _fb(self, d: Device, fb: str) -> str:
        return self._ref(d, "fb", fb)

    # ---------------------------------------------------------- operands
    def bit(self, d: Device) -> str:
        c = d.dclass
        if c == DeviceClass.INPUT:
            return self._ref(d, "input", "BOOL")
        if c == DeviceClass.OUTPUT:
            return self._ref(d, "output", "BOOL")
        if c == DeviceClass.SPECIAL:
            if d.special == "ALWAYS_ON":
                return "TRUE"
            if d.special == "ALWAYS_OFF":
                return "FALSE"
            fs = self.first_scan_ref()
            return fs if d.special == "FIRST_SCAN_ON" else f"NOT {fs}"
        if c == DeviceClass.INTERNAL:
            return self._ref(d, "mem", "BOOL")
        if c == DeviceClass.LATCH:
            return self._ref(d, "retain", "BOOL")
        if c == DeviceClass.TIMER:
            return f"{self._fb(d, 'TON')}.Q"
        if c == DeviceClass.COUNTER:
            return f"{self._fb(d, 'CTU')}.Q"
        if c == DeviceClass.CONST:
            return str(d.number)
        if c == DeviceClass.HCONST:
            return f"16#{d.number:X}"
        if c == DeviceClass.ACCUM:
            return self._acc(d)
        return self.unmapped(d)

    def word(self, d: Device) -> str:
        c = d.dclass
        if c == DeviceClass.CONST:
            return str(d.number)
        if c == DeviceClass.HCONST:
            return f"16#{d.number:X}"
        if c == DeviceClass.DATA:
            return self._ref(d, "mem", "INT")
        if c == DeviceClass.FILE:
            return self._ref(d, "retain", "INT")
        if c == DeviceClass.TIMER:
            # MELSEC timer current value, in timer-base units
            return (f"DINT_TO_INT(TIME_TO_DINT({self._fb(d, 'TON')}.ET) / "
                    f"{self.base})")
        if c == DeviceClass.COUNTER:
            cv = f"{self._fb(d, 'CTU')}.CV"
            return f"WORD_TO_INT({cv})" if self.t.ctu_pv_type == "WORD" else cv
        if c == DeviceClass.ACCUM:
            return self._acc(d)
        return self.unmapped(d, "as word")

    def word_target(self, d: Device) -> Optional[str]:
        if d.dclass in (DeviceClass.DATA, DeviceClass.FILE):
            return self.word(d)
        if d.dclass == DeviceClass.ACCUM:
            return self._acc(d)
        return None

    def bit_target(self, d: Device) -> Optional[str]:
        if d.dclass in (DeviceClass.INPUT, DeviceClass.OUTPUT,
                        DeviceClass.INTERNAL, DeviceClass.LATCH):
            return self.bit(d)
        return None

    def _timer_pt(self, preset: Device) -> str:
        if preset.dclass == DeviceClass.CONST:
            return f"T#{preset.number * self.base}ms"
        if preset.dclass in (DeviceClass.DATA, DeviceClass.FILE):
            return (f"DINT_TO_TIME(INT_TO_DINT({self.word(preset)}) * "
                    f"{self.base})")
        return self.unmapped(preset, "timer preset")

    def _counter_pv(self, preset: Device) -> str:
        if preset.dclass in (DeviceClass.CONST, DeviceClass.HCONST):
            return self.word(preset)
        if preset.dclass in (DeviceClass.DATA, DeviceClass.FILE):
            reg = self.word(preset)
            return f"INT_TO_WORD({reg})" if self.t.ctu_pv_type == "WORD" \
                else reg
        return self.unmapped(preset, "counter preset")

    # ---------------------------------------------------------- networks
    def translate_rung(self, rung: Rung) -> list[str]:
        stack: list[str] = []
        mps: list[str] = []
        lines: list[str] = []
        had_action = False

        def cur() -> str:
            return stack[-1] if stack else "TRUE"

        def untranslated(ins: Instruction, why: str) -> None:
            lines.append(f"(* UNTRANSLATED: step {ins.step} "
                         f"{neutralize_comment(ins.raw or ins.op.value)}"
                         f" - {why} *)")

        for ins in rung.instructions:
            op, ops = ins.op, ins.operands
            if op in _CONTACTS:
                if not ops:
                    untranslated(ins, "contact without operand")
                    continue
                d = ops[0]
                if op in _EDGE_RISE or op in _EDGE_FALL:
                    rise = op in _EDGE_RISE
                    inst = self._local_fb(
                        f"{'rt' if rise else 'ft'}S{ins.step}",
                        "R_TRIG" if rise else "F_TRIG",
                        f"{op.value} {d.raw} edge memory (step {ins.step})")
                    lines.append(f"{inst}(CLK := {self.bit(d)});")
                    e = f"{inst}.Q"
                else:
                    e = self.bit(d)
                    if op in _NEGATED:
                        e = f"NOT {e}"
                if op in _LOADS:
                    stack.append(e)
                elif op in _ANDS:
                    stack.append(f"({stack.pop()} AND {e})" if stack else e)
                else:
                    stack.append(f"({stack.pop()} OR {e})" if stack else e)
            elif op in CMP_OPS:
                if len(ops) < 2:
                    untranslated(ins, "compare needs two operands")
                    continue
                e = f"({self.word(ops[0])} {_CMP_SYM[op]} {self.word(ops[1])})"
                form = _mnemonic(ins)
                if form.startswith("AND") and stack:
                    stack.append(f"({stack.pop()} AND {e})")
                elif form.startswith("OR") and stack:
                    stack.append(f"({stack.pop()} OR {e})")
                else:
                    stack.append(e)
            elif op in (Op.ANB, Op.ORB):
                if len(stack) < 2:
                    untranslated(ins, f"block stack depth {len(stack)}")
                    continue
                b, a = stack.pop(), stack.pop()
                stack.append(f"({a} {'AND' if op is Op.ANB else 'OR'} {b})")
            elif op is Op.MPS:
                mps.append(cur())
            elif op in (Op.MRD, Op.MPP):
                if not mps:
                    untranslated(ins, "no MPS branch point stored")
                    continue
                v = mps[-1] if op is Op.MRD else mps.pop()
                if stack:
                    stack[-1] = v
                else:
                    stack.append(v)
            elif op is Op.NOP:
                continue
            else:
                had_action = True
                lines.extend(self.action(ins, cur()))
        if not had_action:
            lines.append(f"(* condition-only network: "
                         f"{neutralize_comment(cur())} *)")
        return lines

    def action(self, a: Instruction, cond: str) -> list[str]:
        ops = a.operands
        sym = self.prog.symbols.get(ops[0].raw, "") if ops else ""
        symc = f"  (* {neutralize_comment(sym)} *)" if sym else ""

        def guarded(stmt: str) -> str:
            if cond == "TRUE":
                return f"{stmt}{symc}"
            return f"IF {cond} THEN {stmt} END_IF;{symc}"

        def untranslated(why: str) -> list[str]:
            return [f"(* UNTRANSLATED: step {a.step} "
                    f"{neutralize_comment(a.raw or a.op.value)} - {why} *)"]

        d0 = ops[0] if ops else None
        if a.op is Op.OUT and d0 is not None:
            if d0.dclass == DeviceClass.COUNTER:
                pv = self._counter_pv(ops[1]) if len(ops) > 1 else "0"
                return [f"{self._fb(d0, 'CTU')}(CU := {cond}, RESET := FALSE, "
                        f"PV := {pv});{symc}"]
            if d0.dclass == DeviceClass.TIMER:
                pt = self._timer_pt(ops[1]) if len(ops) > 1 else "T#0ms"
                return [f"{self._fb(d0, 'TON')}(IN := {cond}, PT := {pt});"
                        f"{symc}"]
            tgt = self.bit_target(d0)
            return [f"{tgt} := {cond};{symc}"] if tgt else \
                untranslated("not a writable bit device")
        if a.op in (Op.SET, Op.RST) and d0 is not None:
            if a.op is Op.RST and d0.dclass == DeviceClass.TIMER:
                return [guarded(f"{self._fb(d0, 'TON')}(IN := FALSE);")]
            if a.op is Op.RST and d0.dclass == DeviceClass.COUNTER:
                return [guarded(f"{self._fb(d0, 'CTU')}(RESET := TRUE);")]
            if a.op is Op.RST:
                w = self.word_target(d0)
                if w:
                    return [guarded(f"{w} := 0;")]
            tgt = self.bit_target(d0)
            if not tgt:
                return untranslated(f"{a.op.value} on this device type")
            val = "TRUE" if a.op is Op.SET else "FALSE"
            return [guarded(f"{tgt} := {val};")]
        if a.op in (Op.PLS, Op.PLF) and d0 is not None:
            tgt = self.bit_target(d0)
            if not tgt:
                return untranslated("pulse to a non-bit device")
            fb = "R_TRIG" if a.op is Op.PLS else "F_TRIG"
            name = f"trig{d0.raw}" if self._pulse_count[d0.raw] <= 1 \
                else f"trig{d0.raw}_S{a.step}"
            trig = self._local_fb(name, fb, f"{a.op.value} {d0.raw} "
                                  f"(step {a.step})")
            return [f"{trig}(CLK := {cond});  (* {fb} *)",
                    f"{tgt} := {trig}.Q;{symc}"]
        if a.op in (Op.MOV, Op.DMOV) and len(ops) >= 2:
            dst = self.word_target(ops[1])
            if not dst:
                return untranslated("destination has no direct ST target")
            return [guarded(f"{dst} := {self.word(ops[0])};")]
        if a.op in _ARITH3 and len(ops) >= 2:
            sym_op = a.op.value.lstrip("D")
            if len(ops) >= 3:
                s1, s2, dd = ops[0], ops[1], ops[2]
            else:                          # two-operand form: D op S -> D
                s1, s2, dd = ops[1], ops[0], ops[1]
            dst = self.word_target(dd)
            if not dst:
                return untranslated("destination has no direct ST target")
            return [guarded(f"{dst} := {self.word(s1)} {sym_op} "
                            f"{self.word(s2)};")]
        if a.op in _INCDEC and ops:
            sgn = "-" if a.op in (Op.DSUB, Op.DEC, Op.DECP) else "+"
            if a.op in (Op.INC, Op.DEC, Op.INCP, Op.DECP):
                dd, expr_l, expr_r = ops[-1], ops[-1], None
            elif len(ops) >= 3:
                dd, expr_l, expr_r = ops[2], ops[0], ops[1]
            elif len(ops) == 2:
                dd, expr_l, expr_r = ops[1], ops[1], ops[0]
            else:
                return untranslated("operand count")
            dst = self.word_target(dd)
            if not dst:
                return untranslated("destination has no direct ST target")
            rhs = self.word(expr_r) if expr_r is not None else "1"
            return [guarded(f"{dst} := {self.word(expr_l)} {sgn} {rhs};")]
        if a.op in (Op.BMOV, Op.FMOV):
            args = " ".join(o.raw for o in ops)
            return [f"(* TODO block op {a.op.value} {args} - rewrite as array "
                    f"copy/fill under IF {neutralize_comment(cond)} *)"]
        if a.op in (Op.TO, Op.FROM):
            args = " ".join(o.raw for o in ops)
            return [f"(* TODO [H6] {a.op.value} {args} - legacy module "
                    f"buffer, redesign on new fieldbus *)"]
        if a.op is Op.MC:
            ctl = ops[1].raw if len(ops) > 1 else "?"
            return [f"(* TODO [H4] MC zone start, control {ctl}: wrap "
                    f"following networks in IF {ctl} THEN ... ELSE explicit "
                    f"output resets END_IF *)"]
        if a.op is Op.MCR:
            return ["(* TODO [H4] MCR - zone end *)"]
        if a.op is Op.CJ:
            return [f"(* TODO [H7] CJ {ops[0].raw if ops else ''} - "
                    f"restructure as conditional POU call *)"]
        if a.op is Op.END:
            return ["(* END of source program *)"]
        return untranslated("no translation rule")


# ---------------------------------------------------------------- headers

def flags_by_step(flags: dict | None) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for f in (flags or {}).get("flags", []):
        out.setdefault(f["step"], []).append(f)
    return out


def network_header(rung: Rung, by_step: dict[int, list[dict]]) -> list[str]:
    hdr = [f"(* ---- step {rung.start_step}"
           + (f" - {neutralize_comment(rung.comment)}" if rung.comment else "")
           + " ---- *)"]
    for i in rung.instructions:
        for f in by_step.get(i.step, []):
            hdr.append(f"(* TODO [{f['code']}|{f['severity']}] "
                       f"{neutralize_comment(f['required_action'])} *)")
    return hdr


def safety_banner(flags: dict | None) -> str:
    summary = (flags or {}).get("summary", {})
    if not summary.get("safety_boundary"):
        return ""
    cats = ", ".join(summary.get("safety_categories", []))
    return (
        "(* #############################################################\n"
        "   ##  SAFETY BOUNDARY HIT - DO NOT USE THIS OUTPUT FOR SAFETY ##\n"
        f"   ##  Detected safety-instrumented function(s): {cats}\n"
        "   ##  These rungs carry TODO [H11-SAFETY-BOUNDARY] markers.\n"
        "   ##  This code is STANDARD-PLC ONLY. Safety functions must be\n"
        "   ##  engineered in a CERTIFIED toolchain (ABB AC500-S / PROFIsafe)\n"
        "   ##  under IEC 61508/61511 - NOT mechanically translated here.\n"
        "   ############################################################# *)\n")


def draft_header(title: str, prog: Program, target: Target,
                 timer_base_ms: int) -> str:
    return (
        "(* =============================================================\n"
        f"   {title} - DRAFT auto-conversion, {prog.source_dialect} -> "
        "IEC 61131-3 ST\n"
        f"   Target profile: {target.id} ({target.ide})\n"
        "   Generated by dummvinci-rosetta-plc. NOT commissioning-ready:\n"
        "   every TODO [Hx] marker requires an engineering decision.\n"
        "   NOT for safety-instrumented functions / SIL-rated loops.\n"
        "   ASSUMPTIONS - verify against the MELSEC PLC parameters:\n"
        f"   - timer base {timer_base_ms} ms for every T device (high-speed"
        " timer ranges not modelled)\n"
        "   - retentive devices are L and R only (latch ranges configured"
        " for M/D/T/C are not modelled)\n"
        f"   - CTU preset/value type {target.ctu_pv_type}; first-scan flag"
        " re-arms on reset/power-up, not on STOP->RUN\n"
        "   ============================================================= *)\n")


def decl_line(v: VarDecl, address: Optional[str] = None) -> str:
    at = f" AT {address}" if address else ""
    init = f" := {v.init}" if v.init is not None else ""
    com = f"  (* {neutralize_comment(v.comment)} *)" if v.comment else ""
    return f"    {v.name}{at} : {v.iec_type}{init};{com}"


def var_block(keyword: str, decls, address_of=None) -> str:
    decls = list(decls)
    if not decls:
        return ""
    lines = "\n".join(decl_line(v, address_of(v) if address_of else None)
                      for v in decls)
    return f"{keyword}\n{lines}\nEND_VAR\n"


# ---------------------------------------------------------------- single POU

def emit_st(prog: Program, flags: dict | None = None,
            pou_name: str = "PRG_Converted", target: str | None = None,
            naming: str = "legacy", timer_base_ms: int = 100) -> str:
    """Single-POU draft (all variables local to one PROGRAM). For real
    projects use emitters/project.py, which splits I/O and memory into
    GVLs and large programs into several POUs."""
    tgt = get_target(target, for_emit=True)
    tr = Translator(prog, tgt, naming=naming, timer_base_ms=timer_base_ms)
    by_step = flags_by_step(flags)
    body: list[str] = []
    for rung in prog.rungs:
        body.extend(network_header(rung, by_step))
        body.extend(tr.translate_rung(rung))
        body.append("")
    body.append(f"{FIRST_SCAN} := FALSE;  (* end of scan: first-scan flag "
                "is TRUE only during the first cycle *)")

    def of(*kinds):
        return sorted_decls(v for v in tr.decls.values() if v.kind in kinds)

    plain = of("mem", "fb") + sorted_decls(tr.locals.values())
    plain.sort(key=lambda v: (v.is_fb, v.iec_type if v.is_fb else "",
                              v.iec_type != "BOOL"))
    decl_txt = (
        f"PROGRAM {pou_name}\n"
        + var_block("VAR_INPUT", of("input"))
        + var_block("VAR_OUTPUT", of("output"))
        + var_block("VAR", plain)
        + var_block(f"VAR {tgt.retain_keyword}", of("retain"))
    )
    return (safety_banner(flags) + draft_header(pou_name, prog, tgt,
                                                timer_base_ms)
            + decl_txt + "\n" + "\n".join(body) + "\nEND_PROGRAM\n")
