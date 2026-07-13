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
    assert "cC10(CU :=" in st and "PV := D279" in st
    assert "cC10 : CTU;" in st
    assert "cC10 : TON" not in st

def test_report():
    from plc_mcp.analysis.report import generate_report
    p = _prog()
    rep = generate_report(p, project="pilot test")
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

# ---- Phase 0 regressions (audit 2026-07-13) -------------------------------

def test_anb_block_combine():
    # audit H-1: (X0 OR X1) AND (X2 OR X3) via ANB must stay ONE network
    il = "0 LD X0\n1 OR X1\n2 LD X2\n3 OR X3\n4 ANB\n5 OUT Y10\n"
    p = parse_melsec_il(il)
    assert p.meta["rung_count"] == 1
    st = emit_st(p)
    assert "xDO_Y10 := ((xDI_X0 OR xDI_X1) AND (xDI_X2 OR xDI_X3));" in st

def test_orb_block_combine():
    # (X0 AND X1) OR (X2 AND X3)
    il = "0 LD X0\n1 AND X1\n2 LD X2\n3 AND X3\n4 ORB\n5 OUT Y11\n"
    st = emit_st(parse_melsec_il(il))
    assert "xDO_Y11 := ((xDI_X0 AND xDI_X1) OR (xDI_X2 AND xDI_X3));" in st

def test_mps_branch_conditions():
    # audit H-2: every branch output gets ITS OWN condition
    il = ("0 LD X0\n1 MPS\n2 AND X1\n3 OUT Y10\n4 MRD\n5 AND X2\n"
          "6 OUT Y11\n7 MPP\n8 AND X3\n9 OUT Y12\n")
    st = emit_st(parse_melsec_il(il))
    assert "xDO_Y10 := (xDI_X0 AND xDI_X1);" in st
    assert "xDO_Y11 := (xDI_X0 AND xDI_X2);" in st
    assert "xDO_Y12 := (xDI_X0 AND xDI_X3);" in st

def test_rst_counter_timer_register():
    # audit H-3: RST semantics per device class
    il = ("0 LD X0\n1 OUT C10 D279\n2 LD X1\n3 RST C10\n"
          "4 LD X2\n5 RST T40\n6 LD X3\n7 RST D100\n")
    st = emit_st(parse_melsec_il(il))
    assert "RESET := xRstC10" in st           # CTU reset wired
    assert "xRstC10 := xDI_X1;" in st         # helper assigned at RST rung
    assert "cC10.Q := FALSE" not in st        # old invalid output gone
    assert "TODO [RST-TIMER] RST T40" in st   # timer: honest TODO
    assert "tT40.Q := FALSE" not in st
    assert "IF xDI_X3 THEN D100 := 0; END_IF;" in st  # register := 0

def test_symbol_free_h11_screening_limited():
    # audit H-4: no symbols => H11 must NOT read as clearance
    il = "0 LD X10\n1 AND X11\n2 OUT M100\n"
    f = flag_semantics(parse_melsec_il(il))
    assert f["summary"]["safety_screening_limited"] is True
    assert "H11-COVERAGE-UNKNOWN" in {fl["code"] for fl in f["flags"]}
    assert f["summary"]["verdict"].startswith("H11 SCREENING INCOMPLETE")
    # and the golden sample (with symbols) is NOT limited
    assert flag_semantics(_prog())["summary"][
        "safety_screening_limited"] is False

def test_iomap_nonhex_token_no_crash():
    # audit M-1: symbolic X token must not abort the whole mapping
    il = "0 LD XRUN\n1 AND X1F\n2 OUT Y0\n"
    m = map_io(parse_melsec_il(il))
    unmapped = [r for r in m["mapping"] if r["module"] == "UNMAPPED"]
    assert any(r["legacy"] == "XRUN" for r in unmapped)
    assert any(r["legacy"] == "X1F" and r["direction"] == "DI"
               for r in m["mapping"])

def test_device_parse_bad_number_no_crash():
    # audit M-3: hex letters on a base-10 device class must not raise
    from plc_mcp.ir import Device, DeviceClass
    d = Device.parse("D1A")
    assert d.dclass == DeviceClass.UNKNOWN and d.raw == "D1A"

def test_lint_ignores_if_inside_comments():
    # audit M-2: TODO comments containing 'IF' must not trip the lint
    il = "0 LD M9036\n1 FMOV K0 D100 K8\n"
    p = parse_melsec_il(il)
    st = emit_st(p, flag_semantics(p))
    res = lint_st(st)
    assert res["ok"] is True, res["issues"]

