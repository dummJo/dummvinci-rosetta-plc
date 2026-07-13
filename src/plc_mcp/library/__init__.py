"""Engineering library — ST patterns + error-code catalogs (package data).

Patterns implement the concepts in docs/library/ALARM-PHILOSOPHY.md and
HISTORIAN-DESIGN.md. Catalogs obey catalog/README.md: provenance is
mandatory, unknown codes are refused, never guessed.
"""
from __future__ import annotations

import json
import re
from importlib import resources
from typing import Any, Optional

_PKG = "plc_mcp.library"


def _dir(sub: str):
    return resources.files(_PKG) / sub


# --------------------------------------------------------------- patterns

def list_patterns() -> list[dict[str, Any]]:
    out = []
    for f in sorted(_dir("patterns").iterdir(), key=lambda x: x.name):
        if not f.name.endswith(".st"):
            continue
        summary = ""
        for line in f.read_text(encoding="utf-8").splitlines()[:5]:
            line = line.strip().strip("(*=").strip()
            if line:
                summary = line
                break
        out.append({"name": f.name[:-3], "file": f.name, "summary": summary})
    return out


def get_pattern(name: str) -> Optional[str]:
    fname = name if name.endswith(".st") else f"{name}.st"
    if "/" in fname or "\\" in fname or ".." in fname:
        return None
    f = _dir("patterns") / fname
    try:
        return f.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None


# --------------------------------------------------------------- catalogs

def list_catalogs() -> list[dict[str, Any]]:
    out = []
    for f in sorted(_dir("catalog").iterdir(), key=lambda x: x.name):
        if not f.name.endswith(".json") or f.name == "schema.json":
            continue
        data = json.loads(f.read_text(encoding="utf-8"))
        entries = data.get("entries", [])
        out.append({
            "source": data.get("source", f.name[:-5]),
            "device_family": data.get("device_family", ""),
            "entries": len(entries),
            "verified_entries": sum(1 for e in entries if e.get("verified")),
        })
    return out


def load_catalog(source: str) -> Optional[dict[str, Any]]:
    if not re.fullmatch(r"[a-z0-9_\-]+", source):
        return None
    f = _dir("catalog") / f"{source}.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError):
        return None


_UNVERIFIED_NOTE = (
    "UNVERIFIED — check against the vendor manual for the installed FW "
    "revision before using in any customer-facing document.")


def lookup_code(source: str, code: str) -> dict[str, Any]:
    """Catalog lookup. Unknown source/code => explicit refusal, never a
    guess (calibrated trust: fault codes are a low-trust domain)."""
    cat = load_catalog(source)
    if cat is None:
        return {
            "found": False,
            "error": f"unknown catalog source '{source}'",
            "available_sources": [c["source"] for c in list_catalogs()],
            "action": ("No guessing: consult the vendor manual directly, "
                       "or add a catalog with provenance "
                       "(library/catalog/README.md)."),
        }
    code_n = code.strip().upper()
    for e in cat.get("entries", []):
        if e.get("code", "").upper() == code_n:
            entry = dict(e)
            if not entry.get("provenance"):
                entry["provenance"] = cat.get("provenance_default", "")
            entry_status = ("VERIFIED against vendor manual."
                            if entry.get("verified") else _UNVERIFIED_NOTE)
            return {"found": True, "entry": entry,
                    "verification_status": entry_status}
    return {
        "found": False,
        "source": source,
        "code": code_n,
        "refusal": ("Code not in catalog — refusing to guess a cause or "
                    "remedy."),
        "action": ("Look it up in the authoritative source (ACS880: Drive "
                   "Composer Pro event log / the firmware manual matching "
                   "the installed FW revision), then add a catalog entry "
                   "with provenance."),
    }


# ------------------------------------------------------ alarm bundle gen

_CLASSES = {"FAULT": 2, "WARNING": 1, "EVENT": 0}
_MAX_POINTS = 64  # FB_AlarmManager array bound


