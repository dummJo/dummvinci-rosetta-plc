"""Semantic hazard detection.

Finds every construct whose meaning does NOT survive a mechanical
translation to IEC 61131-3 and emits a [VERIFY]-style assumption register.
This encodes the lessons of the reference migration dossier:

  H1  ALWAYS_OFF contact in series      -> rung can never be true; almost
                                           always a commissioning disable.
                                           Human decision required.
  H2  ALWAYS_ON / first-scan specials   -> systematic replacement pass
                                           (TRUE literal / init flag).
  H3  Accumulator A0/A1 usage           -> rewrite divide/multiply pairs.
  H4  MC/MCR zones                      -> outputs-OFF semantics must be
                                           reproduced explicitly.
  H5  Index register V                  -> array indexing rewrite.
  H6  TO/FROM (special module buffer)   -> fieldbus/module code: no
                                           translation path, redesign.
  H7  CJ conditional jumps              -> restructure as conditional calls.
  H8  Multiple OUT coils on one device  -> scan-order dependency risk.
  H9  Unknown mnemonics                 -> parser gap or exotic instruction.
  H10 RST on wide ranges / FMOV K0      -> bulk memory wipe, guard carefully.
  H11 SAFETY-RELATED function            -> OUT OF SCOPE. Not a translation
                                            problem: safety-instrumented
                                            functions belong in a certified
                                            functional-safety toolchain, never
                                            in mechanically-generated code.

Design note on H11 (the safety boundary): this tool does NOT — and must not —
generate code for safety functions. A SIL rating is a property of the whole
safety loop (sensor + certified logic solver + final element + proof test) per
IEC 61508 / IEC 61511, not of a code fragment. When the program touches an
emergency-stop, overload/load-moment, anti-two-block, guard interlock, watchdog
or any safety-instrumented function, H11 fires as a BLOCKER and hands the work
off to a certified path (e.g. ABB AC500-S + SM560-S / PROFIsafe). Detection is
deliberately conservative — it flags for a human, it never clears.
"""
from __future__ import annotations

import re
from typing import Any

from ..ir import (Program, Op, DeviceClass, CONTACT_OPS, CMP_OPS,
                  instruction_error, source_mnemonic, numeric_review_reason)
from .xref import build_xref

SEVERITY = {"BLOCKER": 3, "MAJOR": 2, "MINOR": 1}

_H11_COVERAGE_ACTION = (
    "Do NOT treat the absence of H11 flags as safety clearance. "
    "Obtain the symbol/comment export, or review with the site "
    "engineer which devices belong to safety-instrumented "
    "functions before emitting or trusting any code.")

_PULSE_MNEMONICS = {"D+P", "D-P", "MOVP", "DMOVP", "BMOVP", "FMOVP",
                    "TOP", "FROMP", "*P", "/P", "+P", "-P", "INCP", "DECP"}

# Safety-function detection patterns (case-insensitive), matched against a
# device's symbol description and its raw name. Kept SPECIFIC on purpose: a
# false positive that halts a non-safety rung is cheap; a false negative that
# lets safety logic slip into mechanical translation is not. Generic process
# terms ("limit switch", "brake", "reset") are intentionally NOT here — only
# terms that denote a safety-instrumented function.
_SAFETY_PATTERNS: list[tuple[str, str]] = [
    (r"\b(EMERGENCY|EMERGENCY[\s\-]*STOP|E[\s\-]?STOP|ESTOP|EMG|ESD)\b",
     "emergency-stop"),
    (r"\b(SAFETY|SAFE\s*TORQUE|STO|SIL[\s\-]?\d?|FAIL[\s\-]?SAFE|"
     r"PROFISAFE|F[\s\-]?DI|F[\s\-]?DO)\b", "safety-function"),
    (r"\b(OVER\s*LOAD|OVERLOAD|LOAD\s*MOMENT|LMI|MOPS|AOPS|"
     r"OVER\s*HOIST|OVERHOIST|ANTI[\s\-]?TWO[\s\-]?BLOCK|TWO[\s\-]?BLOCK|"
     r"A2B|SLACK\s*ROPE|OVER\s*SPEED|OVERSPEED|OVER\s*TRAVEL|OVERTRAVEL|"
     r"LOAD\s*CELL|DEAD[\s\-]?MAN|DEADMAN)\b", "crane-load-safety"),
    (r"\b(LIGHT\s*CURTAIN|MUTING|SAFETY\s*GATE|GUARD\s*DOOR|INTERLOCK|"
     r"TWO[\s\-]?HAND|ENABLE\s*SWITCH|SAFETY\s*MAT|SAFETY\s*RELAY)\b",
     "guard-interlock"),
    (r"\b(WATCH\s*DOG|WATCHDOG|WDT)\b", "watchdog"),
]
_SAFETY_RX = [(re.compile(p, re.IGNORECASE), cat) for p, cat in _SAFETY_PATTERNS]


