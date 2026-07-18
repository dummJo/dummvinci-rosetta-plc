"""Parser for Siemens S7 STL/AWL source (classic S7-300/400 era subset).

Supported input: plain-text AWL as exported from STEP 7 ("generate source")
or typed listings — German (U/UN/O/ON/=/S/R/L/T/SPA...) and English
(A/AN/O/ON/=...) mnemonics, NETWORK/TITLE structure, // and ; comments,
and an optional [SYMBOLS] section using the same convention as the MELSEC
parser (one "ADDRESS  description" per line).

Scope discipline (same contract as the MELSEC parser): the boolean/RLO
subset, outputs, S/R, L/T moves, S5 timers (flagged), jumps and MCR zones
are translated into the shared IR. EVERYTHING ELSE — accumulator math,
counters, DB structure access, pointer/indirect addressing — is preserved
as Op.UNKNOWN and surfaces as an H9 BLOCKER. Nothing is silently dropped.

AWL specifics mapped onto the IR:
  Erstabfrage (first check after an output) -> network/rung start, exactly
    like a MELSEC LD after an action.
  Bare O (OR of AND-groups)                 -> block split + ORB.
  U( / O( ... )                             -> block open + ANB/ORB.
  L <const/time> ; SE T5                    -> OUT T5 <preset> (+S5 flag).
  L src ; T dst                             -> MOV src dst.
  SPA/SPB/SPBN/SPBB label                   -> CJ (H7).
  CALL FBx/FCx                              -> CALL (flagged).
  MCR( / )MCR                               -> MC / MCR (H4 fires).
"""
from __future__ import annotations

import re
from ..ir import Program, Rung, Instruction, Device, DeviceClass, Op

# boolean contact mnemonics -> (Op-if-continuing, negated?)
_BOOL = {
    "U": (Op.AND, False), "UN": (Op.ANI, True),
    "A": (Op.AND, False), "AN": (Op.ANI, True),      # english
    "O": (Op.OR, False), "ON": (Op.ORI, True),
}

_DEV_BIT = re.compile(r"^(E|I|A|Q|M)(\d+)\.([0-7])$", re.IGNORECASE)
_DEV_WORD = re.compile(r"^(EW|IW|AW|QW|MW|MB|MD|EB|IB|AB|QB)(\d+)$",
                       re.IGNORECASE)
_DEV_TC = re.compile(r"^(T|Z|C)(\d+)$", re.IGNORECASE)
_S5TIME = re.compile(r"^S5T#(?:(\d+)H_?)?(?:(\d+)M_?)?(?:(\d+)S_?)?"
                     r"(?:(\d+)MS)?$", re.IGNORECASE)
_TIME_LIT = re.compile(r"^T#(?:(\d+)H)?(?:(\d+)M(?!S))?(?:(\d+)S)?"
                       r"(?:(\d+)MS)?$", re.IGNORECASE)
_INT_LIT = re.compile(r"^-?\d+$")
_HEX_LIT = re.compile(r"^(?:B|W|DW)#16#([0-9A-F]+)$", re.IGNORECASE)

_BIT_CLASS = {"E": DeviceClass.INPUT, "I": DeviceClass.INPUT,
              "A": DeviceClass.OUTPUT, "Q": DeviceClass.OUTPUT,
              "M": DeviceClass.INTERNAL}


def _parse_device(token: str) -> Device:
    """Map a Siemens operand onto the shared IR device model."""
    t = token.strip().upper()
    m = _DEV_BIT.match(t)
    if m:
        area, byte, bit = m.group(1), int(m.group(2)), int(m.group(3))
        return Device(raw=t, dclass=_BIT_CLASS[area], number=byte * 8 + bit)
    m = _DEV_WORD.match(t)
    if m:
        return Device(raw=t, dclass=DeviceClass.DATA, number=int(m.group(2)))
    m = _DEV_TC.match(t)
    if m:
        cls = DeviceClass.TIMER if m.group(1) == "T" else DeviceClass.COUNTER
        return Device(raw=t, dclass=cls, number=int(m.group(2)))
    if _INT_LIT.match(t):
        return Device(raw=f"K{t}" if not t.startswith("-") else t,
                      dclass=DeviceClass.CONST, number=int(t))
    m = _HEX_LIT.match(t)
    if m:
        return Device(raw=t, dclass=DeviceClass.HCONST,
                      number=int(m.group(1), 16))
    # DB access, pointers, symbols in quotes, anything else: preserved,
    # classed UNKNOWN so downstream analysis keeps the raw text visible.
    return Device(raw=t, dclass=DeviceClass.UNKNOWN)


