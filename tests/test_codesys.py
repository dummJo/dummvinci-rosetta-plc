"""CODESYS-family targets: profiles, library manifests, the structured PLCopen
project emitter (GVLs, segmentation, naming) and its validation evidence."""
import re
import xml.etree.ElementTree as ET

from _util import ROOT, run_module, sample, skip

from plc_mcp.parsers.melsec import parse_melsec_il
from plc_mcp.analysis.semantics import flag_semantics
from plc_mcp.emitters.st import emit_st
from plc_mcp.emitters.plcopen import emit_plcopen_xml, XSD_PATH
from plc_mcp.emitters.project import ProjectOptions, emit_project
from plc_mcp.targets import TARGETS, get_target, list_targets

NS = {"p": "http://www.plcopen.org/xml/tc6_0200",
      "p1": "http://www.plcopen.org/xml/tc6_0201",
      "x": "http://www.w3.org/1999/xhtml"}
CODESYS_FAMILY = ("abb_ac500_v3", "codesys_v35", "beckhoff_twincat3",
                  "schneider_machine_expert", "wago_codesys35")


def _gold(name="CENTRIFUGAL1"):
    return parse_melsec_il(sample("centrifugal_excerpt.il"), name=name)


def _proj(progs=None, **kw):
    progs = progs or [_gold()]
    return emit_project([(p, flag_semantics(p)) for p in progs],
                        ProjectOptions(**kw))


def _ns(xml):
    return NS["p1"] if "tc6_0201" in xml else NS["p"]


# --------------------------------------------------------------- targets

def test_target_registry_covers_codesys_family_and_names_non_codesys():
    ids = set(TARGETS)
    for t in CODESYS_FAMILY + ("generic_plcopen",):
        assert t in ids, t
        assert get_target(t, for_emit=True).supported
    for t in CODESYS_FAMILY:
        assert TARGETS[t].codesys_based
    rk = TARGETS["rockwell_logix5000"]
    assert rk.codesys_based is False and rk.supported is False
    assert "L5X" in rk.unsupported_reason
    try:
        get_target("rockwell_logix5000", for_emit=True)
    except ValueError as e:
        assert "not CODESYS" in str(e)
    else:
        raise AssertionError("Rockwell must refuse emission")
    assert TARGETS["abb_ac500_v2"].supported is False
    assert {t["id"] for t in list_targets()} == ids


def test_library_manifest_per_target():
    def req(t):
        return {lib.name for lib in TARGETS[t].libraries if lib.required}
    assert "Standard" in req("abb_ac500_v3")
    assert "Standard" in req("codesys_v35")
    assert "Tc2_Standard" in req("beckhoff_twincat3")
    m = _proj(target="abb_ac500_v3").libraries
    assert set(m["fb_types_used"]) == {"TON", "CTU"}   # golden sample
    blk = parse_melsec_il(sample("stack_blocks.il"), name="BLK")
    m2 = _proj([blk], target="abb_ac500_v3").libraries
    assert {"TON", "CTU", "R_TRIG", "F_TRIG"} <= set(m2["fb_types_used"])
    assert any(lib["name"] == "Standard" and lib["required"]
               for lib in m["libraries"])


# --------------------------------------------------------------- project

def test_project_gvl_architecture():
    pr = _proj(target="abb_ac500_v3")
    gvls = {g.name: g for g in pr.gvls}
    assert set(gvls) == {"GVL_IO", "GVL_MEM", "GVL_RETAIN"}
    names = {g: {v.name for v in gvls[g].vars} for g in gvls}
    assert "xDI_X87" in names["GVL_IO"] and "xDO_Y0C1" in names["GVL_IO"]
    assert "L1000" in names["GVL_RETAIN"] and "R5119" in names["GVL_RETAIN"]
    assert {"M500", "tT40", "cC10", "bInitFirstScan"} <= names["GVL_MEM"]
    assert gvls["GVL_RETAIN"].retain_kw == "RETAIN"
    assert all(g.qualified_only for g in pr.gvls)
    st = pr.st_files()
    body = st["PRG_CENTRIFUGAL1.st"]
    assert "GVL_IO.xDO_Y0C1 := " in body
    assert "{attribute 'qualified_only'}" in st["GVL_IO.st"]
    assert "VAR_GLOBAL RETAIN" in st["GVL_RETAIN.st"]