def _safety_category(*texts: str) -> str | None:
    for text in texts:
        if not text:
            continue
        # PLC symbols commonly use underscores as word separators.
        # Preserve all existing patterns and broaden their coverage.
        text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
        text = re.sub(r"[_-]+", " ", text)
        for rx, cat in _SAFETY_RX:
            if rx.search(text):
                return cat
    return None


def _flag(code: str, severity: str, step: int, detail: str,
          action: str) -> dict[str, Any]:
    return {"code": code, "severity": severity, "step": step,
            "detail": detail, "required_action": action}


def reject_safety_text(text: str, *names: str) -> None:
    """Conservative text-based gate for target names and raw ST exports.

    Only exact generated disclaimer prose is excluded. This is screening,
    not certification or complete semantic analysis of arbitrary ST.
    """
    screened = text.replace(
        "NOT for safety-instrumented functions / SIL-rated loops.", "")
    screened = screened.replace(_H11_COVERAGE_ACTION, "")
    explicit = "H11-SAFETY-BOUNDARY" in text.upper() or \
        "SAFETY BOUNDARY HIT" in text.upper()
    if explicit or _safety_category(screened, *names):
        raise ValueError(
            "SAFETY BOUNDARY HIT [H11-SAFETY-BOUNDARY]: emission/export "
            "refused by text-based screening. Use a CERTIFIED IEC "
            "61508/61511 toolchain (e.g. ABB AC500-S / PROFIsafe).")


def _condition_errors(prog: Program) -> list[tuple[int, str]]:
    """Validate stack structure before treating any action as unconditional."""
    errors = []
    for rung in prog.rungs:
        depth, saved, actions = 0, [], False
        for instr in rung.instructions:
            op = instr.op
            mnemonic = source_mnemonic(instr)
            if op in (Op.LD, Op.LDI, Op.LDP, Op.LDF) or (
                    op in CMP_OPS and not mnemonic.startswith(("AND", "OR"))):
                depth += 1
            elif op in CONTACT_OPS or op in CMP_OPS:
                if depth == 0:
                    errors.append((instr.step, f"{mnemonic} has no LD condition"))
            elif op in (Op.ANB, Op.ORB):
                if depth < 2:
                    errors.append((instr.step, f"{op.value} needs two condition blocks"))
                else:
                    depth -= 1
            elif op is Op.MPS:
                if depth != 1:
                    errors.append((instr.step, "MPS requires one complete condition"))
                saved.append(depth)
            elif op in (Op.MRD, Op.MPP):
                if not saved:
                    errors.append((instr.step, f"{op.value} has no matching MPS"))
                else:
                    depth = saved[-1] if op is Op.MRD else saved.pop()
            elif op not in (Op.END, Op.MCR, Op.NOP, Op.RET):
                actions = True
                if depth != 1:
                    errors.append((instr.step,
                                   f"{op.value} requires one complete LD condition; "
                                   f"found {depth} uncombined blocks"))
        if saved:
            errors.append((rung.start_step, "MPS branch stack is not closed by MPP"))
        if depth > 1:
            errors.append((rung.start_step, "LD blocks remain uncombined"))
        elif depth and not actions:
            errors.append((rung.start_step, "Condition network contains no action"))
    return errors


