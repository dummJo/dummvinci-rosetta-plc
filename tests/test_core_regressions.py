"""Regressions for reproduced failures; no vendor compiler claims."""
from pathlib import Path
from xml.etree import ElementTree

import pytest

from plc_mcp.ir import Device, DeviceClass
from plc_mcp.parsers.melsec import parse_melsec_il
from plc_mcp.analysis.xref import build_xref
from plc_mcp.analysis.semantics import flag_semantics
from plc_mcp.analysis.iomap import map_io
from plc_mcp.analysis.report import generate_report
from plc_mcp.emitters.st import emit_st
from plc_mcp.emitters.plcopen import emit_plcopen_xml, emit_scl, lint_st


@pytest.mark.parametrize("combine,first,second,expected", [
    ("ANB", "OR", "OR", "((xDI_X0 OR xDI_X1) AND (xDI_X2 OR xDI_X3))"),
    ("ORB", "AND", "AND", "((xDI_X0 AND xDI_X1) OR (xDI_X2 AND xDI_X3))"),
])
def test_ld_block_is_not_a_new_network(combine, first, second, expected):
    program = parse_melsec_il(
        f"0 LD X0\n1 {first} X1\n2 LD X2\n3 {second} X3\n"
        f"4 {combine}\n5 OUT Y10\n")
    assert len(program.rungs) == 1
    assert f"xDO_Y10 := {expected};" in emit_st(program)


def test_each_mps_branch_keeps_its_condition():
    source = ("0 LD X0\n1 MPS\n2 AND X1\n3 OUT Y10\n4 MRD\n"
              "5 AND X2\n6 OUT Y11\n7 MPP\n8 AND X3\n9 OUT Y12\n")
    output = emit_st(parse_melsec_il(source))
    for target, contact in (("Y10", "X1"), ("Y11", "X2"), ("Y12", "X3")):
        assert f"xDO_{target} := (xDI_X0 AND xDI_{contact});" in output


def test_ld_block_inside_saved_branch_remains_one_network():
    source = ("0 LD X0\n1 MPS\n2 AND X1\n3 OUT Y0\n4 MRD\n"
              "5 LD X2\n6 OR X3\n7 ANB\n8 OUT Y1\n9 MPP\n10 OUT Y2\n")
    program = parse_melsec_il(source)
    assert len(program.rungs) == 1
    output = emit_st(program)
    assert "xDO_Y1 := (xDI_X0 AND (xDI_X2 OR xDI_X3));" in output
    assert "xDO_Y2 := xDI_X0;" in output


def test_rst_does_not_assign_fb_output_or_boolean_to_integer():
    source = ("0 LD X0\n1 OUT C10 D279\n2 LD X1\n3 RST C10\n"
              "4 LD X2\n5 RST T40\n6 LD X3\n7 RST D100\n")
    output = emit_st(parse_melsec_il(source))
    assert "RESET := xRstC10" in output
    assert "xRstC10 := xDI_X1;" in output
    assert "verify scan order" in output
    assert "cC10.Q := FALSE" not in output
    assert "tT40.Q := FALSE" not in output
    assert "TODO [RST-TIMER]" in output
    assert "IF xDI_X3 THEN D100 := 0; END_IF;" in output
    assert any("RST-TIMER" in item for item in lint_st(output)["warnings"])


def test_plf_and_pls_use_distinct_correct_edge_types():
    output = emit_st(parse_melsec_il(
        "0 LD X0\n1 PLF M10\n2 LD X1\n3 PLS M10\n"))
    assert "trigM10_PLF_1 : F_TRIG;" in output
    assert "trigM10_PLS_3 : R_TRIG;" in output
    assert "trigM10_PLF_1(CLK := xDI_X0);" in output
    assert "trigM10_PLS_3(CLK := xDI_X1);" in output


