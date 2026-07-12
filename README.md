<div align="center">

# 🗿 dummvinci-rosetta-plc

**A Rosetta Stone for PLC dialects.**
*Deterministic cross-vendor migration engineering, exposed as an MCP server.*

Mitsubishi MELSEC → ABB AC500 V3 (production path) · Siemens SCL (seeded)

`by DummVinci — for PTTS retrofit engineering`

</div>

---

## The thesis

A PLC migration does **not** fail on syntax. It fails on **meaning.**

Any competent script can turn `LD X0 / OUT Y0` into `Y0 := X0;`. That is the
easy 80% — the *mechanical* layer, where translation is total and lossless.
The 20% that ends careers lives one level down, in the **semantic** layer:

- A rung wired through an *always-OFF* contact — is that a bug, or a
  commissioning disable someone left in on purpose fifteen years ago?
- An `MC/MCR` master-control zone — whose collapse silently forces every coil
  inside it OFF and resets its timers. Translate it as plain `IF` and you have
  changed the machine's failure behavior without knowing it.
- A `TO/FROM` block writing a legacy OPCN-1 fieldbus buffer — there *is* no
  equivalent on the new network. It is not a translation problem. It is a
  redesign.

The Tsukishima-era migration dossiers are full of these. Every one of them is
a place where a naive transpiler produces code that **compiles, downloads, and
is wrong** — and you find out during cold commissioning, with the machine
energized.

**This tool is built on a single discipline: automate the mechanical layer
completely and deterministically, and refuse — categorically — to guess at the
semantic layer.** Where meaning does not survive translation, the tool does not
paper over the gap. It *marks* it, at the exact step, with the severity and the
engineering question that must be answered by a human before the code is
trusted.

A Rosetta Stone does not *interpret* the text for you. It gives you the faithful
correspondence and leaves the meaning to the scholar. That is precisely the
contract here.

---

## What it is — and is not

| ✅ Automated (deterministic, total) | 🚩 Flagged for engineer decision (never guessed) |
|---|---|
| MELSEC IL parsing → vendor-neutral IR | `H1` always-OFF "disabled" rungs |
| Full device read/write cross-reference | `H2` always-ON / first-scan special relays |
| Timer / counter / latch / compare / math translation | `H3` A0/A1 accumulator arithmetic |
| I/O channel mapping onto ABB S500 modules | `H4` MC/MCR outputs-OFF zone semantics |
| ST / PLCopen TC6 XML / SCL emission | `H5` index-register (V) array rewrites |
| Structural lint of generated code | `H6` TO/FROM legacy fieldbus buffers |
| Markdown migration report + sign-off table | `H7` CJ jumps · `H8` double coils · `H10` bulk wipes |

Every emitted file is a **draft** and says so in its own header. It carries
`(*TODO [Hx]*)` markers at the exact hazard sites. **Automation Builder / TIA
Portal remain the compile authority. Cold I/O checkout and FAT remain
mandatory.** This tool makes an engineer faster and harder to surprise — it does
not replace the engineer, the vendor IDE, or the commissioning procedure. It is
deliberately *weaker* than the principal toolchain in exactly the places where
quiet confidence would be dangerous, and stronger in the one place that scales
badly by hand: exhaustive, tireless, deterministic bookkeeping.

> **Input contract.** Plain-text instruction-list exports (GX Developer /
> GX Works "list" export). Proprietary binaries (`.gxw`, `.ap1x`) are opaque
> without the vendor IDE — export to text first. Garbage in is *flagged*, not
> silently dropped: unknown mnemonics surface as `H9` blockers, never vanish.

---

## The nine tools

The server exposes a pipeline. Each tool is one honest step; they chain through
an in-memory program store keyed by `program_id`, so you never re-pass megabytes
of listing between calls.

