"""Stack-machine and coil semantics: the constructs a naive converter gets
silently wrong. Each assertion pins the exact ST a faithful translation must
produce for samples/stack_blocks.il."""
from _util import run_module, sample, skip  # noqa: F401  (sets sys.path)

from plc_mcp.parsers.melsec import parse_melsec_il
from plc_mcp.analysis.semantics import flag_semantics
from plc_mcp.emitters.st import emit_st
from plc_mcp.emitters.plcopen import lint_st, comment_depth


def _blocks():
    return parse_melsec_il(sample("stack_blocks.il"), name="BLOCKS")


def _st(target="abb_ac500_v3"):
    p = _blocks()
    return emit_st(p, flag_semantics(p), target=target)


def _rung_steps(prog, start):
    for r in prog.rungs:
        if r.start_step == start:
            return [i.step for i in r.instructions]
    raise AssertionError(f"no rung starts at step {start}")


def test_anb_block_load_does_not_split_rung():
    # LD X2 at step 2 opens a parallel block; it is NOT a new network.
    assert _rung_steps(_blocks(), 0) == [0, 1, 2, 3, 4, 5]
    assert "xDO_Y20 := ((xDI_X0 OR xDI_X1) AND (xDI_X2 OR xDI_X3));" in _st()


def test_orb_block():
    assert _rung_steps(_blocks(), 10) == [10, 11, 12, 13, 14, 15]
    assert "M10 := ((xDI_X4 AND xDI_X5) OR (xDI_X6 AND xDI_X7));" in _st()


def test_mps_mrd_mpp_each_output_keeps_its_own_condition():
    st = _st()
    assert "xDO_Y21 := (xDI_X10 AND xDI_X11);" in st
    assert "xDO_Y22 := (xDI_X10 AND xDI_X12);" in st
    assert "xDO_Y23 := (xDI_X10 AND NOT xDI_X13);" in st


def test_outputs_in_series_use_condition_at_their_position():
    st = _st()
    assert "M21 := M20;" in st
    assert "M23 := (M20 AND M22);" in st


def test_edge_contacts_use_per_instruction_trig_instances():
    st = _st()
    assert "(*EDGE" not in st
    assert "rtS40(CLK := xDI_X14);" in st
    assert "M30 := rtS40.Q;" in st
    assert "ftS43(CLK := xDI_X15);" in st
    assert "M32 := (M31 AND ftS43.Q);" in st
    assert "rtS40 : R_TRIG;" in st
    assert "ftS43 : F_TRIG;" in st
    # the trig call must come before the statement that reads it
    assert st.index("rtS40(CLK :=") < st.index("M30 := rtS40.Q;")


def test_plf_declares_falling_edge_trigger():
    st = _st()
    assert "trigM40(CLK := xDI_X16);" in st
    assert "M40 := trigM40.Q;" in st
    assert "trigM40 : F_TRIG;" in st
    assert "trigM40 : R_TRIG;" not in st


def test_timer_in_word_context_reads_elapsed_value_not_q():
    st = _st()
    assert "tT5(IN := M50, PT := T#5000ms);" in st
    assert "(DINT_TO_INT(TIME_TO_DINT(tT5.ET) / 100) > 20)" in st
    assert "D10 := DINT_TO_INT(TIME_TO_DINT(tT5.ET) / 100);" in st
    assert "tT5.Q > 20" not in st


def test_counter_reset_drives_reset_input_and_value_reads_cv():
    st = _st()
    assert "cC3(CU := xDI_X17, RESET := FALSE, PV := 5);" in st
    assert "IF xDI_X18 THEN cC3(RESET := TRUE); END_IF;" in st
    assert "D11 := WORD_TO_INT(cC3.CV);" in st
    assert ".Q := FALSE" not in st   # never assign an FB output


def test_rst_on_timer_and_word_device():
    st = _st()
    assert "IF xDI_X19 THEN tT5(IN := FALSE); END_IF;" in st
    assert "IF xDI_X1A THEN D12 := 0; END_IF;" in st


def test_first_scan_flag_is_initialised_and_cleared():
    st = _st()
    assert "bInitFirstScan : BOOL := TRUE;" in st
    assert "IF bInitFirstScan THEN D13 := 0; END_IF;" in st
    # cleared after the last network, before END_PROGRAM
    assert st.rindex("bInitFirstScan := FALSE;") > st.index("D13 := 0")
    # H2 is still flagged: STOP->RUN without reset differs on CODESYS
    f = flag_semantics(_blocks())
    assert any(x["code"] == "H2-SPECIAL-RELAY" and x["step"] == 90
               for x in f["flags"])


def test_h12_wide_result_flagged_not_hidden():
    f = flag_semantics(_blocks())
    h12 = [x for x in f["flags"] if x["code"] == "H12-WIDE-RESULT"]
    assert any(x["step"] == 93 and "D23" in x["detail"] for x in h12), h12
    assert all(x["severity"] == "MAJOR" for x in h12)
    st = _st()
    assert "IF M54 THEN D22 := D20 * D21; END_IF;" in st
    assert "TODO [H12-WIDE-RESULT" in st
    # 32-bit increment in the golden sample is flagged too
    gold = parse_melsec_il(sample("centrifugal_excerpt.il"))
    assert any(x["code"] == "H12-WIDE-RESULT" and x["step"] == 2452
               for x in flag_semantics(gold)["flags"])


def test_generic_target_ctu_preset_is_int():
    gold = parse_melsec_il(sample("centrifugal_excerpt.il"))
    st = emit_st(gold, flag_semantics(gold), target="generic_plcopen")
    assert "PV := D279" in st
    st_cds = emit_st(gold, flag_semantics(gold), target="abb_ac500_v3")
    assert "PV := INT_TO_WORD(D279)" in st_cds


def test_emitted_code_has_no_nested_comments():
    for name in ("stack_blocks.il", "centrifugal_excerpt.il",
                 "marine_crane_safety_excerpt.il"):
        p = parse_melsec_il(sample(name))
        st = emit_st(p, flag_semantics(p))
        assert comment_depth(st) <= 1, name
        assert not [w for w in lint_st(st)["warnings"] if "nested" in w]


def test_lint_ignores_keywords_inside_comments():
    code = ("PROGRAM P\n(* wrap following networks in IF zone THEN *)\n"
            "x := TRUE;\nEND_PROGRAM\n")
    res = lint_st(code)
    assert not [i for i in res["issues"] if "mismatch" in i], res


def test_lint_flags_nested_comment_as_portability_warning():
    res = lint_st("PROGRAM P\n(* outer (* inner *) *)\nEND_PROGRAM\n")
    assert any("nested" in w for w in res["warnings"])


def test_blark_parses_single_pou_output():
    try:
        from blark import parse_source_code
    except ImportError:
        skip("blark not installed (pip install -e .[dev])")
    for name in ("stack_blocks.il", "centrifugal_excerpt.il",
                 "marine_crane_safety_excerpt.il"):
        for target in ("abb_ac500_v3", "beckhoff_twincat3", "generic_plcopen"):
            p = parse_melsec_il(sample(name))
            res = parse_source_code(emit_st(p, flag_semantics(p),
                                            target=target))
            assert res.exception is None, (name, target, res.exception)


if __name__ == "__main__":
    run_module(globals())
