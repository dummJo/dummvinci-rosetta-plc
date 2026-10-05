"""Project build: JSON project config -> deterministic bundle on disk.

Big projects cannot round-trip megabytes of ST through a chat context, and
they need reproducible, reviewable output. A build writes:

    plcopen/<project>.xml             structured PLCopen project (import this)
    st/<POU or GVL>.st                one file per object, for review/diff
    analysis/<PID>/flags.json         [VERIFY] register (H1..H12)
    analysis/<PID>/xref.json          device cross-reference
    analysis/<PID>/iomap.json|.csv    I/O mapping worksheet (GVL paths)
    analysis/<PID>/report.md          engineering-review report
    libraries.json                    libraries the code needs + project pins
    IMPORT.md                         target-specific import checklist
    SAFETY_BOUNDARY.md                only when H11 fired
    MANIFEST.json                     inputs/outputs with sha256, exit code

Exit codes: 0 no BLOCKER, 2 BLOCKERs present (draft emitted), 3 safety
boundary hit (draft emitted for the standard part only), 1 error.

The writer refuses a non-empty directory that is not a previous build (no
MANIFEST.json from this tool) unless forced, and on rebuild removes only the
files its own previous manifest lists.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import __version__
from .analysis.iomap import GENERIC_PROFILE, map_io
from .analysis.report import generate_report
from .analysis.semantics import flag_semantics
from .analysis.xref import build_xref
from .emitters.plcopen import FIXED_TIMESTAMP
from .emitters.project import ProjectModel, ProjectOptions, emit_project
from .ir import Program
from .parsers.melsec import parse_melsec_il
from .parsers.symbols import load_symbols, merge_symbols, read_text_file
from .targets import get_target

EXIT_OK, EXIT_ERROR, EXIT_BLOCKERS, EXIT_SAFETY = 0, 1, 2, 3
MANIFEST = "MANIFEST.json"
SIGNATURE = "dummvinci-rosetta-plc build"

CONFIG_KEYS = {"project", "target", "naming", "max_networks_per_pou",
               "timer_base_ms", "output_dir", "programs", "io_profile",
               "timestamp", "task_interval_ms", "libraries"}
PROGRAM_KEYS = {"id", "source", "dialect", "symbols", "io_profile"}
LIBRARY_KEYS = {"name", "version", "purpose", "required", "vendor"}


class BuildError(Exception):
    pass


@dataclass
class BuildResult:
    exit_code: int
    output_dir: str
    manifest: dict


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _resolve_timestamp(ts: Optional[str]) -> str:
    if ts:
        return ts
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch and epoch.isdigit():
        return datetime.fromtimestamp(int(epoch), timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S")
    return FIXED_TIMESTAMP


def _check_keys(obj: dict, allowed: set, where: str) -> None:
    unknown = sorted(set(obj) - allowed)
    if unknown:
        raise BuildError(f"unknown key(s) {unknown} in {where}; allowed: "
                         f"{sorted(allowed)}")


def _project_libraries(libs) -> list[dict]:
    out = []
    for i, lib in enumerate(libs or []):
        if not isinstance(lib, dict) or not lib.get("name"):
            raise BuildError(f"libraries[{i}] needs at least a 'name'")
        _check_keys(lib, LIBRARY_KEYS, f"libraries[{i}]")
        out.append({"name": lib["name"], "version": lib.get("version", ""),
                    "vendor": lib.get("vendor", ""),
                    "purpose": lib.get("purpose", ""),
                    "required": bool(lib.get("required", True)),
                    "source": "project config"})
    return out


# ----------------------------------------------------------------- inputs

def build_from_config(config_path, out_dir=None, force: bool = False
                      ) -> BuildResult:
    cfg_path = Path(config_path).resolve()
    base = cfg_path.parent
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise BuildError(f"cannot read project config {cfg_path}: {e}") from e
    _check_keys(cfg, CONFIG_KEYS, "project config")
    if not cfg.get("programs"):
        raise BuildError("project config needs a non-empty 'programs' list")
    programs: list[Program] = []
    inputs: list[dict] = []
    for i, p in enumerate(cfg["programs"]):
        _check_keys(p, PROGRAM_KEYS, f"programs[{i}]")
        if not p.get("id") or not p.get("source"):
            raise BuildError(f"programs[{i}] needs 'id' and 'source'")
        src = (base / p["source"]).resolve()
        try:
            data = src.read_bytes()
        except OSError as e:
            raise BuildError(f"programs[{i}] source: {e}") from e
        text, enc = read_text_file(src)
        prog = parse_melsec_il(text, name=p["id"],
                               dialect=p.get("dialect", "melsec_a"))
        inputs.append({"role": "source", "program": p["id"],
                       "path": p["source"], "encoding": enc,
                       "sha256": _sha(data)})
        if p.get("symbols"):
            sym_path = (base / p["symbols"]).resolve()
            try:
                syms, stats = load_symbols(sym_path)
            except OSError as e:
                raise BuildError(f"programs[{i}] symbols: {e}") from e
            merge_symbols(prog, syms, source=p["symbols"])
            inputs.append({"role": "symbols", "program": p["id"],
                           "path": p["symbols"],
                           "encoding": stats["encoding"],
                           "sha256": _sha(sym_path.read_bytes()),
                           "loaded": stats["loaded"],
                           "skipped_rows": stats["skipped_rows"]})
        if p.get("io_profile") is not None:
            prog.meta["io_profile"] = p["io_profile"]
        programs.append(prog)
    out = Path(out_dir) if out_dir else (base / cfg.get(
        "output_dir", f"build/{cfg.get('project', 'project')}"))
    return build_programs(
        programs, project=cfg.get("project", "ptts_migration"),
        target=cfg.get("target"), naming=cfg.get("naming", "legacy"),
        max_networks_per_pou=int(cfg.get("max_networks_per_pou", 0)),
        timer_base_ms=int(cfg.get("timer_base_ms", 100)),
        output_dir=out, force=force, io_profile=cfg.get("io_profile"),
        timestamp=cfg.get("timestamp"),
        task_interval_ms=int(cfg.get("task_interval_ms", 10)),
        libraries=cfg.get("libraries"), inputs=inputs)


# ----------------------------------------------------------------- outputs

def _iomap_csv(io_map: dict, model: ProjectModel, pid: str) -> str:
    gvl_io = next((g for g in model.gvls if g.program == pid
                   and g.role == "io"), None)
    names = {v.legacy: v.name for v in (gvl_io.vars if gvl_io else [])}
    qualified = model.target.qualified_access and gvl_io is not None
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["legacy", "symbol", "direction", "module", "channel",
                "iec_var", "note"])
    for m in io_map["mapping"]:
        var = names.get(m["legacy"], m["iec_var"])
        if qualified:
            var = f"{gvl_io.name}.{var}"
        w.writerow([m["legacy"], m["symbol"], m["direction"], m["module"],
                    m["channel"], var, m["note"]])
    return buf.getvalue()


def _import_md(model: ProjectModel, libraries: list[dict],
               shared_io: bool = False) -> str:
    t = model.target
    lines = [f"# Import checklist — {model.options.project_name}", "",
             f"Target profile: **{t.id}** — {t.vendor}, {t.platform}",
             f"IDE: {t.ide}", "",
             "> Draft output. The IDE is the compile authority; cold I/O "
             "checkout and FAT remain mandatory. Not validated by a real "
             "IDE import yet (feature_list: codesys-ide-import-validation).",
             "", "## Steps", ""]
    lines += [f"{i}. {s}" for i, s in enumerate(t.import_steps, 1)]
    if shared_io:
        lines += ["", "> ⚠️ **I/O channel overlap.** Each legacy program's "
                  "iomap worksheet starts at the first channel of its I/O "
                  "profile. When several programs share one controller, "
                  "their proposals overlap. Give each program its own "
                  "`io_profile` in the project config (its own modules) "
                  "before using the worksheets."]
    lines += ["", "## POUs to assign to a cyclic task", ""]
    lines += [f"- `{n}`" for n in model.main_pous]
    lines += ["", "## Libraries", "",
              "| Library | Version | Required | Source | Purpose |",
              "|---|---|---|---|---|"]
    for lib in libraries:
        ref = f" ([ref]({lib['reference']}))" if lib.get("reference") else ""
        lines.append(f"| {lib['name']} | {lib.get('version') or '—'} | "
                     f"{'yes' if lib['required'] else 'optional'} | "
                     f"{lib.get('source', 'target profile')} | "
                     f"{lib['purpose']}{ref} |")
    if t.notes:
        lines += ["", "## Platform notes", ""] + [f"- {n}" for n in t.notes]
    lines += ["", "## Open engineering decisions", "",
              "| Program | BLOCKER | MAJOR | MINOR | Verdict |",
              "|---|---|---|---|---|"]
    for pid, s in model.summaries.items():
        verdict = s["verdict"].replace("|", "\\|")
        lines.append(f"| {pid} | {s['BLOCKER']} | {s['MAJOR']} | "
                     f"{s['MINOR']} | {verdict} |")
    lines.append("")
    return "\n".join(lines)


def _safety_md(model: ProjectModel, flags_by_pid: dict) -> str:
    lines = ["# SAFETY BOUNDARY HIT", "",
             "Safety-instrumented functions were detected (H11). They are "
             "OUT OF SCOPE for this tool and must be engineered in a "
             "certified functional-safety toolchain (e.g. ABB AC500-S + "
             "SM560-S / PROFIsafe) under the IEC 61508/61511 lifecycle. The "
             "emitted code is for the standard (non-safety) part only and is "
             "a draft. See SAFETY.md in the tool repository.", "",
             "| Program | Step | Detail |", "|---|---|---|"]
    for pid, flags in flags_by_pid.items():
        for f in flags["flags"]:
            if f["code"] == "H11-SAFETY-BOUNDARY":
                lines.append(f"| {pid} | {f['step']} | "
                             f"{f['detail'].replace('|', '/')} |")
    lines.append("")
    return "\n".join(lines)


def build_programs(programs: list[Program], *, project: str = "ptts_migration",
                   target: Optional[str] = None, naming: str = "legacy",
                   max_networks_per_pou: int = 0, timer_base_ms: int = 100,
                   output_dir=None, force: bool = False,
                   io_profile: Optional[dict] = None,
                   timestamp: Optional[str] = None,
                   task_interval_ms: int = 10,
                   libraries: Optional[list] = None,
                   inputs: Optional[list] = None) -> BuildResult:
    if output_dir is None:
        raise BuildError("output_dir is required")
    try:
        tgt = get_target(target, for_emit=True)
    except ValueError as e:
        raise BuildError(str(e)) from e
    ts = _resolve_timestamp(timestamp)
    items = [(p, flag_semantics(p)) for p in programs]
    try:
        model = emit_project(items, ProjectOptions(
            target=tgt.id, naming=naming,
            max_networks_per_pou=max_networks_per_pou,
            timer_base_ms=timer_base_ms, project_name=project, timestamp=ts,
            task_interval_ms=task_interval_ms))
    except ValueError as e:
        raise BuildError(str(e)) from e
    profile = io_profile if io_profile is not None else (
        None if tgt.id.startswith("abb_") else GENERIC_PROFILE)

    files: dict[str, str] = {f"plcopen/{project}.xml": model.plcopen_xml()}
    for name, text in model.st_files().items():
        files[f"st/{name}"] = text
    flags_by_pid = {}
    programs_meta = {}
    for (prog, flags), pid in zip(items, model.programs):
        flags_by_pid[pid] = flags
        prog_profile = prog.meta.get("io_profile", profile)
        io_map = map_io(prog, prog_profile)
        a = f"analysis/{pid}"
        files[f"{a}/flags.json"] = json.dumps(flags, indent=2,
                                              ensure_ascii=False) + "\n"
        files[f"{a}/xref.json"] = json.dumps(build_xref(prog), indent=2,
                                             ensure_ascii=False) + "\n"
        files[f"{a}/iomap.json"] = json.dumps(io_map, indent=2,
                                              ensure_ascii=False) + "\n"
        files[f"{a}/iomap.csv"] = _iomap_csv(io_map, model, pid)
        files[f"{a}/report.md"] = generate_report(
            prog, project=f"{project} / {pid}", target=tgt.id,
            io_profile=prog_profile,
            report_date=(ts[:10] if timestamp or os.environ.get(
                "SOURCE_DATE_EPOCH") else
                "not stamped (reproducible build; set 'timestamp' in the "
                "project config)"))
        programs_meta[pid] = {
            **model.summaries[pid],
            "instructions": prog.meta.get("instruction_count", 0),
            "unknown_mnemonics": prog.meta.get("unknown_mnemonics", 0),
            "unparsed_lines": len(prog.meta.get("unparsed_lines", [])),
            "symbols_loaded": len(prog.symbols),
            "symbol_conflicts": len(prog.meta.get("symbol_conflicts", [])),
        }
    proj_libs = _project_libraries(libraries)
    pinned = {lib["name"]: lib for lib in proj_libs}
    all_libs = []
    for lib in model.libraries["libraries"]:
        entry = {**lib, "version": "", "source": "target profile"}
        if lib["name"] in pinned:
            entry.update({k: v for k, v in pinned.pop(lib["name"]).items()
                          if v not in ("", None)})
        all_libs.append(entry)
    all_libs += list(pinned.values())
    files["libraries.json"] = json.dumps(
        {**model.libraries, "libraries": all_libs}, indent=2,
        ensure_ascii=False) + "\n"
    shared_io = len(programs) > 1 and not all(
        "io_profile" in p.meta for p in programs)
    files["IMPORT.md"] = _import_md(model, all_libs, shared_io)
    if model.safety_boundary:
        files["SAFETY_BOUNDARY.md"] = _safety_md(model, flags_by_pid)

    if model.safety_boundary:
        code = EXIT_SAFETY
    elif any(s["BLOCKER"] for s in model.summaries.values()):
        code = EXIT_BLOCKERS
    else:
        code = EXIT_OK
    manifest = {
        "generator": SIGNATURE, "tool_version": __version__,
        "project": project, "target": tgt.id, "timestamp": ts,
        "options": {"naming": naming,
                    "max_networks_per_pou": max_networks_per_pou,
                    "timer_base_ms": timer_base_ms,
                    "task_interval_ms": task_interval_ms,
                    "io_profile": (profile or {}).get(
                        "profile_name", "AC500 S500 default")},
        "inputs": inputs or [],
        "programs": programs_meta,
        "main_pous": model.main_pous,
        "safety_boundary": model.safety_boundary,
        "exit_code": code,
    }
    out = Path(output_dir).resolve()
    manifest["outputs"] = _write_bundle(out, files, force)
    (out / MANIFEST).write_bytes(
        (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        .encode("utf-8"))
    return BuildResult(code, str(out), manifest)


def _write_bundle(out: Path, files: dict[str, str], force: bool
                  ) -> dict[str, str]:
    prev_outputs: dict = {}
    if out.exists():
        if not out.is_dir():
            raise BuildError(f"{out} exists and is not a directory")
        ours = False
        man = out / MANIFEST
        if man.is_file():
            try:
                prev = json.loads(man.read_text(encoding="utf-8"))
                ours = prev.get("generator") == SIGNATURE
                prev_outputs = prev.get("outputs", {}) if ours else {}
            except (OSError, json.JSONDecodeError):
                ours = False
        if not ours and any(out.iterdir()) and not force:
            raise BuildError(
                f"{out} is not empty and is not a rosetta-plc build (no "
                f"{MANIFEST} from this tool); refusing to write into it. "
                f"Choose another output_dir or pass force.")
    out.mkdir(parents=True, exist_ok=True)
    for rel in prev_outputs:                 # our own stale files only
        if rel in files:
            continue
        p = (out / rel).resolve()
        if out in p.parents and p.is_file():
            p.unlink()
    for d in sorted({(out / rel).parent for rel in prev_outputs},
                    key=lambda x: len(x.parts), reverse=True):
        if d != out and out in d.parents and d.is_dir() \
                and not any(d.iterdir()):
            d.rmdir()
    hashes = {}
    for rel in sorted(files):
        data = files[rel].encode("utf-8")
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        hashes[rel] = _sha(data)
    return hashes
