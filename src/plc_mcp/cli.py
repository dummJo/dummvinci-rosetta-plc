"""rosetta-plc — command line for batch / CI use of the migration pipeline.

    rosetta-plc targets [--json]
    rosetta-plc analyze FILE.il [--symbols CSV] [--dialect melsec_a] [--json]
    rosetta-plc build PROJECT.json [--out DIR] [--force]
    rosetta-plc validate-xml FILE.xml

Exit codes (analyze/build): 0 no BLOCKER, 2 BLOCKERs present,
3 safety boundary hit, 1 error. A CI job can therefore fail a merge while
engineering decisions are still open.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .analysis.semantics import flag_semantics
from .build import (EXIT_BLOCKERS, EXIT_ERROR, EXIT_OK, EXIT_SAFETY,
                    BuildError, build_from_config)
from .parsers.melsec import parse_melsec_il
from .parsers.symbols import load_symbols, merge_symbols, read_text_file
from .targets import TARGETS


def _cmd_targets(args) -> int:
    if args.json:
        print(json.dumps([t.to_dict() for t in TARGETS.values()], indent=2))
        return EXIT_OK
    for t in TARGETS.values():
        state = "supported" if t.supported else "NOT SUPPORTED"
        family = "CODESYS-based" if t.codesys_based else "not CODESYS"
        print(f"{t.id:26} {state:13} {family:13} {t.ide}")
        if not t.supported:
            print(f"{'':26} reason: {t.unsupported_reason}")
    return EXIT_OK


def _cmd_analyze(args) -> int:
    text, _enc = read_text_file(args.file)
    prog = parse_melsec_il(text, name=Path(args.file).stem,
                           dialect=args.dialect)
    if args.symbols:
        syms, _ = load_symbols(Path(args.symbols))
        merge_symbols(prog, syms, source=args.symbols)
    flags = flag_semantics(prog)
    s = flags["summary"]
    if args.json:
        print(json.dumps({"parse": prog.meta, "flags": flags}, indent=2,
                         ensure_ascii=False))
    else:
        print(f"{args.file}: {prog.meta['instruction_count']} instructions, "
              f"{prog.meta['rung_count']} networks, "
              f"{prog.meta['unknown_mnemonics']} unknown mnemonics")
        print(f"BLOCKER {s['BLOCKER']}  MAJOR {s['MAJOR']}  "
              f"MINOR {s['MINOR']}")
        print(f"Verdict: {s['verdict']}")
    if s["safety_boundary"]:
        return EXIT_SAFETY
    return EXIT_BLOCKERS if s["BLOCKER"] else EXIT_OK


def _cmd_build(args) -> int:
    res = build_from_config(args.config, out_dir=args.out, force=args.force)
    m = res.manifest
    print(f"rosetta-plc {__version__}: built '{m['project']}' for "
          f"{m['target']} -> {res.output_dir}")
    for pid, s in m["programs"].items():
        print(f"  {pid}: {s['networks']} networks, {len(s['pous'])} POU(s), "
              f"BLOCKER {s['BLOCKER']} MAJOR {s['MAJOR']} MINOR {s['MINOR']}"
              f", unknown {s['unknown_mnemonics']}")
        print(f"    verdict: {s['verdict']}")
    print(f"  task POUs: {', '.join(m['main_pous'])}")
    print("  next: follow IMPORT.md; the IDE is the compile authority")
    return res.exit_code


def _cmd_validate(args) -> int:
    from .emitters.plcopen import validate_plcopen
    xml = Path(args.file).read_text(encoding="utf-8")
    ok, errors = validate_plcopen(xml)
    if ok:
        print(f"{args.file}: XSD-valid against PLCopen TC6 v2.01 "
              "(schema validity is not proof of IDE import)")
        return EXIT_OK
    print(f"{args.file}: INVALID")
    for e in errors[:50]:
        print("  ", e)
    return EXIT_ERROR


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="rosetta-plc", description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("targets", help="list target platforms")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_targets)
    p = sub.add_parser("analyze", help="parse + hazard register for one file")
    p.add_argument("file")
    p.add_argument("--symbols")
    p.add_argument("--dialect", default="melsec_a")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_analyze)
    p = sub.add_parser("build", help="build a project bundle from JSON config")
    p.add_argument("config")
    p.add_argument("--out", help="override output_dir from the config")
    p.add_argument("--force", action="store_true",
                   help="allow writing into a non-empty foreign directory")
    p.set_defaults(fn=_cmd_build)
    p = sub.add_parser("validate-xml", help="XSD-check a PLCopen file")
    p.add_argument("file")
    p.set_defaults(fn=_cmd_validate)
    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except (BuildError, OSError, ValueError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
