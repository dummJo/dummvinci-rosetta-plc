"""Vendor-neutral Intermediate Representation (IR) for PLC programs.

Every parser (Mitsubishi MELSEC, Siemens, ...) produces this IR.
Every emitter (ABB AC500 ST, PLCopen XML, Siemens SCL, ...) consumes it.
The IR is deliberately close to relay-ladder semantics because that is the
lowest common denominator of legacy programs being migrated.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional
import json
import re


class DeviceClass(str, Enum):
    INPUT = "X"          # physical input
    OUTPUT = "Y"         # physical output
    INTERNAL = "M"       # internal relay / marker
    LATCH = "L"          # retained relay
    SPECIAL = "SM"       # special/system relay (M9xxx on A-series)
    TIMER = "T"
    COUNTER = "C"
    DATA = "D"           # data register
    FILE = "R"           # file register (retained)
    INDEX = "V"          # index register
    ACCUM = "A"          # accumulator (A-series only)
    CONST = "K"          # decimal constant
    HCONST = "H"         # hex constant
    POINTER = "P"        # jump pointer
    NEST = "N"           # MC/MCR nesting level
    UNKNOWN = "?"


# A-series special relays that change conversion semantics.
A_SERIES_SPECIALS = {
    "M9036": "ALWAYS_ON",
    "M9037": "ALWAYS_OFF",
    "M9038": "FIRST_SCAN_ON",   # ON during first scan only
    "M9039": "RUN_AFTER_FIRST", # OFF during first scan, ON after
}


@dataclass
class Device:
    raw: str                     # e.g. "M9036", "X8B", "T40", "K10", "R0V"
    dclass: DeviceClass
    number: Optional[int] = None # numeric part where applicable (hex for X/Y)
    indexed: bool = False        # trailing V index modifier (R0V)
    special: Optional[str] = None  # ALWAYS_ON / ALWAYS_OFF / FIRST_SCAN_ON / RUN_AFTER_FIRST

    # SM is the supported two-letter device prefix. A greedy generic
    # prefix would swallow hex digits A-F from XAB/YFF/HAB addresses.
    _RX = re.compile(r"^(SM|[A-Z])([0-9A-F]*)(V?)$")

    @classmethod
    def parse(cls, token: str) -> "Device":
        token = token.strip().upper()
        decimal = re.fullmatch(r"K([+-]?\d+)", token)
        if decimal:
            return cls(raw=token, dclass=DeviceClass.CONST,
                       number=int(decimal.group(1)))
        m = cls._RX.match(token)
        if not m:
            return cls(raw=token, dclass=DeviceClass.UNKNOWN)
        prefix, num, idx = m.groups()
        indexed = idx == "V"
        if not num:
            return cls(raw=token, dclass=DeviceClass.UNKNOWN)
        # constants
        if prefix == "K":
            # Malformed decimal constants such as K1A are diagnostics,
            # never a parser exception or a silently changed constant.
            return cls(raw=token, dclass=DeviceClass.UNKNOWN)
        if prefix == "H":
            return cls(raw=token, dclass=DeviceClass.HCONST,
                       number=int(num, 16) if num else None)
        try:
            dcls = DeviceClass(prefix)
        except ValueError:
            return cls(raw=token, dclass=DeviceClass.UNKNOWN)
        # X/Y device numbers are octal-free hex on MELSEC A
        base = 16 if dcls in (DeviceClass.INPUT, DeviceClass.OUTPUT) else 10
        try:
            number = int(num, base) if num else None
        except ValueError:
            # e.g. "D1A": hex letters on a base-10 device class. Never
            # crash the whole parse — surface as UNKNOWN (H9 catches it).
            return cls(raw=token, dclass=DeviceClass.UNKNOWN)
        special = A_SERIES_SPECIALS.get(token)
        if special:
            dcls = DeviceClass.SPECIAL
        return cls(raw=token, dclass=dcls, number=number,
                   indexed=indexed, special=special)


class Op(str, Enum):
    # contacts
    LD = "LD"; LDI = "LDI"; AND = "AND"; ANI = "ANI"; OR = "OR"; ORI = "ORI"
    LDP = "LDP"; LDF = "LDF"; ANDP = "ANDP"; ANDF = "ANDF"; ORP = "ORP"; ORF = "ORF"
    ANB = "ANB"; ORB = "ORB"; MPS = "MPS"; MRD = "MRD"; MPP = "MPP"
    # coils
    OUT = "OUT"; SET = "SET"; RST = "RST"; PLS = "PLS"; PLF = "PLF"
    # master control
    MC = "MC"; MCR = "MCR"
    # compare (as LD-type contacts): e.g.  LD= D0 K1
    CMP_EQ = "CMP_EQ"; CMP_NE = "CMP_NE"; CMP_GT = "CMP_GT"
    CMP_LT = "CMP_LT"; CMP_GE = "CMP_GE"; CMP_LE = "CMP_LE"
    # data transfer / math (applied instructions)
    MOV = "MOV"; DMOV = "DMOV"; BMOV = "BMOV"; FMOV = "FMOV"
    ADD = "+"; SUB = "-"; MUL = "*"; DIV = "/"; DMUL = "D*"; DDIV = "D/"
    DADD = "D+"; DSUB = "D-"; INC = "INC"; DEC = "DEC"
    INCP = "INCP"; DECP = "DECP"
    # module & flow
    TO = "TO"; FROM = "FROM"; CJ = "CJ"; CALL = "CALL"; RET = "RET"
    END = "END"; NOP = "NOP"
    UNKNOWN = "UNKNOWN"


CONTACT_OPS = {Op.LD, Op.LDI, Op.AND, Op.ANI, Op.OR, Op.ORI,
               Op.LDP, Op.LDF, Op.ANDP, Op.ANDF, Op.ORP, Op.ORF}
COIL_OPS = {Op.OUT, Op.SET, Op.RST, Op.PLS, Op.PLF}
CMP_OPS = {Op.CMP_EQ, Op.CMP_NE, Op.CMP_GT, Op.CMP_LT, Op.CMP_GE, Op.CMP_LE}
APPLIED_OPS = {Op.MOV, Op.DMOV, Op.BMOV, Op.FMOV, Op.ADD, Op.SUB, Op.MUL,
               Op.DIV, Op.DMUL, Op.DDIV, Op.DADD, Op.DSUB, Op.INC, Op.DEC,
               Op.INCP, Op.DECP, Op.TO, Op.FROM, Op.CJ, Op.CALL}


@dataclass
class Instruction:
    step: int
    op: Op
    operands: list[Device] = field(default_factory=list)
    raw: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"step": self.step, "op": self.op.value,
                "operands": [d.raw for d in self.operands], "raw": self.raw}


def instruction_error(instr: Instruction) -> str | None:
    """Check operand counts before analysis/emission indexes operands.

    This validates the supported structural subset, not engineering
    parameter values. Unknown mnemonics remain a separate H9 finding.
    """
    op, operands = instr.op, instr.operands
    minimum = {
        **{item: 1 for item in CONTACT_OPS | COIL_OPS},
        **{item: 2 for item in CMP_OPS},
        Op.MC: 2, Op.MCR: 1,
        Op.MOV: 2, Op.DMOV: 2, Op.BMOV: 3, Op.FMOV: 3,
        Op.ADD: 2, Op.SUB: 2, Op.MUL: 2, Op.DIV: 2,
        Op.DMUL: 2, Op.DDIV: 2, Op.DADD: 2, Op.DSUB: 2,
        Op.INC: 1, Op.DEC: 1, Op.INCP: 1, Op.DECP: 1,
        Op.TO: 4, Op.FROM: 4, Op.CJ: 1, Op.CALL: 1,
    }.get(op, 0)
    if op is Op.OUT and operands and operands[0].dclass in (
            DeviceClass.TIMER, DeviceClass.COUNTER):
        minimum = 2
    maximum = minimum
    if op in (Op.ADD, Op.SUB, Op.MUL, Op.DIV, Op.DMUL, Op.DDIV):
        maximum = 3  # explicit destination or legacy accumulator form
    if op is Op.UNKNOWN:
        return None
    if not minimum <= len(operands) <= maximum:
        expected = str(minimum) if minimum == maximum else \
            f"{minimum} to {maximum}"
        return (f"{op.value} needs {expected} operand(s) in the supported "
                f"subset; received {len(operands)}")
    return operand_role_error(instr)


def source_mnemonic(instr: Instruction) -> str:
    tokens = instr.raw.split()
    return tokens[1].upper() if len(tokens) > 1 and tokens[0].isdigit() \
        else instr.op.value


def operand_role_error(instr: Instruction) -> str | None:
    """Reject unsupported operand roles instead of assigning wrong types."""
    op, operands = instr.op, instr.operands
    if not operands or any(d.dclass is DeviceClass.UNKNOWN for d in operands):
        return None  # counts/unknown operands have their own diagnostics
    writable_bits = {DeviceClass.OUTPUT, DeviceClass.INTERNAL, DeviceClass.LATCH}
    contacts = writable_bits | {DeviceClass.INPUT, DeviceClass.SPECIAL,
                               DeviceClass.TIMER, DeviceClass.COUNTER}
    numbers = {DeviceClass.DATA, DeviceClass.FILE, DeviceClass.ACCUM,
               DeviceClass.CONST, DeviceClass.HCONST}
    numeric_review = {DeviceClass.TIMER, DeviceClass.COUNTER, DeviceClass.INDEX}
    writable_numbers = {DeviceClass.DATA, DeviceClass.FILE, DeviceClass.ACCUM,
                        DeviceClass.INDEX}
    destination = None
    allowed = set()
    if op in CONTACT_OPS and operands[0].dclass not in contacts:
        return f"{op.value} requires a Boolean contact, not {operands[0].raw}"
    if op in CMP_OPS and any(d.dclass not in numbers for d in operands):
        return f"{op.value} numeric comparison operand types are unsupported"
    if op is Op.OUT:
        destination = operands[0]
        allowed = writable_bits | {DeviceClass.TIMER, DeviceClass.COUNTER}
        if destination.dclass in (DeviceClass.TIMER, DeviceClass.COUNTER):
            if len(operands) > 1 and operands[1].dclass not in numbers:
                return f"{op.value} preset must be a numeric constant/register"
    elif op in (Op.SET, Op.PLS, Op.PLF):
        destination, allowed = operands[0], writable_bits
    elif op is Op.RST:
        destination = operands[0]
        allowed = writable_bits | {DeviceClass.DATA, DeviceClass.FILE,
                                   DeviceClass.TIMER, DeviceClass.COUNTER}
    elif op in (Op.MOV, Op.DMOV):
        if operands[0].dclass not in numbers | numeric_review:
            return f"{op.value} source type is unsupported: {operands[0].raw}"
        destination, allowed = operands[1], writable_numbers
    elif op in (Op.ADD, Op.SUB, Op.MUL, Op.DIV, Op.DMUL, Op.DDIV):
        if any(d.dclass not in numbers | numeric_review for d in operands[:2]):
            return f"{op.value} needs numeric source operands"
        if len(operands) > 2:
            destination, allowed = operands[2], writable_numbers
    elif op in (Op.DADD, Op.DSUB):
        if operands[0].dclass not in numbers | numeric_review:
            return f"{op.value} needs a numeric source operand"
        destination, allowed = operands[-1], writable_numbers
    elif op in (Op.INC, Op.DEC, Op.INCP, Op.DECP):
        destination, allowed = operands[0], writable_numbers
    elif op in (Op.BMOV, Op.FMOV):
        destination = operands[1]
        allowed = writable_bits | writable_numbers | {DeviceClass.TIMER,
                                                       DeviceClass.COUNTER}
    elif op is Op.FROM:
        destination, allowed = operands[2], writable_numbers | writable_bits
    elif op is Op.MC:
        destination = operands[1]
        allowed = {DeviceClass.INTERNAL, DeviceClass.LATCH}
    if destination is not None and destination.dclass not in allowed:
        return (f"{op.value} destination {destination.raw} is read-only "
                "or has an unsupported type")
    return None


def numeric_review_reason(instr: Instruction) -> str | None:
    """Do not guess numeric widths or elapsed/count/index value access."""
    if (instr.op in (Op.DMOV, Op.DMUL, Op.DDIV, Op.DADD, Op.DSUB)
            or source_mnemonic(instr).startswith(("LDD", "ANDD", "ORD"))):
        return ("Doubleword width and paired-register semantics are not "
                "modeled by the INT register draft. Review and implement "
                "the vendor's 32-bit register access explicitly; no DINT "
                "cast or paired-register behavior is guessed.")
    numeric_ops = {Op.MOV, Op.DMOV, Op.ADD, Op.SUB, Op.MUL, Op.DIV,
                   Op.DMUL, Op.DDIV, Op.DADD, Op.DSUB, Op.INC, Op.DEC}
    if instr.op in numeric_ops and any(
            d.dclass in (DeviceClass.TIMER, DeviceClass.COUNTER, DeviceClass.INDEX)
            for d in instr.operands):
        return ("Timer elapsed values, counter values and index registers "
                "need explicit vendor-specific value access; Boolean .Q "
                "is not a numeric replacement. Review this instruction.")
    return None


def validate_iec_identifier(name: str, field: str = "name") -> str:
    """Reject malformed generated identifiers instead of emitting syntax."""
    reserved = {"PROGRAM", "END_PROGRAM", "FUNCTION", "END_FUNCTION",
                "FUNCTION_BLOCK", "END_FUNCTION_BLOCK", "VAR", "END_VAR",
                "VAR_INPUT", "VAR_OUTPUT", "VAR_IN_OUT", "VAR_GLOBAL",
                "IF", "THEN", "ELSE", "ELSIF", "END_IF", "CASE", "OF",
                "END_CASE", "FOR", "TO", "BY", "DO", "END_FOR", "WHILE",
                "END_WHILE", "REPEAT", "UNTIL", "END_REPEAT", "TRUE",
                "FALSE", "AND", "OR", "NOT", "XOR", "MOD", "RETAIN"}
    if (not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name)
            or name.upper() in reserved):
        raise ValueError(
            f"{field} must be an IEC identifier of 1 to 64 characters "
            "(letters, digits, underscore; cannot start with a digit "
            "or be a reserved keyword)")
    return name


@dataclass
class Rung:
    """One network: everything from a rung-starting LD/LDI up to (and
    including) its output instruction(s)."""
    start_step: int
    instructions: list[Instruction] = field(default_factory=list)
    comment: str = ""

    @property
    def coils(self) -> list[Instruction]:
        return [i for i in self.instructions
                if i.op in COIL_OPS or i.op in APPLIED_OPS
                or i.op in (Op.MC, Op.MCR)]

    def to_dict(self) -> dict[str, Any]:
        return {"start_step": self.start_step, "comment": self.comment,
                "instructions": [i.to_dict() for i in self.instructions]}


@dataclass
class Program:
    name: str
    source_dialect: str                      # "melsec_a", "melsec_q", ...
    rungs: list[Rung] = field(default_factory=list)
    symbols: dict[str, str] = field(default_factory=dict)  # device -> name
    meta: dict[str, Any] = field(default_factory=dict)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps({
            "name": self.name,
            "source_dialect": self.source_dialect,
            "symbols": self.symbols,
            "meta": self.meta,
            "rungs": [r.to_dict() for r in self.rungs],
        }, indent=indent)

    def all_instructions(self):
        for r in self.rungs:
            yield from r.instructions