def test_plcopen_project_bodies_carry_no_declarations():
    for t in CODESYS_FAMILY + ("generic_plcopen",):
        xml = _proj(target=t).plcopen_xml()
        root = ET.fromstring(xml)
        ns = {"p": _ns(xml), "x": NS["x"]}
        pous = root.findall(".//p:pou", ns)
        assert pous, t
        for pou in pous:
            text = "".join(pou.find("p:body/p:ST/x:xhtml", ns).itertext())
            for kw in ("PROGRAM ", "END_PROGRAM", "END_VAR"):
                assert not re.search(rf"^\s*{kw}", text, re.M), (t, kw)
        main = root.find(".//p:pou[@name='PRG_CENTRIFUGAL1']", ns)
        assert main is not None, t


def test_plcopen_project_interfaces_are_typed():
    xml = _proj(target="abb_ac500_v3", max_networks_per_pou=3).plcopen_xml()
    root = ET.fromstring(xml)
    ns = {"p": _ns(xml), "x": NS["x"]}
    # one <data> per GVL: TC6 allows exactly one child element per <data>
    datas = root.findall("p:addData/p:data[@name='http://www.3s-software.com/"
                         "plcopenxml/globalvars']", ns)
    assert all(len(list(d)) == 1 for d in datas)
    gv = {d.find("p:globalVars", ns).get("name"): d.find("p:globalVars", ns)
          for d in datas}
    assert set(gv) == {"GVL_IO", "GVL_MEM", "GVL_RETAIN"}
    assert gv["GVL_RETAIN"].get("retain") == "true"
    t40 = gv["GVL_MEM"].find("p:variable[@name='tT40']/p:type/p:derived", ns)
    assert t40 is not None and t40.get("name") == "TON"
    flag = gv["GVL_MEM"].find("p:variable[@name='bInitFirstScan']", ns)
    assert flag.find("p:type/p:BOOL", ns) is not None
    assert flag.find("p:initialValue/p:simpleValue", ns).get("value") == "TRUE"


def test_plcopen_project_xsd_valid_for_every_target():
    try:
        from lxml import etree
    except ImportError:
        skip("lxml not installed (pip install -e .[dev])")
    schema = etree.XMLSchema(etree.parse(str(XSD_PATH)))
    progs = [_gold(), parse_melsec_il(sample("stack_blocks.il"), name="BLK")]
    for t in CODESYS_FAMILY + ("generic_plcopen",):
        for seg in (0, 3):
            xml = _proj(progs, target=t, max_networks_per_pou=seg).plcopen_xml()
            # TC6 v2.00 and v2.01 share this subset; validate against the
            # official v2.01 schema with the namespace aligned.
            doc = etree.fromstring(xml.replace("tc6_0200", "tc6_0201")
                                   .encode("utf-8"))
            assert schema.validate(doc), (t, seg, str(schema.error_log)[:800])


def test_generic_target_uses_resource_globals_and_var_external():
    pr = _proj(target="generic_plcopen")
    xml = pr.plcopen_xml()
    root = ET.fromstring(xml)
    ns = {"p": _ns(xml), "x": NS["x"]}
    res = root.find("p:instances/p:configurations/p:configuration/p:resource",
                    ns)
    assert res is not None
    assert {g.get("name") for g in res.findall("p:globalVars", ns)} == \
        {"GVL_IO", "GVL_MEM", "GVL_RETAIN"}
    inst = res.find("p:task/p:pouInstance", ns)
    assert inst.get("typeName") == "PRG_CENTRIFUGAL1"
    pou = root.find(".//p:pou[@name='PRG_CENTRIFUGAL1']", ns)
    ext = {v.get("name") for v in pou.findall("p:interface/p:externalVars/"
                                               "p:variable", ns)}
    assert {"xDO_Y0C1", "tT40", "L1000"} <= ext
    body = pr.st_files()["PRG_CENTRIFUGAL1.st"]
    assert "GVL_IO." not in body and "xDO_Y0C1 := " in body
    assert "VAR_EXTERNAL" in body


def test_twincat_io_allocation_placeholders():
    pr = _proj(target="beckhoff_twincat3")
    gio = pr.st_files()["GVL_IO.st"]
    assert re.search(r"xDI_X87 AT %I\* : BOOL;", gio)
    assert re.search(r"xDO_Y0C1 AT %Q\* : BOOL;", gio)
    assert "VAR_GLOBAL PERSISTENT" in pr.st_files()["GVL_RETAIN.st"]
    xml = pr.plcopen_xml()
    assert 'name="xDI_X87" address="%I*"' in xml


