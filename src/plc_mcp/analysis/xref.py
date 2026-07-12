"""Device cross-reference: where every device is written, read, latched."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..ir import (Program, Op, DeviceClass, CONTACT_OPS, COIL_OPS, CMP_OPS,
                  APPLIED_OPS)

# For applied instructions, which operand positions are writes.
_WRITE_POSITIONS = {
    Op.MOV: [1], Op.DMOV: [1], Op.BMOV: [1], Op.FMOV: [1],
    Op.ADD: [2], Op.SUB: [2], Op.MUL: [2], Op.DIV: [2],
    Op.DMUL: [2], Op.DDIV: [2], Op.DADD: [1], Op.DSUB: [1],
    Op.INC: [0], Op.DEC: [0], Op.INCP: [0], Op.DECP: [0],
    Op.FROM: [2],   # FROM head buffer dest count -> dest is idx 2
    Op.TO: [],      # TO writes to module buffer, reads PLC devices
}


def build_xref(prog: Program) -> dict[str, Any]:
    reads: dict[str, list[int]] = defaultdict(list)
    writes: dict[str, list[dict]] = defaultdict(list)

    for instr in prog.all_instructions():
        if instr.op in CONTACT_OPS or instr.op in CMP_OPS:
            for d in instr.operands:
                if d.dclass not in (DeviceClass.CONST, DeviceClass.HCONST):
                    reads[d.raw].append(instr.step)
        elif instr.op in COIL_OPS:
            for d in instr.operands:
                if d.dclass in (DeviceClass.CONST, DeviceClass.HCONST):
                    continue  # timer preset constant
                writes[d.raw].append({"step": instr.step,
                                      "kind": instr.op.value})
        elif instr.op in APPLIED_OPS:
            wpos = _WRITE_POSITIONS.get(instr.op, [])
            for idx, d in enumerate(instr.operands):
                if d.dclass in (DeviceClass.CONST, DeviceClass.HCONST):
                    continue
                if idx in wpos:
                    writes[d.raw].append({"step": instr.step,
                                          "kind": instr.op.value})
                else:
                    reads[d.raw].append(instr.step)
        elif instr.op in (Op.MC, Op.MCR):
            for d in instr.operands:
                if d.dclass == DeviceClass.INTERNAL or d.dclass == DeviceClass.LATCH:
                    writes[d.raw].append({"step": instr.step, "kind": instr.op.value})

    devices = sorted(set(reads) | set(writes))
    table = {}
    for dev in devices:
        w = writes.get(dev, [])
        table[dev] = {
            "symbol": prog.symbols.get(dev, ""),
            "reads": reads.get(dev, []),
            "writes": w,
            "write_count": len(w),
            "read_count": len(reads.get(dev, [])),
            "multi_coil": len({x["step"] for x in w if x["kind"] == "OUT"}) > 1,
            "written_never_read": bool(w) and dev not in reads,
            "read_never_written": bool(reads.get(dev)) and not w,
        }
    summary = {
        "device_count": len(devices),
        "multi_coil_devices": [d for d, v in table.items() if v["multi_coil"]],
        "written_never_read": [d for d, v in table.items() if v["written_never_read"]],
        "read_never_written": [d for d, v in table.items()
                               if v["read_never_written"]
                               and not d.startswith(("X", "K", "H"))],
    }
    return {"summary": summary, "devices": table}