| Tool | Does | Refuses to |
|---|---|---|
| `plc_parse_melsec` | IL text → IR; loads `[SYMBOLS]`, compare contacts, applied instructions | invent a meaning for an unknown mnemonic |
| `plc_device_xref` | every read/write of every device; double-coil & write-only detection | — (pure fact) |
| `plc_flag_semantics` | the `[VERIFY]` assumption register (H1–H10) | rank a hazard as safe |
| `plc_map_io` | X/Y → AC500 S500 channels + electrical hazard notes | assert wiring is correct without checkout |
| `plc_emit_st` | draft IEC 61131-3 ST for AC500 V3 / CODESYS | omit a `(*TODO [Hx]*)` at a hazard site |
| `plc_emit_plcopen_xml` | PLCopen TC6 XML for Automation Builder import | claim AB-validated (see limits) |
| `plc_emit_scl` | Siemens SCL external-source skeleton (Phase-3 seed) | hide the manual timer-instancing gap |
| `plc_lint_st` | structural lint: IF/VAR pairing, leftover TODO/UNTRANSLATED | act as a compiler |
| `plc_generate_report` | full markdown analysis + BLOCKER sign-off table | mark a BLOCKER resolved on its own |

---

## The hazard register (H1–H10)

This is the heart of the tool — the codified memory of what goes wrong.

| Code | Construct | Why it does not survive mechanical translation | Severity |
|---|---|---|---|
| **H1** | Always-OFF contact in series | Rung can never energize — almost always a deliberate disable | 🔴 BLOCKER |
| **H2** | Always-ON / first-scan specials (`M9036`, `M9039`…) | Need a systematic `TRUE`/init-flag pass, not a literal port | 🟡 MINOR |
| **H3** | Accumulator `A0/A1` | Do not exist outside A-series; divide/multiply pairs must be rewritten | 🟠 MAJOR |
| **H4** | `MC/MCR` master-control zone | Zone collapse forces coils OFF and resets timers — must be reproduced explicitly | 🟠 MAJOR |
| **H5** | Index register `V` | Becomes declared `ARRAY` indexing; bounds must be verified | 🟠 MAJOR |
| **H6** | `TO/FROM` special-module buffer | Legacy fieldbus (OPCN-1…) — **no translation path**, redesign on new network | 🔴 BLOCKER |
| **H7** | `CJ` conditional jump | Restructure as conditional POU call; watch coil freeze-state assumptions | 🟠 MAJOR |
| **H8** | Multiple `OUT` coils on one device | Scan-order-dependent double coil | 🟠 MAJOR |
| **H9** | Unknown mnemonic | Parser gap or exotic instruction — never dropped, always surfaced | 🔴 BLOCKER |
| **H10** | `RST` on wide ranges / `FMOV K0` | Bulk memory wipe — guard the trigger explicitly | 🟠 MAJOR |

`plc_flag_semantics` returns a verdict: if any BLOCKER exists, the register
reads **"BLOCKED — resolve BLOCKER items before emitting final code."** The tool
enforces its own discipline.

---

## Install & register with Claude

```bash
pip install -e .
```

Add to your project `.mcp.json` (or `claude mcp add`):

```json
{
  "mcpServers": {
    "dummvinci-rosetta-plc": {
      "command": "python",
      "args": ["-m", "plc_mcp.server"],
      "env": { "PYTHONPATH": "src" }
    }
  }
}
```

The server speaks MCP over stdio and advertises all nine tools with correct
read-only / non-destructive annotations. Verified against the reference MCP
client: `initialize` → `list_tools` → `call_tool` round-trips cleanly.

---

## Typical workflow (JMR / DSI centrifugal retrofit)

1. During shutdown, export the A2SH program from GX Developer as a **text list**.
2. `plc_parse_melsec` → parse summary + symbol table loaded.
3. `plc_flag_semantics` → the assumption register. **Resolve every BLOCKER with
   the site engineer before proceeding** — the five `M9037` disabled rungs live
   here.
4. `plc_device_xref` + `plc_map_io` → wiring and tag basis, with electrical
   hazard notes (sink→source polarity flips, high-cycle relay outputs needing
   SSR interposing).