def gen_alarm_bundle(points: list[dict[str, Any]],
                     pou_name: str = "PRG_Alarms") -> dict[str, Any]:
    """Deterministic codegen: alarm point list -> ST program instantiating
    FB_AlarmPoint per point + FB_AlarmManager, plus catalog stubs for the
    codes. Output is a DRAFT: xIn wiring is left as explicit TODOs."""
    errors: list[str] = []
    if not points:
        errors.append("empty point list")
    if len(points) > _MAX_POINTS:
        errors.append(f"{len(points)} points exceed the FB_AlarmManager "
                      f"bound of {_MAX_POINTS} — split into groups or "
                      f"resize the pattern arrays")
    seen_tags: set[str] = set()
    cleaned: list[dict[str, Any]] = []
    for i, p in enumerate(points, 1):
        tag = re.sub(r"\W", "_", str(p.get("tag", "")).strip())
        if not tag:
            errors.append(f"point {i}: missing tag")
            continue
        if tag in seen_tags:
            errors.append(f"point {i}: duplicate tag '{tag}'")
            continue
        seen_tags.add(tag)
        cls = str(p.get("class", "")).upper()
        if cls not in _CLASSES:
            errors.append(f"point {i} ({tag}): class must be one of "
                          f"{sorted(_CLASSES)}")
            continue
        prio = int(p.get("priority", 2))
        if not 1 <= prio <= 4:
            errors.append(f"point {i} ({tag}): priority must be 1..4")
            continue
        code = str(p.get("code", "0")).strip().upper()
        cleaned.append({
            "tag": tag, "class": cls, "priority": prio, "code": code,
            "text_en": str(p.get("text_en", "")),
            "text_de": str(p.get("text_de", "")),
            "on_delay_ms": int(p.get("on_delay_ms", 0)),
            "off_delay_ms": int(p.get("off_delay_ms", 0)),
            "latching": bool(p.get("latching", False)),
        })
    if errors:
        return {"ok": False, "errors": errors}

    n = len(cleaned)
    decl = [f"PROGRAM {pou_name}", "VAR"]
    for p in cleaned:
        decl.append(f"    fbAlm_{p['tag']} : FB_AlarmPoint;")
    decl += [
        "    fbMgr      : FB_AlarmManager;",
        f"    aActive    : ARRAY[1..{_MAX_POINTS}] OF BOOL;",
        f"    aUnacked   : ARRAY[1..{_MAX_POINTS}] OF BOOL;",
        f"    aClass     : ARRAY[1..{_MAX_POINTS}] OF INT;",
        "    xGroupAck  : BOOL;  (* wire to HMI acknowledge button *)",
        "    xHornReset : BOOL;  (* wire to HMI horn-silence button *)",
        "END_VAR",
        "",
    ]
    body: list[str] = []
    for i, p in enumerate(cleaned, 1):
        code_lit = (f"16#{p['code']}"
                    if re.fullmatch(r"[0-9A-F]{1,8}", p["code"]) else "0")
        body += [
            f"(* -- [{i}] {p['tag']} | {p['class']} P{p['priority']} "
            f"| {p['text_en']} / {p['text_de']} -- *)",
            f"fbAlm_{p['tag']}(",
            f"    xIn := FALSE, (* TODO wire the real condition for "
            f"{p['tag']} *)",
            "    xAck := fbMgr.xAckPulse,",
            "    xReset := fbMgr.xAckPulse,",
            f"    xLatching := {'TRUE' if p['latching'] else 'FALSE'},",
            f"    tOnDelay := T#{p['on_delay_ms']}ms,",
            f"    tOffDelay := T#{p['off_delay_ms']}ms,",
            f"    iPriority := {p['priority']},",
            f"    eClass := {_CLASSES[p['class']]},",
            f"    diCode := {code_lit});",
            f"aActive[{i}]  := fbAlm_{p['tag']}.xActive;",
            f"aUnacked[{i}] := fbAlm_{p['tag']}.xUnacked;",
            f"aClass[{i}]   := {_CLASSES[p['class']]};",
            "",
        ]
    body += [
        f"fbMgr(nPoints := {n}, xGroupAck := xGroupAck, "
        f"xHornReset := xHornReset,",
        "      aActive := aActive, aUnacked := aUnacked, "
        "aClass := aClass);",
        "END_PROGRAM",
    ]
    st_code = "\n".join(decl + body) + "\n"

    stubs = [{
        "code": p["code"], "kind": p["class"].lower()
        if p["class"] != "EVENT" else "event",
        "class": p["class"], "name": p["tag"],
        "text_en": p["text_en"], "text_de": p["text_de"],
        "cause": "", "remedy": "",
        "provenance": "TODO — fill per catalog/README.md before use",
        "verified": False,
    } for p in cleaned if p["code"] not in ("", "0")]

    return {"ok": True, "points": n, "st_code": st_code,
            "catalog_stubs": stubs,
            "note": ("DRAFT: wire every xIn TODO, review ack/reset "
                     "routing, size the manager arrays, and run the "
                     "result through plc_lint_st + Automation Builder.")}