@pytest.mark.parametrize("operation,kind", [
    ("LDP", "R_TRIG"), ("LDF", "F_TRIG"),
    ("ANDP", "R_TRIG"), ("ANDF", "F_TRIG"),
    ("ORP", "R_TRIG"), ("ORF", "F_TRIG"),
])
def test_edge_contacts_get_fb_calls(operation, kind):
    prefix = "" if operation.startswith("LD") else "0 LD X0\n"
    output = emit_st(parse_melsec_il(
        prefix + f"1 {operation} X1\n2 OUT Y0\n"))
    assert f"edge{operation}_1 : {kind};" in output
    assert f"edge{operation}_1(CLK := xDI_X1);" in output
    assert f"edge{operation}_1.Q" in output


@pytest.mark.parametrize("token", ["D1A", "K1A", "K", "H", "T", "X", "Y"])
def test_malformed_device_is_diagnostic_not_exception(token):
    device = Device.parse(token)
    assert device.raw == token
    assert device.dclass is DeviceClass.UNKNOWN


def test_signed_decimal_constant_is_preserved():
    output = emit_st(parse_melsec_il("0 LD X0\n1 MOV K-1 D0\n"))
    assert "D0 := -1;" in output


@pytest.mark.parametrize("token,kind,number", [
    ("XAB", DeviceClass.INPUT, 0xAB),
    ("YFF", DeviceClass.OUTPUT, 0xFF),
    ("HAB", DeviceClass.HCONST, 0xAB),
])
def test_letter_leading_hex_digits_are_not_consumed_as_device_prefix(token, kind, number):
    device = Device.parse(token)
    assert device.dclass is kind
    assert device.number == number


def test_letter_leading_hex_addresses_work_in_mapping_and_emission():
    program = parse_melsec_il("0 LD XAB\n1 OUT YFF\n2 MOV HAB D0\n")
    output = emit_st(program)
    assert "xDO_YFF := xDI_XAB;" in output
    assert "D0 := 16#AB;" in output
    assert {row["legacy"] for row in map_io(program)["mapping"]} == {"XAB", "YFF"}


@pytest.mark.parametrize("source", [
    "0 LD\n1 OUT Y0\n", "0 LD X0\n1 OUT\n",
    "0 LD X0\n1 MOV K1\n", "0 LD= D0\n1 OUT Y0\n",
    "0 LD X0\n1 OUT T0\n", "0 LD X0\n1 SET\n",
    "0 LD X0 X1\n1 OUT Y0\n", "0 LD X0\n1 RST D0 D10\n",
    "0 LD X0\n1 MOV K1 D0 D1\n", "0 LD X0\n1 TO H0\n",
])
def test_bad_operand_count_blocks_emission_with_diagnostics(source):
    program = parse_melsec_il(source)
    findings = flag_semantics(program)
    assert any(item["code"] == "H9-MALFORMED-INSTRUCTION"
               for item in findings["flags"])
    with pytest.raises(ValueError, match="H9-MALFORMED-INSTRUCTION"):
        emit_st(program)


@pytest.mark.parametrize("source,code", [
    ("", "H9-EMPTY-PROGRAM"),
    ("LD X0\nOUT Y0\n", "H9-UNPARSED-LINE"),
    ("0 LD X0\nBAD EXPORT LINE\n1 OUT Y0\n", "H9-UNPARSED-LINE"),
    ("0 LD X0\n1 MOV K1A D0\n", "H9-UNKNOWN-OPERAND"),
    ("0 LD SM400\n1 OUT Y0\n", "H9-UNKNOWN-OPERAND"),
])
def test_lost_or_invalid_input_cannot_become_successful_code(source, code):
    program = parse_melsec_il(source)
    assert any(item["code"] == code for item in flag_semantics(program)["flags"])
    with pytest.raises(ValueError, match=code):
        emit_st(program, {"flags": [], "summary": {"safety_boundary": False}})


@pytest.mark.parametrize("source", [
    "0 LD X0\n1 ANB\n2 OUT Y0\n", "0 MRD\n1 OUT Y0\n",
    "0 MPP\n1 OUT Y0\n",
])
def test_invalid_condition_stack_is_explicit_error(source):
    with pytest.raises(ValueError, match="H9"):
        emit_st(parse_melsec_il(source))


