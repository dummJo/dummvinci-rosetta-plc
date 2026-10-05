"""Scale: a synthetic program the size of a large real plant export must run
through parse -> flags -> segmented project emit in bounded, roughly linear
time, with every rung accounted for."""
import time

from _util import run_module

from plc_mcp.parsers.melsec import parse_melsec_il
from plc_mcp.analysis.semantics import flag_semantics
from plc_mcp.emitters.project import ProjectOptions, emit_project

_BLOCK = """{s0}  LD    X{x:X}
{s1}  OR    M{m}
{s2}  LD    M{m1}
{s3}  ANI   X{x1:X}
{s4}  ORB
{s5}  AND   M{m}
{s6}  OUT   M{m2}
{s7}  LD    M{m2}
{s8}  OUT   T{t} K{k}
{s9}  LD    T{t}
{s10} MOV   K{k} D{d}
{s11} OUT   Y{y:X}
"""


def _synthetic(n_blocks: int) -> str:
    lines = []
    step = 0
    for i in range(n_blocks):
        steps = {f"s{j}": step + j for j in range(12)}
        lines.append(_BLOCK.format(x=i % 2048, x1=(i + 7) % 2048,
                                   m=i % 7000, m1=(i + 1) % 7000,
                                   m2=7000 + i % 1000, t=i % 2000,
                                   k=10 + i % 90, d=i % 8000,
                                   y=i % 2048, **steps))
        step += 12
    lines.append(f"{step} END\n")
    return "".join(lines)


def _run(n_blocks: int):
    src = _synthetic(n_blocks)
    t0 = time.perf_counter()
    prog = parse_melsec_il(src, name="BIG")
    flags = flag_semantics(prog)
    pr = emit_project([(prog, flags)],
                      ProjectOptions(target="abb_ac500_v3",
                                     max_networks_per_pou=500))
    xml = pr.plcopen_xml()
    files = pr.st_files()
    return time.perf_counter() - t0, prog, pr, xml, files


def test_large_program_is_bounded_and_complete():
    dt, prog, pr, xml, files = _run(4000)          # 48k instructions
    assert prog.meta["instruction_count"] == 4000 * 12 + 1
    assert prog.meta["unknown_mnemonics"] == 0
    segs = [p for p in pr.pous if p.kind == "segment"]
    assert len(segs) >= 16
    assert sum(len(p.steps) for p in segs) == len(prog.rungs)
    assert xml.count("<pou ") == len(pr.pous)
    print(f"    48k instructions: {dt:.2f}s, {len(segs)} segments, "
          f"{len(xml) / 1e6:.1f} MB XML")
    assert dt < 60, dt


def test_runtime_grows_roughly_linearly():
    small, *_ = _run(500)
    large, *_ = _run(4000)
    # 8x the input may cost at most ~20x the time (generous: CI jitter)
    assert large < max(small, 0.05) * 20, (small, large)


if __name__ == "__main__":
    run_module(globals())
