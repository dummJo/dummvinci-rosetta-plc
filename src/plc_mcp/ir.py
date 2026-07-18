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

    _RX = re.compile(r"^([A-Z]{1,2})([0-9A-F]*)(V?)$")

    @classmethod
    def parse(cls, token: str) -> "Device":
        token = token.strip().upper()
        m = cls._RX.match(token)
        if not m:
            return cls(raw=token, dclass=DeviceClass.UNKNOWN)
        prefix, num, idx = m.groups()
        indexed = idx == "V"
        # constants
        if prefix == "K":
            return cls(raw=token, dclass=DeviceClass.CONST,
                       number=int(num) if num else None)
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