@pytest.mark.parametrize("symbol", [
    "EMERGENCY_STOP", "E_STOP", "LOAD_MOMENT", "GUARD_DOOR",
    "watch_dog", "F_DI", "ENABLE_SWITCH", "Guard-Door", "EmergencyStop",
])
def test_h11_screens_underscore_separated_symbols(symbol):
    source = f"[SYMBOLS]\nX0 {symbol}\n[PROGRAM]\n0 LD X0\n1 OUT Y0\n"
    program = parse_melsec_il(source)
    assert flag_semantics(program)["summary"]["safety_boundary"] is True
    with pytest.raises(ValueError, match="SAFETY BOUNDARY HIT"):
        emit_st(program)


@pytest.mark.parametrize("source", [
    "; EMERGENCY STOP\n; process command\n0 LD X0\n1 OUT Y0\n",
    "0 LD X0\n1 OUT Y0 ; GUARD_DOOR\n",
    "0 LD X0\n; EMERGENCY STOP\n1 OUT Y0\n",
])
def test_h11_preserves_and_screens_all_source_comments(source):
    program = parse_melsec_il(source)
    assert flag_semantics(program)["summary"]["safety_boundary"] is True
    with pytest.raises(ValueError, match="SAFETY BOUNDARY HIT"):
        emit_st(program, {"flags": []})


def test_original_centrifugal_emergency_comment_is_never_sanitized():
    source = Path(__file__).resolve().parents[1] / "samples/centrifugal_excerpt.il"
    program = parse_melsec_il(source.read_text())
    assert "emergency interlock" in source.read_text()
    with pytest.raises(ValueError, match="SAFETY BOUNDARY HIT"):
        emit_st(program)


def test_partial_or_absent_symbols_are_not_safety_clearance():
    for symbols in ("", "[SYMBOLS]\nX0 Run input\n[PROGRAM]\n"):
        findings = flag_semantics(parse_melsec_il(
            symbols + "0 LD X0\n1 OUT Y0\n"))
        assert findings["summary"]["safety_screening_limited"] is True
        assert "H11-COVERAGE-UNKNOWN" in {f["code"] for f in findings["flags"]}
        assert "SCREENING INCOMPLETE" in findings["summary"]["verdict"]


def test_unknown_mnemonic_is_present_in_generated_draft():
    output = emit_st(parse_melsec_il("0 LD X0\n1 MAGIC M1\n2 OUT Y0\n"))
    assert "UNTRANSLATED: 1 MAGIC M1" in output
    assert "H9-UNKNOWN-MNEMONIC" in output
    assert lint_st(output)["ok"] is False


def test_iomap_handles_unrecognized_addresses_and_valid_neighbors():
    mapping = map_io(parse_melsec_il("0 LD XRUN\n1 AND X1F\n2 OUT Y0\n"))
    rows = {row["legacy"]: row for row in mapping["mapping"]}
    assert rows["XRUN"]["module"] == "UNMAPPED"
    assert rows["X1F"]["direction"] == "DI"
    assert rows["Y0"]["direction"] == "DO_DC"


@pytest.mark.parametrize("profile", [
    [], {"di_modules": None}, {"di_modules": ["DI"]},
    {"di_modules": [{"name": "DI", "channels": -1}]},
    {"di_modules": [{"name": "DI", "channels": True}]},
    {"relay_y_ranges": [[10, 1]]}, {"relay_y_ranges": [[0, "10"]]},
    {"unexpected": []},
])
def test_invalid_iomap_profile_reports_value_error(profile):
    with pytest.raises(ValueError):
        map_io(parse_melsec_il("0 LD X0\n1 OUT Y0\n"), profile)


def test_timer_and_counter_preset_registers_are_read_not_written():
    xref = build_xref(parse_melsec_il(
        "0 LD X0\n1 OUT T0 D0\n2 OUT C0 D1\n"))
    for device in ("D0", "D1"):
        assert xref["devices"][device]["read_count"] == 1
        assert xref["devices"][device]["write_count"] == 0


