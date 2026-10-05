"""External device-comment import.

Real projects keep device comments outside the instruction list: GX Developer
/ GX Works export them separately as CSV (often Shift-JIS on Japanese-origin
machines). This loader is deliberately tolerant about layout and strict about
content:

  * delimiter per line: tab, comma or semicolon (quotes honoured), else the
    first whitespace run;
  * first field must look like a MELSEC device (X8B, M500, D279, ZR10...);
    title rows, headers and junk are counted as skipped, never guessed at;
  * encoding: UTF-8 (with or without BOM), then cp932 (Shift-JIS), then
    latin-1 as a lossless last resort — the one used is reported.

Merging never silently overwrites: when a device already has a comment
(e.g. from an inline [SYMBOLS] section) and the external text differs, the
existing comment is kept and the disagreement is recorded in
prog.meta["symbol_conflicts"] for the engineer.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Union

from ..ir import Program

_DEVICE_RX = re.compile(r"^[A-Z]{1,2}[0-9A-F]+$")


def decode_bytes(data: bytes) -> tuple[str, str]:
    for enc, label in (("utf-8-sig", "utf-8"), ("cp932", "cp932")):
        try:
            return data.decode(enc), label
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1"), "latin-1"


def read_text_file(path: Union[str, Path]) -> tuple[str, str]:
    return decode_bytes(Path(path).read_bytes())


def _fields(line: str) -> list[str]:
    for delim in ("\t", ",", ";"):
        if delim in line:
            row = next(csv.reader([line], delimiter=delim), [])
            return [f.strip() for f in row]
    return line.strip().split(None, 1)


def load_symbols(source: Union[str, Path]) -> tuple[dict[str, str], dict]:
    """Load device comments from a file path or from raw text."""
    if isinstance(source, Path) or (
            isinstance(source, str) and "\n" not in source
            and len(source) < 1024 and Path(source).is_file()):
        text, encoding = read_text_file(source)
    else:
        text, encoding = str(source), "text"
    symbols: dict[str, str] = {}
    skipped, duplicates = 0, []
    for line in text.splitlines():
        if not line.strip():
            continue
        f = _fields(line)
        dev = f[0].upper() if f else ""
        comment = f[1] if len(f) > 1 else ""
        if not _DEVICE_RX.match(dev) or not comment:
            skipped += 1
            continue
        if dev in symbols and symbols[dev] != comment:
            duplicates.append(dev)
        symbols[dev] = comment
    return symbols, {"loaded": len(symbols), "skipped_rows": skipped,
                     "encoding": encoding, "duplicates_in_file": duplicates}


def merge_symbols(prog: Program, symbols: dict[str, str],
                  source: str = "external") -> list[dict]:
    conflicts = prog.meta.setdefault("symbol_conflicts", [])
    new = []
    for dev, comment in symbols.items():
        cur = prog.symbols.get(dev)
        if cur is None:
            prog.symbols[dev] = comment
        elif cur.strip() != comment.strip():
            c = {"device": dev, "kept": cur, "external": comment,
                 "source": source}
            conflicts.append(c)
            new.append(c)
    prog.meta.setdefault("symbol_sources", []).append(source)
    return new