# ---- Engineering library (v0.3) -------------------------------------------

def test_library_patterns_list_and_get():
    from plc_mcp import library as lib
    names = {p["name"] for p in lib.list_patterns()}
    assert {"FB_AlarmPoint", "FB_AlarmManager", "FB_FaultLatch",
            "FB_EventLogger", "FB_CommsWatchdog",
            "FB_DriveDiagACS880"} <= names
    src = lib.get_pattern("FB_AlarmPoint")
    assert src and "IEC 62682" in src and "FUNCTION_BLOCK FB_AlarmPoint" in src
    assert lib.get_pattern("nope") is None
    assert lib.get_pattern("../catalog/acs880") is None  # traversal guard

def test_library_patterns_are_lint_clean():
    from plc_mcp import library as lib
    for p in lib.list_patterns():
        res = lint_st(lib.get_pattern(p["name"]))
        assert res["ok"] is True, (p["name"], res["issues"])

def test_catalog_schema_and_provenance_policy():
    import json as _json, pathlib as _pl
    from plc_mcp import library as lib
    cats = lib.list_catalogs()
    assert any(c["source"] == "acs880" for c in cats)
    cat = lib.load_catalog("acs880")
    for e in cat["entries"]:
        for req in ("code", "kind", "class", "name", "text_en", "text_de",
                    "cause", "remedy", "verified"):
            assert req in e, (e.get("code"), req)
        assert e["class"] in ("FAULT", "WARNING", "EVENT")
        assert e["kind"] in ("fault", "warning", "event")
        # seeded entries must NOT claim verification
        assert e["verified"] is False
    assert cat["provenance_default"]
    # schema file itself is valid JSON
    root = _pl.Path(__file__).resolve().parents[1]
    _json.loads((root / "src/plc_mcp/library/catalog/schema.json"
                 ).read_text())

def test_errorcode_lookup_known_and_refusal():
    from plc_mcp import library as lib
    hit = lib.lookup_code("acs880", "2310")
    assert hit["found"] is True
    assert hit["entry"]["remedy"]
    assert "UNVERIFIED" in hit["verification_status"]
    # unknown code -> refusal, not a guess
    miss = lib.lookup_code("acs880", "FFFF")
    assert miss["found"] is False and "refus" in miss["refusal"].lower()
    assert "Drive Composer" in miss["action"]
    # unknown source -> refusal with available sources
    bad = lib.lookup_code("nonexistent", "1")
    assert bad["found"] is False and "acs880" in bad["available_sources"]

def test_gen_alarm_bundle_lints_clean():
    from plc_mcp import library as lib
    pts = [
        {"tag": "MOTOR_OVERTEMP", "class": "FAULT", "priority": 4,
         "code": "4310", "text_en": "Motor overtemperature",
         "text_de": "Motoruebertemperatur", "on_delay_ms": 500,
         "latching": True},
        {"tag": "FILTER_DP_HIGH", "class": "WARNING", "priority": 2,
         "code": "", "text_en": "Filter dP high",
         "text_de": "Filterdifferenzdruck hoch", "on_delay_ms": 2000},
    ]
    out = lib.gen_alarm_bundle(pts)
    assert out["ok"] is True and out["points"] == 2
    st = out["st_code"]
    assert "fbAlm_MOTOR_OVERTEMP : FB_AlarmPoint;" in st
    assert "diCode := 16#4310" in st
    assert "fbMgr(nPoints := 2" in st
    assert lint_st(st)["ok"] is True, lint_st(st)["issues"]
    # catalog stub only for points WITH a code
    assert len(out["catalog_stubs"]) == 1
    assert out["catalog_stubs"][0]["verified"] is False
    # validation errors surface, not crash
    bad = lib.gen_alarm_bundle([{"tag": "X", "class": "NOPE"}])
    assert bad["ok"] is False and bad["errors"]

def test_parse_source_never_reads_files_by_default():
    from plc_mcp.server import _resolve_source
    # default: literal text even if it looks like an existing path
    assert _resolve_source("/etc/hostname", False) == "/etc/hostname"
    try:
        _resolve_source("../../etc/passwd", True)
        raised = False
    except ValueError:
        raised = True
    assert raised

if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn(); print("PASS", name)