@pytest.mark.parametrize("name", [
    'foo"bar', "1bad", "bad name", "", "x" * 65, "PROGRAM",
])
def test_bad_generated_identifiers_are_rejected(name):
    program = parse_melsec_il("0 LD X0\n1 OUT Y0\n")
    with pytest.raises(ValueError, match="IEC identifier"):
        emit_st(program, pou_name=name)
    with pytest.raises(ValueError, match="IEC identifier"):
        emit_plcopen_xml("PROGRAM A\nEND_PROGRAM", pou_name=name)
    with pytest.raises(ValueError, match="IEC identifier"):
        emit_scl("PROGRAM A\nEND_PROGRAM", fb_name=name)


def test_project_label_quotes_are_escaped_in_xml_attributes():
    label = 'project "quoted" & <tag>'
    xml = emit_plcopen_xml("PROGRAM A\nEND_PROGRAM", project_name=label)
    root = ElementTree.fromstring(xml)
    assert root.find("{*}contentHeader").attrib["name"] == label


def test_scl_honors_requested_name_without_replacing_comment_text():
    output = emit_scl(
        "(* PROGRAM OldName *)\nPROGRAM SourceName\nEND_PROGRAM\n",
        fb_name="ClientBlock")
    assert "FUNCTION_BLOCK ClientBlock" in output
    assert "FUNCTION_BLOCK SourceName" not in output
    assert "(* PROGRAM OldName *)" in output
    assert output.rstrip().endswith("END_FUNCTION_BLOCK")


@pytest.mark.parametrize("marker", ["H11-SAFETY-BOUNDARY", "SAFETY BOUNDARY HIT"])
def test_export_wrappers_refuse_previous_warning_only_h11_output(marker):
    source = f"(* {marker} *)\nPROGRAM Old\nEND_PROGRAM\n"
    for exporter in (emit_plcopen_xml, emit_scl):
        with pytest.raises(ValueError, match="SAFETY BOUNDARY HIT"):
            exporter(source)


def test_lint_ignores_keywords_in_nested_comments_and_strings():
    source = ("PROGRAM A\nVAR\n text : STRING;\nEND_VAR\n"
              "(* IF VAR (* END_IF END_VAR *) *)\n"
              "// IF END_VAR\ntext := 'IF END_VAR';\nEND_PROGRAM\n")
    assert lint_st(source)["ok"] is True
    assert lint_st("PROGRAM A\n(* missing close")["ok"] is False


def test_source_comment_delimiters_cannot_change_generated_code():
    program = parse_melsec_il(
        "[SYMBOLS]\nY0 Operator *) IF TRUE THEN\n[PROGRAM]\n"
        "0 LD X0\n1 OUT Y0\n")
    output = emit_st(program)
    assert "Operator * ) IF TRUE THEN" in output
    assert lint_st(output)["ok"] is True


def test_report_remains_available_for_invalid_source():
    program = parse_melsec_il("0 LD XRUN\n1 MOV\ninvalid line\n")
    output = generate_report(program)
    assert "H9-MALFORMED-INSTRUCTION" in output
    assert "H9-UNPARSED-LINE" in output
    assert "UNMAPPED" in output


@pytest.mark.parametrize("first,second", [
    ("EMERGENCY_STOP", "RUN_INPUT"), ("RUN_INPUT", "EMERGENCY_STOP"),
])
def test_duplicate_symbols_cannot_hide_original_safety_labels(first, second):
    program = parse_melsec_il(
        f"[SYMBOLS]\nX0 {first}\nX0 {second}\n[PROGRAM]\n"
        "0 LD X0\n1 OUT Y0\n")
    assert len(program.meta["symbol_texts"]) == 2
    assert program.symbols["X0"] == first
    findings = flag_semantics(program)
    assert findings["summary"]["safety_boundary"] is True
    assert "H9-SYMBOL-CONFLICT" in {f["code"] for f in findings["flags"]}
    with pytest.raises(ValueError, match="SAFETY BOUNDARY HIT"):
        emit_st(program)


