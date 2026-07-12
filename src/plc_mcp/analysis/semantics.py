"""Semantic hazard detection.

Finds every construct whose meaning does NOT survive a mechanical
translation to IEC 61131-3 and emits a [VERIFY]-style assumption register.
This encodes the lessons of the Tsukishima dossier (Section 4):

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
"""
from __future__ import annotations

from typing import Any

from ..ir import Program, Op, DeviceClass, CONTACT_OPS, CMP_OPS
from .xref import build_xref

SEVERITY = {"BLOCKER": 3, "MAJOR": 2, "MINOR": 1}


def _flag(code: str, severity: str, step: int, detail: str,
          action: str) -> dict[str, Any]:
    return {"code": code, "severity": severity, "step": step,
            "detail": detail, "required_action": action}


def flag_semantics(prog: Program) -> dict[str, Any]:
    flags: list[dict[str, Any]] = []
    mc_stack: list[tuple[int, str]] = []

    for rung in prog.rungs:
        contact_specials = []
        for instr in rung.instructions:
            op = instr.op

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

    flags.sort(key=lambda f: (-SEVERITY[f["severity"]], f["step"]))
    counts = {s: sum(1 for f in flags if f["severity"] == s) for s in SEVERITY}
    return {
        "summary": {
            "total": len(flags), **counts,
            "verdict": ("BLOCKED — resolve BLOCKER items before emitting "
                        "final code" if counts["BLOCKER"] else
                        "OK to emit draft code; review MAJOR items"),
        },
        "flags": flags,
    }