def flag_semantics(prog: Program) -> dict[str, Any]:
    flags: list[dict[str, Any]] = []
    mc_stack: list[tuple[int, str]] = []

    if not any(prog.all_instructions()):
        flags.append(_flag(
            "H9-EMPTY-PROGRAM", "BLOCKER", 0,
            "No instructions were parsed from the supplied source.",
            "Provide a numbered MELSEC instruction-list export; do not "
            "treat an empty generated program as a successful conversion."))
    for line in prog.meta.get("unparsed_lines", []):
        flags.append(_flag(
            "H9-UNPARSED-LINE", "BLOCKER", 0,
            f"Source line {line['line']} was not parsed: {line['text']}",
            "Review the original export and resolve every unparsed line "
            "before emission; source instructions must never be discarded."))
    for conflict in prog.meta.get("symbol_conflicts", []):
        flags.append(_flag(
            "H9-SYMBOL-CONFLICT", "BLOCKER", 0,
            f"Conflicting symbol declarations for {conflict['device']} "
            f"at source line {conflict['line']}: "
            f"{conflict['first']!r} versus {conflict['second']!r}.",
            "Resolve the conflicting declarations against the original export. "
            "Earlier descriptions are preserved for safety screening."))
    for step, error in _condition_errors(prog):
        flags.append(_flag(
            "H9-INVALID-CONDITION-STACK", "BLOCKER", step, error,
            "Restore the complete numbered LD/ANB/ORB/MPS/MRD/MPP sequence "
            "from the vendor export; no missing condition is guessed."))

    for rung in prog.rungs:
        contact_specials = []
        for instr in rung.instructions:
            op = instr.op
            error = instruction_error(instr)
            if error:
                flags.append(_flag(
                    "H9-MALFORMED-INSTRUCTION", "BLOCKER", instr.step,
                    error + f" ({instr.raw})",
                    "Correct the operand list against the original "
                    "vendor export before generating code."))
            if source_mnemonic(instr) in _PULSE_MNEMONICS:
                flags.append(_flag(
                    "H9-UNSUPPORTED-PULSE", "BLOCKER", instr.step,
                    f"Pulse instruction {source_mnemonic(instr)} cannot be "
                    "replaced by its continuous form.",
                    "Review and implement the vendor-specific pulse behavior "
                    "explicitly; code emission is refused rather than changing "
                    "the action on a held contact."))
            review = numeric_review_reason(instr)
            if review:
                flags.append(_flag(
                    ("H9-NUMERIC-CONDITION" if op in CMP_OPS
                     else "H9-NUMERIC-VALUE-ACCESS"),
                    "BLOCKER", instr.step, instr.raw,
                    review))

            if op in CONTACT_OPS:
                for d in instr.operands:
                    if d.special == "ALWAYS_OFF" and op in (Op.LD, Op.AND):
                        flags.append(_flag(
                            "H1-ALWAYS-OFF-SERIES", "BLOCKER", instr.step,
                            f"NO contact on {d.raw} (always-OFF) in series — "
                            f"rung as written can never energize.",
                            "Open original in GX Developer: confirm NO vs NC "
                            "and whether this is a deliberate commissioning "
                            "disable. Document decision before porting."))
                    elif d.special:
                        contact_specials.append((instr.step, d))

            if op in (Op.CMP_EQ, Op.CMP_NE, Op.CMP_GT, Op.CMP_LT,
                      Op.CMP_GE, Op.CMP_LE):
                pass  # fine — maps to ST comparison

            for d in instr.operands:
                if (d.dclass is DeviceClass.UNKNOWN
                        or (d.dclass is DeviceClass.SPECIAL and not d.special)):
                    flags.append(_flag(
                        "H9-UNKNOWN-OPERAND", "BLOCKER", instr.step,
                        f"Unrecognized or malformed operand {d.raw!r}.",
                        "Verify the device/address syntax in the original "
                        "export; no address or parameter value is guessed."))
                if d.dclass == DeviceClass.ACCUM:
                    flags.append(_flag(
                        "H3-ACCUMULATOR", "MAJOR", instr.step,
                        f"Accumulator {d.raw} used ({op.value}). A0/A1 do "
                        f"not exist outside A-series.",
                        "Rewrite the divide/multiply + MOV pair as one "
                        "direct ST expression."))
                if d.indexed:
                    flags.append(_flag(
                        "H5-INDEX-REGISTER", "MAJOR", instr.step,
                        f"Index-modified device {d.raw}.",
                        "Rewrite as array indexing over a declared ARRAY "
                        "variable; verify bounds."))

            if op is Op.MC:
                nest = instr.operands[0].raw if instr.operands else "?"
                ctl = instr.operands[1].raw if len(instr.operands) > 1 else "?"
                mc_stack.append((instr.step, ctl))
                flags.append(_flag(
                    "H4-MC-ZONE", "MAJOR", instr.step,
                    f"MC zone opens (nest {nest}, control {ctl}). When the "
                    f"zone disables, coils inside go OFF and timers reset.",
                    "Convert to IF zone THEN ... ELSE explicit resets "
                    "END_IF — do NOT convert to plain conditional logic."))
            if op is Op.MCR:
                if mc_stack:
                    mc_stack.pop()
                else:
                    flags.append(_flag(
                        "H4-MCR-UNMATCHED", "MAJOR", instr.step,
                        "MCR without matching MC in parsed scope.",
                        "Verify zone pairing against full program listing."))

            if op in (Op.TO, Op.FROM):
                flags.append(_flag(
                    "H6-MODULE-BUFFER", "BLOCKER", instr.step,
                    f"{op.value} special-module buffer access "
                    f"({' '.join(x.raw for x in instr.operands)}). Typically "
                    f"legacy fieldbus (e.g. OPCN-1) — no translation path.",
                    "Redesign on the new network (Modbus TCP / PROFINET). "
                    "Exclude this block from mechanical conversion."))

            if op is Op.CJ:
                flags.append(_flag(
                    "H7-COND-JUMP", "MAJOR", instr.step,
                    f"CJ to {instr.operands[0].raw if instr.operands else '?'}.",
                    "Restructure as a conditional program/POU call; verify "
                    "no coil freeze-state assumptions across the jump."))

            if op is Op.UNKNOWN:
                flags.append(_flag(
                    "H9-UNKNOWN-MNEMONIC", "BLOCKER", instr.step,
                    instr.raw,
                    "Parser does not know this instruction. Extend parser "
                    "or convert this rung manually."))

            if op is Op.FMOV and instr.operands and \
                    instr.operands[0].raw in ("K0", "H0"):
                flags.append(_flag(
                    "H10-BULK-WIPE", "MAJOR", instr.step,
                    f"FMOV K0 bulk clear: {' '.join(x.raw for x in instr.operands)}.",
                    "Confirm which retained banks this wipes and guard the "
                    "trigger condition explicitly."))

        for step, d in contact_specials:
            flags.append(_flag(
                "H2-SPECIAL-RELAY", "MINOR", step,
                f"{d.raw} ({d.special}) contact.",
                "ALWAYS_ON -> TRUE; FIRST_SCAN_ON / RUN_AFTER_FIRST -> "
                "self-built bInit first-cycle flag. Do as one systematic "
                "pass across the whole program."))

    if mc_stack:
        for step, ctl in mc_stack:
            flags.append(_flag(
                "H4-MC-UNCLOSED", "MAJOR", step,
                f"MC zone (control {ctl}) never closed in parsed scope.",
                "Verify against full listing — parser may have received a "
                "partial export."))

    # H8 multi-coil from xref
    xref = build_xref(prog)
    for dev in xref["summary"]["multi_coil_devices"]:
        steps = [w["step"] for w in xref["devices"][dev]["writes"]]
        flags.append(_flag(
            "H8-MULTI-COIL", "MAJOR", steps[0],
            f"Device {dev} has OUT coils at steps {steps} — scan-order "
            f"dependent double coil.",
            "Merge into one assignment with OR of both conditions, or "
            "verify intentional last-write-wins behavior."))

    # H11 includes unknown instructions too: xref cannot classify their
    # reads/writes, but their operands must still be screened.
    safety_categories: set[str] = set()
    used = {}
    for instr in prog.all_instructions():
        for device in instr.operands:
            used.setdefault(device.raw, instr.step)
    for dev, first in used.items():
        sym = prog.symbols.get(dev, "")
        cat = _safety_category(sym, dev)
        if not cat:
            continue
        safety_categories.add(cat)
        flags.append(_flag(
            "H11-SAFETY-BOUNDARY", "BLOCKER", first,
            f"{dev}{' (' + sym + ')' if sym else ''} matches a "
            f"safety-instrumented pattern [{cat}]. Safety functions are OUT "
            f"OF SCOPE for mechanical translation.",
            "STOP. Do not mechanically translate this function. Route it to a "
            "CERTIFIED functional-safety toolchain (e.g. ABB AC500-S + "
            "SM560-S / PROFIsafe) under the IEC 61508/61511 safety lifecycle "
            "(V-model, FMEDA, independent assessment). The SIL rating is a "
            "property of the whole loop, not of this code. For marine/offshore "
            "cranes also satisfy the classification-society rules (DNV / ABS / "
            "Lloyd's / BV) for the lifting appliance."))

    texts = [(rung.start_step, rung.comment) for rung in prog.rungs]
    texts += [(0, item["text"]) for item in prog.meta.get("comments", [])]
    texts += [(0, item["text"]) for item in prog.meta.get("symbol_texts", [])]
    texts += [(0, prog.name)]
    screened_texts = set()
    for step, text in texts:
        if text in screened_texts:
            continue
        screened_texts.add(text)
        cat = _safety_category(text)
        if cat:
            safety_categories.add(cat)
            flags.append(_flag(
                "H11-SAFETY-BOUNDARY", "BLOCKER", step,
                f"Source comment/name {text!r} matches a "
                f"safety-instrumented pattern [{cat}].",
                "STOP. Safety functions are out of scope. Route them to "
                "a CERTIFIED IEC 61508/61511 toolchain "
                "(e.g. ABB AC500-S / PROFIsafe)."))

    # Audit H-4: H11 screening is symbol-text-driven. A symbol-free export
    # gives it nothing to see — that is REDUCED coverage, never clearance.
    unnamed = [dev for dev in xref["devices"]
               if not prog.symbols.get(dev)
               and not dev.startswith(("K", "H"))]
    screening_limited = not prog.symbols or bool(unnamed)
    if screening_limited:
        flags.append(_flag(
            "H11-COVERAGE-UNKNOWN", "MAJOR", 0,
            "Symbol descriptions are absent or incomplete — H11 safety "
            "screening is text-based and cannot recognize unnamed safety "
            "functions. This is not safety clearance.",
            _H11_COVERAGE_ACTION))

    flags.sort(key=lambda f: (-SEVERITY[f["severity"]], f["step"]))
    counts = {s: sum(1 for f in flags if f["severity"] == s) for s in SEVERITY}
    safety_hit = bool(safety_categories)
    n_safety = sum(1 for f in flags if f["code"] == "H11-SAFETY-BOUNDARY")
    if safety_hit:
        verdict = (
            f"SAFETY BOUNDARY HIT — {n_safety} safety-related function(s) "
            f"detected {sorted(safety_categories)}. These are OUT OF SCOPE: "
            f"hand them to a certified safety toolchain (IEC 61508/61511). "
            f"Do NOT emit or trust generated code for them. "
            + ("Also resolve the remaining BLOCKER items before emitting "
               "final code for the non-safety remainder."
               if counts["BLOCKER"] > n_safety else
               "Non-safety remainder may proceed as draft after review."))
    elif counts["BLOCKER"]:
        verdict = ("BLOCKED — resolve BLOCKER items before emitting final "
                   "code")
    else:
        verdict = "OK to emit draft code; review MAJOR items"
    if screening_limited:
        verdict = ("H11 SCREENING INCOMPLETE — export is NOT "
                   "cleared for safety; see H11-COVERAGE-UNKNOWN. "
                   + verdict)
    return {
        "summary": {
            "total": len(flags), **counts,
            "safety_boundary": safety_hit,
            "safety_categories": sorted(safety_categories),
            "safety_screening_limited": screening_limited,
            "devices_without_symbols": sorted(unnamed),
            "verdict": verdict,
        },
        "flags": flags,
    }