5. `plc_emit_st` → draft POU → human restructuring into a real FB architecture
   (`FB_BatchSequence`, `FB_Faults`, `FB_DriveComms` Modbus TCP to the ACS880…).
6. `plc_emit_plcopen_xml` → import to Automation Builder → compile → **FAT**.
7. `plc_generate_report` → the engineering-review deliverable that turns the
   `[VERIFY]` set into a formal, signed checklist.

---

## Epistemic honesty — where this tool stands today

This project refuses to overstate itself. Status is tracked in
[`feature_list.json`](./feature_list.json); a feature reaches `"passes": true`
only with **recorded evidence** (a named test or command output), never on
inspection alone.

**Verified (v0.2 — 8/8 tests, `scripts/verify.sh` = VERIFY OK):**
parser (counts, symbols, compare contacts) · xref · H1–H10 flagging · I/O map ·
ST emission incl. the `CTU` counter fix · PLCopen/SCL emission · report.

**Not yet proven — stated plainly, not buried:**

- 🔴 **Real-export validation.** The parser is tested against a crafted
  hazard-dense sample, **not yet against a real GX Developer export** from a live
  JMR/DSI shutdown. Until it parses a real dump with zero unparsed lines and all
  unknown mnemonics triaged, treat parser coverage as *provisional*.
- 🔴 **Automation Builder import.** The PLCopen XML is well-formed and
  schema-shaped, but **has not been round-tripped through an actual AB 2.x
  import.** Treat it as a draft container until proven.
- 🟡 **SCL emitter is a Phase-3 seed.** It emits, but `IEC_TIMER` instance DBs
  are still manual. Not a production Siemens path yet — honestly labeled as such.

Anyone claiming this tool is "compile-ready" or "certified equal to the vendor
toolchain" is misreading it. It is a **force multiplier for a competent
migration engineer**, whose value is exactly proportional to how ruthlessly it
refuses to guess.

---

## Roadmap

- **Phase 1 (this repo):** MELSEC A/AnS → AC500 V3. Driven by the DSI/JMR
  4-panel retrofit program.
- **Phase 2:** CP600 tag-DB generator; FB pattern library (fault latch, valve
  answer-back watchdog, ACS880 Modbus TCP comms block).
- **Phase 3:** Siemens S7 (AWL/SCL) as a first-class *input* dialect; richer SCL
  output. Built when a project needs it — **not speculatively.**

---

## Design principles

1. **Deterministic core, human semantics.** The server never resolves an
   `ALWAYS_OFF` rung, an `MC/MCR` zone, or a `TO/FROM` block on its own.
2. **No silent loss.** Unknown input is flagged (`H9`), never dropped.
3. **Evidence over assertion.** `"passes": true` requires a test name or command
   output. Inspection is not evidence.
4. **The draft announces itself.** Every generated artifact carries a header
   stating it is not commissioning-ready and lists its open decisions.
5. **The vendor IDE is the authority.** Automation Builder / TIA Portal compile;
   this tool prepares. FAT and cold I/O checkout are never optional.

---

## Tests

```bash
python tests/test_pipeline.py      # 8 named checks, all must PASS
bash   scripts/verify.sh           # tests + server import + py_compile → VERIFY OK
```

The sample [`samples/centrifugal_excerpt.il`](./samples/centrifugal_excerpt.il)
deliberately reproduces the hazard patterns from the Tsukishima TSK-E-130-0402
dossier: reset one-shot, memory-signature check, register-preset timer, an
`M9037` disabled rung, an `MC` zone, an accumulator pair, an OPCN-1 `TO` block,
and a 32-bit batch counter. If a change breaks the tool's ability to see any of
these, a test goes red.

---

<div align="center">

*Built for PT Prima Tekindo Tirta Sejahtera (PTTS) retrofit engineering.*
*The map is not the territory; the draft is not the commissioned machine.*

**🗿 dummvinci-rosetta-plc**

</div>