def test_non_sif_symbol_conflict_requires_review_instead_of_overwriting():
    program = parse_melsec_il(
        "[SYMBOLS]\nX0 RUN_INPUT\nX0 STOP_INPUT\n[PROGRAM]\n"
        "0 LD X0\n1 OUT Y0\n")
    with pytest.raises(ValueError, match="H9-SYMBOL-CONFLICT"):
        emit_st(program)


@pytest.mark.parametrize("source", [
    "0 OUT Y0\n", "0 AND X0\n1 OUT Y0\n", "0 OR X0\n1 OUT Y0\n",
    "0 LD X0\n1 LD X1\n2 OUT Y0\n",
    "0 LD X0\n1 MPS\n2 AND X1\n3 OUT Y0\n",
    "0 LD X0\n1 LD X1\n2 MPS\n3 OUT Y0\n4 MPP\n",
    "0 AND= D0 K1\n1 OUT Y0\n", "0 LD X0\n",
])
def test_missing_or_ambiguous_condition_is_never_guessed(source):
    program = parse_melsec_il(source)
    findings = flag_semantics(program)
    assert "H9-INVALID-CONDITION-STACK" in {f["code"] for f in findings["flags"]}
    assert "H9-INVALID-CONDITION-STACK" in generate_report(program)
    with pytest.raises(ValueError, match="H9-INVALID-CONDITION-STACK"):
        emit_st(program)


def test_two_nested_saved_branches_restore_each_original_condition():
    source = ("0 LD X0\n1 MPS\n2 AND X1\n3 MPS\n4 AND X2\n5 OUT Y0\n"
              "6 MRD\n7 AND X3\n8 OUT Y1\n9 MPP\n10 AND X4\n11 OUT Y2\n"
              "12 MPP\n13 AND X5\n14 OUT Y3\n")
    output = emit_st(parse_melsec_il(source))
    for target, contact in (("Y0", "X2"), ("Y1", "X3"), ("Y2", "X4")):
        assert f"xDO_{target} := ((xDI_X0 AND xDI_X1) AND xDI_{contact});" in output
    assert "xDO_Y3 := (xDI_X0 AND xDI_X5);" in output


@pytest.mark.parametrize("instruction", [
    "MOV K1 K2", "MOV K1 X0", "MOV K1 M0", "OUT D0", "OUT R0",
    "OUT X0", "OUT M9036", "SET D0", "SET C0", "PLS X0", "RST K1",
    "INC X0", "FMOV K0 K1 K2",
])
def test_readonly_or_wrong_typed_destinations_never_become_assignments(instruction):
    program = parse_melsec_il(f"0 LD X1\n1 {instruction}\n")
    findings = flag_semantics(program)
    assert "H9-MALFORMED-INSTRUCTION" in {f["code"] for f in findings["flags"]}
    with pytest.raises(ValueError, match="H9-MALFORMED-INSTRUCTION"):
        emit_st(program)


@pytest.mark.parametrize("instruction", ["LD D0", "LD K1", "LD= X0 K1"])
def test_unsupported_contact_types_are_not_converted_to_boolean_guesses(instruction):
    program = parse_melsec_il(f"0 {instruction}\n1 OUT Y0\n")
    with pytest.raises(ValueError, match="H9-MALFORMED-INSTRUCTION"):
        emit_st(program)


@pytest.mark.parametrize("instruction", [
    "MOV T0 D0", "MOV C0 D0", "/ T0 K10 D0", "INC V0",
])
def test_numeric_elapsed_count_and_index_semantics_remain_review_todos(instruction):
    program = parse_melsec_il(f"0 LD X0\n1 {instruction}\n")
    findings = flag_semantics(program)
    assert "H9-NUMERIC-VALUE-ACCESS" in {f["code"] for f in findings["flags"]}
    output = emit_st(program)
    assert f"TODO [H9-NUMERIC-VALUE-ACCESS] 1 {instruction}" in output
    assert "D0 :=" not in output
    assert "tT0.Q" not in output
    assert "cC0.Q" not in output


