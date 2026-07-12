"""I/O mapping: Mitsubishi X/Y devices -> ABB AC500 S500 module channels.

Default hardware profile mirrors the JMR/DSI centrifugal dossier BOM:
  DI524 x2   (64 DI)               <- X inputs
  DO524 x1   (32 DO, 24VDC source) <- Y DC outputs (lamps, drive cmds)
  DX522 x4   (32 relay outputs)    <- Y AC loads (solenoids, contactors)

Electrical hazards flagged automatically:
  - A1SY41 sink (NPN) -> DO524 source (PNP): every DC load re-referenced.
  - A1SY22 triac -> DX522 relay: high-cycle outputs need SSR interposing.
  - DI524 expects PNP/dry-contact-to-+24V field wiring.
"""
from __future__ import annotations

from typing import Any

from ..ir import Program, DeviceClass
from .xref import build_xref

DEFAULT_PROFILE = {
    "di_modules": [{"name": "DI524_1", "channels": 32},
                   {"name": "DI524_2", "channels": 32}],
    "do_dc_modules": [{"name": "DO524_1", "channels": 32}],
    "do_relay_modules": [{"name": f"DX522_{i}", "channels": 8}
                         for i in range(1, 5)],
    # Y ranges (hex) that drive AC loads on the legacy A1SY22 triac cards.
    # Dossier: Y0C0-Y0DF = solenoids/bell/contactors (2x A1SY22).
    "relay_y_ranges": [(0xC0, 0xDF)],
}


def _in_ranges(n: int, ranges) -> bool:
    return any(lo <= n <= hi for lo, hi in ranges)


def map_io(prog: Program, profile: dict | None = None) -> dict[str, Any]:
    p = {**DEFAULT_PROFILE, **(profile or {})}
    xref = build_xref(prog)
    xs = sorted((d for d in xref["devices"]
                 if d.startswith("X") and not d.startswith("XX")),
                key=lambda d: int(d[1:], 16))
    ys = sorted((d for d in xref["devices"] if d.startswith("Y")),
                key=lambda d: int(d[1:], 16))

    mapping: list[dict[str, Any]] = []
    warnings: list[str] = []

    # inputs
    di_slots = [(m["name"], ch) for m in p["di_modules"]
                for ch in range(m["channels"])]
    if len(xs) > len(di_slots):
        warnings.append(f"{len(xs)} X devices exceed {len(di_slots)} DI "
                        f"channels in profile — add DI modules.")
    for i, dev in enumerate(xs):
        slot = di_slots[i] if i < len(di_slots) else ("UNMAPPED", -1)
        mapping.append({
            "legacy": dev, "symbol": xref["devices"][dev]["symbol"],
            "direction": "DI", "module": slot[0], "channel": slot[1],
            "iec_var": f"xDI_{dev}",
            "note": "DI524 expects PNP/source wiring — verify field common",
        })

    # outputs, split relay vs DC by configured ranges
    relay_devs = [d for d in ys if _in_ranges(int(d[1:], 16), p["relay_y_ranges"])]
    dc_devs = [d for d in ys if d not in relay_devs]

    relay_slots = [(m["name"], ch) for m in p["do_relay_modules"]
                   for ch in range(m["channels"])]
    dc_slots = [(m["name"], ch) for m in p["do_dc_modules"]
                for ch in range(m["channels"])]
    if len(relay_devs) > len(relay_slots):
        warnings.append("Relay outputs exceed DX522 capacity — add module "
                        "or interposing strip.")
    if len(dc_devs) > len(dc_slots):
        warnings.append("DC outputs exceed DO524 capacity — add module.")

    HIGH_CYCLE_HINTS = ("MVMOV", "VS-7", "VS7")
    for i, dev in enumerate(relay_devs):
        slot = relay_slots[i] if i < len(relay_slots) else ("UNMAPPED", -1)
        sym = xref["devices"][dev]["symbol"]
        note = "relay contact for AC load"
        if any(h in sym.upper() for h in HIGH_CYCLE_HINTS):
            note = ("HIGH-CYCLE output — fit SSR interposing relay to avoid "
                    "contact wear (dossier item 10)")
        mapping.append({"legacy": dev, "symbol": sym, "direction": "DO_RELAY",
                        "module": slot[0], "channel": slot[1],
                        "iec_var": f"xDO_{dev}", "note": note})
    for i, dev in enumerate(dc_devs):
        slot = dc_slots[i] if i < len(dc_slots) else ("UNMAPPED", -1)
        mapping.append({"legacy": dev,
                        "symbol": xref["devices"][dev]["symbol"],
                        "direction": "DO_DC", "module": slot[0],
                        "channel": slot[1], "iec_var": f"xDO_{dev}",
                        "note": ("POLARITY: legacy A1SY41 is sink (NPN), "
                                 "DO524 is source (PNP) — re-reference load "
                                 "to 0V")})

    warnings.append("Every mapping row is a proposal — cold I/O checkout "
                    "against the wiring diagram is mandatory (dossier item 13a).")
    return {
        "summary": {"di_used": len(xs), "do_relay_used": len(relay_devs),
                    "do_dc_used": len(dc_devs),
                    "di_capacity": len(di_slots),
                    "do_relay_capacity": len(relay_slots),
                    "do_dc_capacity": len(dc_slots)},
        "warnings": warnings,
        "mapping": mapping,
    }
