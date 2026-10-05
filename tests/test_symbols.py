"""External device-comment import (GX Developer / GX Works CSV export)."""
import tempfile
from pathlib import Path

from _util import SAMPLES, run_module, sample

from plc_mcp.parsers.melsec import parse_melsec_il
from plc_mcp.parsers.symbols import load_symbols, merge_symbols


def test_csv_symbols_merge_and_conflicts_are_reported():
    prog = parse_melsec_il(sample("centrifugal_excerpt.il"))
    syms, stats = load_symbols(SAMPLES / "centrifugal_comments.csv")
    assert syms["X82"] == "LS3 charge limit"
    assert stats["loaded"] == 9
    assert stats["skipped_rows"] == 3          # title, header, junk row
    merge_symbols(prog, syms, source="centrifugal_comments.csv")
    # inline [SYMBOLS] wins; the disagreement is recorded, never silent
    assert prog.symbols["X8B"].startswith("LSW-11 vibration")
    conflicts = prog.meta["symbol_conflicts"]
    assert [c["device"] for c in conflicts] == ["X8B"]
    assert conflicts[0]["external"] == "LSW-11 vibration sensor"
    # identical comments are not conflicts; new devices are added
    assert prog.symbols["Y0BA"] == "6Z RUN command"
    assert prog.symbols["M43"] == "MC zone enable"
    assert prog.meta["symbol_sources"] == ["centrifugal_comments.csv"]


def test_shift_jis_export_is_decoded():
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "comments_sjis.csv"
        f.write_bytes('"Device Name","Comment"\n"X0","非常停止 E-STOP"\n'
                      '"M5","運転準備"\n'.encode("cp932"))
        syms, stats = load_symbols(f)
        assert stats["encoding"] == "cp932"
        assert syms["X0"] == "非常停止 E-STOP"
        assert syms["M5"] == "運転準備"


def test_tab_and_semicolon_separated_text():
    syms, _ = load_symbols("X10\tPUMP RUN FB\nD100\tFLOW SP\n")
    assert syms == {"X10": "PUMP RUN FB", "D100": "FLOW SP"}
    syms, _ = load_symbols("X10;PUMP RUN FB\nY20;PUMP START\n")
    assert syms == {"X10": "PUMP RUN FB", "Y20": "PUMP START"}


def test_external_safety_comment_still_trips_h11():
    # A safety comment arriving only via the external file must still be
    # seen by H11 — the boundary may not depend on where symbols came from.
    from plc_mcp.analysis.semantics import flag_semantics
    prog = parse_melsec_il("0 LD X0\n1 OUT Y0\n9 END\n")
    merge_symbols(prog, {"X0": "EMERGENCY STOP PB"}, source="ext.csv")
    f = flag_semantics(prog)
    assert f["summary"]["safety_boundary"] is True


if __name__ == "__main__":
    run_module(globals())