def test_segmentation_never_splits_an_mc_zone():
    pr = _proj(target="abb_ac500_v3", max_networks_per_pou=2)
    segs = [p for p in pr.pous if p.kind == "segment"]
    assert len(segs) >= 5
    zone = [p for p in segs if 2131 in p.steps]
    assert len(zone) == 1 and {2137, 2159} <= set(zone[0].steps)
    main = next(p for p in pr.pous if p.name == "PRG_CENTRIFUGAL1")
    assert main.kind == "main"
    assert main.calls == [p.name for p in segs]
    body = pr.st_files()["PRG_CENTRIFUGAL1.st"]
    calls = [body.index(f"{p.name}();") for p in segs]
    assert calls == sorted(calls)
    assert body.rindex("GVL_MEM.bInitFirstScan := FALSE;") > calls[-1]
    # every rung lands in exactly one segment
    all_steps = [s for p in segs for s in p.steps]
    assert len(all_steps) == len(set(all_steps)) == len(_gold().rungs)


def test_symbolic_naming_keeps_address_and_is_iec_legal():
    pr = _proj(target="abb_ac500_v3", naming="symbolic")
    names = [v.name for g in pr.gvls for v in g.vars]
    assert "xDI_X87_LSW_7_discharger_home" in names
    assert "L1000_3_R_RESET_latch" in names
    for n in names:
        assert re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", n), n
        assert "__" not in n and not n.endswith("_"), n
    assert len(names) == len(set(names))


def test_multi_program_consolidation_is_collision_free():
    progs = [_gold("P1"), _gold("P2")]
    pr = _proj(progs, target="codesys_v35")
    gnames = [g.name for g in pr.gvls]
    assert "GVL_P1_IO" in gnames and "GVL_P2_IO" in gnames
    pnames = [p.name for p in pr.pous]
    assert len(pnames) == len(set(pnames))
    assert "GVL_P2_IO.xDO_Y0C1" in pr.st_files()["PRG_P2.st"]


def test_project_output_is_deterministic():
    a = _proj(target="abb_ac500_v3", max_networks_per_pou=3)
    b = _proj(target="abb_ac500_v3", max_networks_per_pou=3)
    assert a.plcopen_xml() == b.plcopen_xml()
    assert a.st_files() == b.st_files()


def test_safety_banner_in_every_pou_of_a_safety_program():
    crane = parse_melsec_il(sample("marine_crane_safety_excerpt.il"),
                            name="CRANE")
    pr = _proj([crane], target="abb_ac500_v3", max_networks_per_pou=2)
    assert pr.safety_boundary
    for name, text in pr.st_files().items():
        if name.startswith("PRG_"):
            assert "SAFETY BOUNDARY HIT" in text, name
    assert "TODO [H11-SAFETY-BOUNDARY" in "".join(pr.st_files().values())
    assert "SAFETY BOUNDARY HIT" in pr.plcopen_xml()


def test_blark_parses_every_project_file():
    try:
        from blark import parse_source_code
    except ImportError:
        skip("blark not installed (pip install -e .[dev])")
    progs = [_gold(), parse_melsec_il(sample("stack_blocks.il"), name="BLK")]
    for t in CODESYS_FAMILY + ("generic_plcopen",):
        for name, text in _proj(progs, target=t,
                                max_networks_per_pou=3).st_files().items():
            res = parse_source_code(text)
            assert res.exception is None, (t, name, res.exception)


def test_legacy_wrapper_now_emits_structured_pou():
    p = _gold()
    xml = emit_plcopen_xml(emit_st(p, flag_semantics(p)))
    root = ET.fromstring(xml)
    ns = {"p": _ns(xml), "x": NS["x"]}
    pou = root.find(".//p:pou", ns)
    body = "".join(pou.find("p:body/p:ST/x:xhtml", ns).itertext())
    assert "END_VAR" not in body and "END_PROGRAM" not in body
    inputs = {v.get("name") for v in
              pou.findall("p:interface/p:inputVars/p:variable", ns)}
    assert "xDI_X87" in inputs
    retain = pou.find("p:interface/p:localVars[@retain='true']", ns)
    assert retain is not None
    try:
        from lxml import etree
    except ImportError:
        skip("lxml not installed")
    schema = etree.XMLSchema(etree.parse(str(XSD_PATH)))
    assert schema.validate(etree.fromstring(
        xml.replace("tc6_0200", "tc6_0201").encode("utf-8")))


def test_vendored_xsd_is_the_official_tc6_v201():
    import hashlib
    data = XSD_PATH.read_bytes().replace(b"\r\n", b"\n")
    assert b'targetNamespace="http://www.plcopen.org/xml/tc6_0201"' in data
    assert XSD_PATH.is_relative_to(ROOT / "src")
    assert hashlib.sha256(data).hexdigest() == (
        "db546a6d1847fee06268fa29baf9f643c8bdd75b3ad083eb9ebd4bfa93cb6c9b")


if __name__ == "__main__":
    run_module(globals())