@pytest.mark.parametrize("instruction", [
    "INCP D0", "DECP D0", "MOVP K1 D0", "DMOVP K1 D0",
    "BMOVP D0 D1 K2", "FMOVP K0 D0 K2", "TOP H0 H0 D0 K1",
    "FROMP H0 H0 D0 K1", "*P K2 K3 D0", "/P K2 K3 D0",
    "+P K2 K3 D0", "-P K2 K3 D0", "D+P K1 D0", "D-P K1 D0",
])
def test_pulse_aliases_do_not_become_continuous_operations(instruction):
    program = parse_melsec_il(f"0 LD X0\n1 {instruction}\n")
    findings = flag_semantics(program)
    assert "H9-UNSUPPORTED-PULSE" in {f["code"] for f in findings["flags"]}
    with pytest.raises(ValueError, match="H9-UNSUPPORTED-PULSE"):
        emit_st(program)


def test_ordinary_increment_and_move_keep_their_continuous_behavior():
    output = emit_st(parse_melsec_il("0 LD X0\n1 INC D0\n2 MOV K1 D1\n"))
    assert "IF xDI_X0 THEN D0 := D0 + 1; END_IF;" in output
    assert "IF xDI_X0 THEN D1 := 1; END_IF;" in output


@pytest.mark.parametrize("source", [
    "PROGRAM EmergencyStop\nEND_PROGRAM\n",
    "PROGRAM EMERGENCYSTOP\nEND_PROGRAM\n",
    "PROGRAM emergencystop\nEND_PROGRAM\n",
    "PROGRAM P\nVAR\nEmergency_Stop : BOOL;\nEND_VAR\nEND_PROGRAM\n",
    "PROGRAM P\n(* GUARD_DOOR *)\nEND_PROGRAM\n",
])
def test_raw_st_sif_names_and_comments_are_refused_without_h11_marker(source):
    for exporter in (emit_plcopen_xml, emit_scl):
        with pytest.raises(ValueError, match="SAFETY BOUNDARY HIT"):
            exporter(source)


@pytest.mark.parametrize("name", [
    "EmergencyStop", "EMERGENCYSTOP", "emergencystop", "Guard_Door",
])
def test_requested_output_names_join_h11_screening(name):
    program = parse_melsec_il("0 LD X0\n1 OUT Y0\n")
    with pytest.raises(ValueError, match="SAFETY BOUNDARY HIT"):
        emit_st(program, pou_name=name)
    with pytest.raises(ValueError, match="SAFETY BOUNDARY HIT"):
        emit_plcopen_xml("PROGRAM P\nEND_PROGRAM", pou_name=name)
    with pytest.raises(ValueError, match="SAFETY BOUNDARY HIT"):
        emit_scl("PROGRAM P\nEND_PROGRAM", fb_name=name)


def test_standard_generated_disclaimers_do_not_block_normal_export():
    output = emit_st(parse_melsec_il("0 LD X0\n1 OUT Y0\n"))
    assert "H11-COVERAGE-UNKNOWN" in output
    ElementTree.fromstring(emit_plcopen_xml(output))
    assert "FUNCTION_BLOCK FB_Converted" in emit_scl(output)


@pytest.mark.parametrize("instruction", [
    "DMOV K1 D0", "D+ K1 D0", "D- K1 D0", "D* K1 K2 D0", "D/ K10 K2 D0",
])
def test_doubleword_operations_are_review_todos_not_guessed_int_assignments(instruction):
    program = parse_melsec_il(f"0 LD X0\n1 {instruction}\n")
    output = emit_st(program)
    assert "TODO [H9-NUMERIC-VALUE-ACCESS]" in output
    assert "paired-register semantics" in output
    assert "D0 :=" not in output
    assert "H9-NUMERIC-VALUE-ACCESS" in generate_report(program)


def test_doubleword_contact_cannot_drop_register_pair_width():
    program = parse_melsec_il("0 LDD= D0 K1\n1 OUT Y0\n")
    with pytest.raises(ValueError, match="H9-NUMERIC-CONDITION"):
        emit_st(program)
