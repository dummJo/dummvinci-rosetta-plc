import json, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from plc_mcp.parsers.melsec import parse_melsec_il
from plc_mcp.analysis.xref import build_xref
from plc_mcp.analysis.semantics import flag_semantics
from plc_mcp.analysis.iomap import map_io
from plc_mcp.emitters.st import emit_st
from plc_mcp.emitters.plcopen import emit_plcopen_xml, emit_scl, lint_st

SRC = (pathlib.Path(__file__).resolve().parents[1] /
       "samples/centrifugal_excerpt.il").read_text()

def _prog():
    return parse_melsec_il(SRC, name="TEST")

def test_parse_counts():
    p = _prog()
    assert p.meta["instruction_count"] > 30
    assert p.symbols["X8B"].startswith("LSW-11")
    assert p.meta["unknown_mnemonics"] == 0

def test_xref_detects_reads_writes():
    x = build_xref(_prog())
    assert "L1000" in x["devices"]
    assert x["devices"]["L1000"]["write_count"] == 1
    assert 3 in x["devices"]["L1000"]["reads"]
    assert x["devices"]["R5100"]["write_count"] == 1

def test_flags():
    f = flag_semantics(_prog())
    codes = {fl["code"] for fl in f["flags"]}
    assert "H1-ALWAYS-OFF-SERIES" in codes
    assert "H3-ACCUMULATOR" in codes
    assert "H4-MC-ZONE" in codes
    assert "H6-MODULE-BUFFER" in codes
    assert f["summary"]["BLOCKER"] >= 2

def test_iomap():
    m = map_io(_prog())
    legacy = {r["legacy"]: r for r in m["mapping"]}
    assert legacy["Y0C1"]["direction"] == "DO_RELAY"
    assert legacy["X87"]["direction"] == "DI"

def test_emit_st_and_lint():
    p = _prog()
    st = emit_st(p, flag_semantics(p))
    assert "tT99(IN :=" in st and "PT := T#100ms" in st
    assert "tT40(IN :=" in st and "DINT_TO_TIME" in st
    assert "(R5119 = 1374)" in st
    assert "TODO [H1-ALWAYS-OFF-SERIES" in st
    assert "R5100 := R5100 + 1" in st
    res = lint_st(st)
    assert not [i for i in res["issues"] if "mismatch" in i], res["issues"]

def test_counter_ctu():
    p = _prog()
    st = emit_st(p, flag_semantics(p))
    # CODESYS Standard CTU.PV is WORD -> explicit conversion of the INT
    # register (generic IEC target keeps "PV := D279", see
    # test_stack_semantics.test_generic_target_ctu_preset_is_int)
    assert "cC10(CU :=" in st and "PV := INT_TO_WORD(D279)" in st
    assert "cC10 : CTU;" in st
    assert "cC10 : TON" not in st

def test_report():
    from plc_mcp.analysis.report import generate_report
    p = _prog()
    rep = generate_report(p, project="JMR test")
    assert "hazard register" in rep.lower()
    assert "H1-ALWAYS-OFF-SERIES" in rep
    assert "Sign-off" in rep

def test_plcopen_and_scl():
    p = _prog()
    st = emit_st(p, flag_semantics(p))
    xml = emit_plcopen_xml(st)
    assert xml.startswith("<?xml") and "plcopen" in xml
    scl = emit_scl(st)
    assert "FUNCTION_BLOCK" in scl

def test_no_safety_false_positive_on_process_sample():
    # The centrifugal golden sample has NO safety-instrumented function.
    # H11 must not fire on it (brake/limit-switch/reset are process terms).
    f = flag_semantics(_prog())
    assert f["summary"]["safety_boundary"] is False
    assert not [fl for fl in f["flags"]
                if fl["code"] == "H11-SAFETY-BOUNDARY"]

def _safety_prog():
    src = (pathlib.Path(__file__).resolve().parents[1] /
           "samples/marine_crane_safety_excerpt.il").read_text()
    return parse_melsec_il(src, name="CRANE")

def test_safety_boundary_detected():
    f = flag_semantics(_safety_prog())
    codes = {fl["code"] for fl in f["flags"]}
    assert "H11-SAFETY-BOUNDARY" in codes
    assert f["summary"]["safety_boundary"] is True
    # e-stop, overload/load-moment, anti-two-block, overhoist, watchdog
    cats = set(f["summary"]["safety_categories"])
    assert "emergency-stop" in cats
    assert "crane-load-safety" in cats
    assert "watchdog" in cats
    # every H11 is a BLOCKER, and the verdict names the boundary
    assert all(fl["severity"] == "BLOCKER" for fl in f["flags"]
               if fl["code"] == "H11-SAFETY-BOUNDARY")
    assert "SAFETY BOUNDARY HIT" in f["summary"]["verdict"]

def test_safety_banner_in_emitted_st():
    p = _safety_prog()
    st = emit_st(p, flag_semantics(p))
    assert "SAFETY BOUNDARY HIT" in st
    assert "AC500-S" in st
    assert "TODO [H11-SAFETY-BOUNDARY" in st

if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn(); print("PASS", name)