def _time_to_k(token: str) -> tuple[Device, str] | None:
    """S5T#/T# literal -> CONST device in 100 ms units (MELSEC-timer
    compatible so the shared ST emitter renders the right PT)."""
    for rx in (_S5TIME, _TIME_LIT):
        m = rx.match(token)
        if m and any(m.groups()):
            h, mi, s, ms = (int(g) if g else 0 for g in m.groups())
            total_ms = ((h * 60 + mi) * 60 + s) * 1000 + ms
            k = max(1, total_ms // 100)
            return (Device(raw=f"K{k}", dclass=DeviceClass.CONST, number=k),
                    token.upper())
    return None


def parse_siemens_awl(text: str, name: str = "MAIN",
                      dialect: str = "s7_awl") -> Program:
    prog = Program(name=name, source_dialect=dialect)
    current: Rung | None = None
    in_symbols = False
    pending_comment = ""
    unknown_count = 0
    step = 0
    rlo_fresh = True          # next bool contact is an Erstabfrage (LD)
    block_stack: list[str] = []   # openers: 'U(' or 'O('
    accu: tuple[Device, str] | None = None   # last L: (device, raw)

    def new_rung(comment: str = "") -> Rung:
        nonlocal current
        current = Rung(start_step=step, comment=comment)
        prog.rungs.append(current)
        return current

    def emit(op: Op, operands: list[Device], raw: str) -> Instruction:
        nonlocal current
        if current is None:
            new_rung(pending_comment)
        ins = Instruction(step=step, op=op, operands=operands, raw=raw)
        current.instructions.append(ins)
        return ins

    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        up = stripped.upper()
        if up.startswith("[SYMBOLS]"):
            in_symbols = True
            continue
        if up.startswith("[PROGRAM]") or up.startswith("[NETWORKS]"):
            in_symbols = False
            continue
        if in_symbols:
            parts = stripped.split(None, 1)
            if len(parts) == 2:
                prog.symbols[parts[0].upper()] = parts[1].strip()
            continue
        # comments
        if stripped.startswith("//") or stripped.startswith(";"):
            pending_comment = stripped.lstrip("/; ").strip()
            continue
        for marker in ("//", ";"):
            if marker in stripped:
                head, _, tail = stripped.partition(marker)
                if head.strip():
                    stripped = head.strip()
                    if tail.strip():
                        pending_comment = tail.strip()
                break
        up = stripped.upper()

        # structure lines
        if up.startswith("NETWORK"):
            rlo_fresh = True
            current = None
            continue
        if up.startswith("TITLE"):
            _, _, t = stripped.partition("=")
            if t.strip():
                pending_comment = t.strip()
            continue
        if up in ("BE", "BEA", "BEB", "END_FUNCTION_BLOCK", "END_ORGANIZATION_BLOCK"):
            step += 1
            emit(Op.END, [], stripped)
            rlo_fresh = True
            continue
        if up.startswith(("FUNCTION", "ORGANIZATION_BLOCK", "DATA_BLOCK",
                          "VAR", "END_VAR", "BEGIN", "STANDARD",
                          "VERSION")):
            continue  # block scaffolding from source exports

        step += 1
        parts = stripped.split(None, 1)
        mn = parts[0].upper()
        operand = parts[1].strip() if len(parts) > 1 else ""

        # ---- brackets -------------------------------------------------
        if mn in ("U(", "O(", "A(", "UN(", "ON(", "AN("):
            block_stack.append("O" if mn.startswith("O") else "U")
            rlo_fresh = True
            continue
        if mn == ")":
            opener = block_stack.pop() if block_stack else "U"
            emit(Op.ORB if opener == "O" else Op.ANB, [], stripped)
            continue

        # ---- boolean contacts -----------------------------------------
        if mn in _BOOL and operand:
            dev = _parse_device(operand)
            op, neg = _BOOL[mn]
            if rlo_fresh:
                # Erstabfrage: becomes LD/LDI; after an output this also
                # starts a new network (same rule as the MELSEC parser).
                if current is not None and any(
                        i.op not in (Op.LD, Op.LDI, Op.AND, Op.ANI, Op.OR,
                                     Op.ORI, Op.ANB, Op.ORB, Op.MPS,
                                     Op.MRD, Op.MPP)
                        for i in current.instructions) and not block_stack:
                    current = None
                if current is None:
                    new_rung(pending_comment)
                    pending_comment = ""
                emit(Op.LDI if neg else Op.LD, [dev], stripped)
                rlo_fresh = False
            else:
                emit(op, [dev], stripped)
            continue
        if mn == "O" and not operand:
            # bare O: OR of AND-groups — the current RLO chain becomes one
            # term; the next contacts (Erstabfrage -> LD) form the next
            # term; ORB merges them when the group closes (next bare O,
            # bracket close, or output).
            block_stack.append("O_BARE")
            rlo_fresh = True
            continue

        # close pending bare-O group when an output/action arrives
        def _close_bare_o():
            nonlocal block_stack
            while block_stack and block_stack[-1] == "O_BARE":
                block_stack.pop()
                emit(Op.ORB, [], "(bare-O group merge)")

        # ---- edges ------------------------------------------------------
        # FP/FN evaluate the RLO edge (operand = edge-memory bit): the
        # chain CONTINUES, so map to AND-form edge ops — the ST emitter
        # notes the required R_TRIG/F_TRIG instance.
        if mn in ("FP", "FN") and operand:
            dev = _parse_device(operand)
            emit(Op.ANDP if mn == "FP" else Op.ANDF, [dev],
                 stripped + "  (RLO edge, memory bit)")
            continue

        # ---- outputs ----------------------------------------------------
        if mn == "=" and operand:
            _close_bare_o()
            emit(Op.OUT, [_parse_device(operand)], stripped)
            rlo_fresh = True
            continue
        if mn in ("S", "R") and operand:
            _close_bare_o()
            emit(Op.SET if mn == "S" else Op.RST,
                 [_parse_device(operand)], stripped)
            rlo_fresh = True
            continue

        # ---- accumulator: load / transfer -------------------------------
        if mn == "L" and operand:
            tk = _time_to_k(operand)
            accu = tk if tk else (_parse_device(operand), operand)
            continue
        if mn == "T" and operand:
            if accu is None:
                unknown_count += 1
                emit(Op.UNKNOWN, [_parse_device(operand)],
                     f"{stripped}  <<T without preceding L>>")
            else:
                _close_bare_o()
                emit(Op.MOV, [accu[0], _parse_device(operand)],
                     f"L {accu[1]} ; T {operand}")
                accu = None
                rlo_fresh = True
            continue

        # ---- S5 timers ---------------------------------------------------
        if mn in ("SE", "SS", "SI", "SV", "SA") and operand:
            dev = _parse_device(operand)
            preset = accu[0] if accu else Device(
                raw="K0", dclass=DeviceClass.CONST, number=0)
            note = accu[1] if accu else "?"
            accu = None
            emit(Op.OUT, [dev, preset],
                 f"{mn} {operand} (S5 timer, preset {note})")
            rlo_fresh = True
            continue

        # ---- flow --------------------------------------------------------
        if mn in ("SPA", "SPB", "SPBN", "SPBB", "SPBI", "JU", "JC") \
                and operand:
            emit(Op.CJ, [Device(raw=operand.upper(),
                                dclass=DeviceClass.POINTER)], stripped)
            rlo_fresh = True
            continue
        if mn == "CALL":
            emit(Op.CALL, [Device(raw=operand.upper() or "?",
                                  dclass=DeviceClass.UNKNOWN)], stripped)
            rlo_fresh = True
            continue
        if mn == "MCR(":
            emit(Op.MC, [Device(raw="N0", dclass=DeviceClass.NEST),
                         Device(raw="RLO", dclass=DeviceClass.UNKNOWN)],
                 stripped)
            rlo_fresh = True
            continue
        if mn == ")MCR":
            emit(Op.MCR, [], stripped)
            rlo_fresh = True
            continue
        if mn in ("MCRA", "MCRD"):
            continue  # zone arming — informational
        if mn == "NOP":
            continue

        # ---- everything else: preserved, never dropped -------------------
        # An unknown op may have consumed/altered the accumulator: clear
        # it so a following T does NOT silently emit a wrong MOV — it
        # surfaces as its own unknown instead.
        accu = None
        unknown_count += 1
        emit(Op.UNKNOWN, [_parse_device(x) for x in operand.split()
                          if x] if operand else [],
             f"{stripped}  <<UNKNOWN AWL MNEMONIC '{mn}'>>")

    prog.meta["instruction_count"] = sum(
        len(r.instructions) for r in prog.rungs)
    prog.meta["rung_count"] = len(prog.rungs)
    prog.meta["unknown_mnemonics"] = unknown_count
    return prog
