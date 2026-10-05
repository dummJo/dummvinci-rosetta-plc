<div align="center">

# 🗿 dummvinci-rosetta-plc

**A Rosetta Stone for PLC dialects.**
*Deterministic cross-vendor migration engineering, exposed as an MCP server.*

Mitsubishi MELSEC → CODESYS-family PLCs (ABB AC500 V3 first; CODESYS V3.5, Beckhoff TwinCAT 3, Schneider Machine Expert, WAGO) · Siemens SCL (seeded)

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
| MELSEC IL parsing → vendor-neutral IR (ANB/ORB blocks, MPS/MRD/MPP) | `H1` always-OFF "disabled" rungs |
| Device-comment import (GX CSV, UTF-8 / Shift-JIS) | `H2` always-ON / first-scan special relays |
| Full device read/write cross-reference | `H3` A0/A1 accumulator arithmetic |
| Timer / counter / edge / latch / compare / math translation | `H4` MC/MCR outputs-OFF zone semantics |
| I/O channel mapping (ABB S500 or your own profile) | `H5` index-register (V) array rewrites |
| ST / structured PLCopen TC6 project / SCL emission | `H6` TO/FROM legacy fieldbus buffers |
| CODESYS project architecture: GVLs, segmented POUs, naming | `H7` CJ jumps · `H8` double coils · `H10` bulk wipes |
| Deterministic build bundle + MANIFEST (CLI and MCP) | `H12` wide results / 32-bit register pairs |
| Markdown migration report + sign-off table | `H11` safety functions → hand-off (never translated) |

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

## 🛑 The safety boundary (H11) — read this before any crane / SIL work

**This tool does not generate safety code, and it must never be made to.**

A SIL rating is a property of a whole safety loop — sensor + certified logic
solver + final element + proof test — under IEC 61508 / IEC 61511. It is *not* a
property of a code fragment. There is no such thing as "SIL 4 code" that a
translator can emit. Safety-instrumented functions belong in a **certified**
safety controller (e.g. ABB **AC500-S** + **SM560-S** / PROFiSAFE) engineered
through the full safety lifecycle — not in mechanically-generated draft ST.

So the tool draws a hard line. When it detects a safety-instrumented function —
emergency stop, overload / load-moment (LMI), anti-two-block, overhoist, guard
interlock, safe-torque-off, watchdog — it raises **`H11-SAFETY-BOUNDARY` as a
BLOCKER**, stamps the emitted code with a `SAFETY BOUNDARY HIT` banner, and hands
the function off. Detection is deliberately biased toward false positives: a
spurious halt costs a review; a missed safety function costs more than money.

> **Marine / offshore cranes are not SIL 4.** Their safety functions (AOPS/MOPS
> overload, emergency lowering, e-stop) are typically **SIL 2**, governed by
> classification-society rules (DNV-ST-0378, EN 13852, ABS, Lloyd's, BV). If a
> spec says "SIL 4" for a crane, review the spec before engineering anything.

Full rationale and the certified hand-off path: **[`SAFETY.md`](./SAFETY.md).**

---

## Target platforms — the CODESYS family

"CODESYS-based" means the vendor IDE is built on the CODESYS V3.5 compiler and
imports PLCopen XML. A target profile captures only what changes the artifact
mechanically; everything it cannot know is written into `IMPORT.md` as a check.

| Target id | Platform / IDE | Required library | Profile differences |
|---|---|---|---|
| `abb_ac500_v3` (default) | ABB AC500 V3 · Automation Builder 2.x | `Standard` | ABB library pointers (Modbus TCP, Drives) |
| `codesys_v35` | any CODESYS V3.5 runtime (also OEMs without a profile: Eaton, Festo, Lenze, ctrlX, Delta, Inovance…) | `Standard` | — |
| `beckhoff_twincat3` | Beckhoff TwinCAT 3 | `Tc2_Standard` | I/O `AT %I*`/`%Q*`, `VAR_GLOBAL PERSISTENT` |
| `schneider_machine_expert` | Schneider EcoStruxure Machine Expert | `Standard` | — |
| `wago_codesys35` | WAGO CODESYS V3.5 / e!COCKPIT | `Standard` | — |
| `generic_plcopen` | strict IEC importers (Beremiz / OpenPLC) | IEC std FBs | resource globals, `VAR_EXTERNAL`, `CTU.PV : INT` |
| `abb_ac500_v2` | AC500 V2 · CoDeSys 2.3 | — | **not supported**: needs an `.EXP` emitter |
| `rockwell_logix5000` | Allen-Bradley Control/CompactLogix · Studio 5000 | — | **not supported — not CODESYS** (see below) |

> **Allen-Bradley is not CODESYS-based.** Studio 5000 imports L5X, not PLCopen.
> Its ST timers are `TONR`/`FBD_TIMER` with DINT millisecond presets, counters
> `CTUD`, one-shots `OSRI`, and *every* tag is retentive (non-latched MELSEC `M`
> relays would need explicit first-scan clearing). That is a separate emitter
> with its own semantic hazards. It is listed and refused with that reason
> rather than mapped badly, and will be built when a project needs it
> (sample + tests first, per `CLAUDE.md`).

**Libraries.** The generated code needs only the IEC standard FBs (`TON`,
`CTU`, `R_TRIG`, `F_TRIG`). Each bundle's `libraries.json` / `IMPORT.md` lists
what is required and what is commonly added in large projects (`Util`, OSCAT,
and for AC500 V3 `ABB_ModbusTcp_AC500` / `ABB_Drives_AC500` for the H6 fieldbus
and drive-comms redesign). CODESYS libraries are installed **in the IDE**
(Library Manager / Automation Builder package), not from this repository. Pin
the exact names and versions your site uses in the project config:

```json
"libraries": [
  {"name": "Standard", "version": "<from your AB Library Manager>"},
  {"name": "ABB_ModbusTcp_AC500 (namespace AC500_ModbusTcp)",
   "version": "<from your AB Library Manager>", "required": true,
   "purpose": "Modbus TCP to ACS880 (H6 redesign)"}
]
```

They are recorded in `libraries.json` / `IMPORT.md` with `source: project config`.

---

## The tools

The server exposes a pipeline. Each tool is one honest step; they chain through
an in-memory program store keyed by `program_id`, so you never re-pass megabytes
of listing between calls. For big projects the export/build tools write to disk
instead of returning megabytes into the conversation.

| Tool | Does | Refuses to |
|---|---|---|
| `plc_parse_melsec` | IL text → IR; `[SYMBOLS]`, external comment CSV (`symbols_source`), compare contacts, applied instructions | invent a meaning for an unknown mnemonic; silently overwrite a conflicting comment |
| `plc_device_xref` | every read/write of every device; double-coil & write-only detection | — (pure fact) |
| `plc_flag_semantics` | the `[VERIFY]` assumption register (H1–H12) | rank a hazard as safe |
| `plc_map_io` | X/Y → channels of an I/O profile + electrical hazard notes | assert wiring is correct without checkout |
| `plc_emit_st` | single-POU draft ST for a target profile (`target`, `naming`) | omit a `TODO [Hx]` marker at a hazard site |
| `plc_emit_plcopen_xml` | wraps `plc_emit_st` output as a **structured** PLCopen POU | claim IDE-validated (see limits) |
| `plc_emit_scl` | Siemens SCL external-source skeleton (Phase-3 seed) | hide the manual timer-instancing gap |
| `plc_lint_st` | structural lint on comment-stripped code: IF/VAR pairing, nested/unclosed comments, leftovers | act as a compiler |
| `plc_generate_report` | full markdown analysis + BLOCKER sign-off table | mark a BLOCKER resolved on its own |
| `plc_list_targets` | target profiles, libraries, unsupported platforms with reasons | pretend a non-CODESYS platform is supported |
| `plc_list_programs` | programs loaded in this session | — |
| `plc_emit_plcopen_project` | multi-POU PLCopen project: GVLs, segments, main PRG | split an MC zone across POUs |
| `plc_export_project` | full bundle to disk for loaded programs | write into a foreign non-empty directory |
| `plc_build_project` | full bundle from a JSON project config (same engine as the CLI) | ignore a typo in the config |

---

## The hazard register (H1–H12)

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
| **H12** | 16-bit `*` / `/`, 32-bit `D+` `D-` `DMOV` `D*` `D/` `LDD=`… | The result spills into `Dn+1` (product high word, remainder, 32-bit pair). The draft is 16-bit, so width and aliasing must be decided | 🟠 MAJOR |
| **H11** | Safety-instrumented function | Out of scope: hand-off to a certified toolchain (see above) | 🔴 BLOCKER |

`plc_flag_semantics` returns a verdict: if any BLOCKER exists, the register
reads **"BLOCKED — resolve BLOCKER items before emitting final code."** The tool
enforces its own discipline.

---

## Install & register with Claude

```bash
pip install -e ".[dev]"     # tool + test evidence deps (pytest, lxml, blark)
pip install -e .            # runtime only (mcp, pydantic)
```

The server runs on mcp 2.x (`MCPServer`) and on mcp 1.x from 1.14 (`FastMCP`);
a clean install resolves 2.x. Older 1.x releases cannot register these tools.

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

The server speaks MCP over stdio and advertises all fourteen tools. Analysis
and emit tools are annotated read-only; `plc_export_project` and
`plc_build_project` write files and are annotated as such.

---

## Big projects: one config, one deterministic bundle

```bash
rosetta-plc targets                               # what can be emitted, and what not
rosetta-plc analyze panel1.il --symbols panel1_comments.csv
rosetta-plc build samples/project_jmr_example.json --out build/jmr
rosetta-plc validate-xml build/jmr/plcopen/JMR_DSI_Retrofit_demo.xml
```

The project config ([`samples/project_jmr_example.json`](./samples/project_jmr_example.json))
lists the legacy programs (IL export + device-comment CSV each), the target,
`naming` (`legacy` or `symbolic` = address + sanitised comment), and
`max_networks_per_pou`. It also takes optional `io_profile` (global or per
program), `libraries` pins, `timer_base_ms` and `timestamp`. Unknown keys are
an error, not a silent default.

What the bundle contains, per legacy program `<PID>`:

| Object | Content |
|---|---|
| `GVL_IO` | physical I/O (`xDI_*`, `xDO_*`); TwinCAT `AT %I*` / `%Q*` |
| `GVL_MEM` | M relays, D registers, timer/counter instances, first-scan flag |
| `GVL_RETAIN` | L relays, R file registers (`VAR_GLOBAL RETAIN` / `PERSISTENT`) |
| `PRG_<PID>_Snn` | segments of ≤ N networks; never split inside an MC zone; edge memories local |
| `PRG_<PID>` | calls the segments in legacy scan order, clears the first-scan flag; put this in the task |

Several programs can be consolidated into one project. GVLs are prefixed
(`GVL_<PID>_IO` …) and CODESYS access is qualified (`GVL.var`, `qualified_only`),
so identical device names cannot collide. Next to the PLCopen file, the bundle
holds per-object `.st` files for review/diff, per-program analysis (`flags.json`,
`xref.json`, `iomap.csv` with GVL paths, `report.md`), `libraries.json`,
`IMPORT.md` (target-specific checklist) and `MANIFEST.json` (sha256 of every
input and output). Same inputs give byte-identical output.

**Exit codes** (CLI and `exit_code` in the MCP result): `0` no BLOCKER ·
`2` BLOCKERs open (draft emitted) · `3` safety boundary hit · `1` error. A CI
job can block a merge while engineering decisions are open. The writer refuses
a non-empty directory that is not a previous build, and on rebuild deletes only
its own stale files.

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
6. `plc_export_project` (or `rosetta-plc build`) → import `plcopen/*.xml` into
   Automation Builder following `IMPORT.md` → compile → **FAT**.
7. `plc_generate_report` → the engineering-review deliverable that turns the
   `[VERIFY]` set into a formal, signed checklist.

---

## Epistemic honesty — where this tool stands today

This project refuses to overstate itself. Status is tracked in
[`feature_list.json`](./feature_list.json); a feature reaches `"passes": true`
only with **recorded evidence** (a named test or command output), never on
inspection alone.

**Verified (v0.3 — 63 named tests, `scripts/verify.sh` = VERIFY OK on
Python 3.10–3.12 with mcp 2.x, and on Python 3.10 with mcp 1.14):**
parser incl. ANB/ORB blocks and MPS branches · xref · H1–H12 flagging · I/O map ·
ST emission (edges, PLF, RST on T/C, timer/counter values, first-scan flag) ·
structured PLCopen projects **XSD-valid against the official TC6 v2.01 schema**
for every target · every emitted `.st` file **parses with an independent IEC ST
grammar** (`blark`) · deterministic bundles · 48k-instruction program in ~1–2 s.

**Fixed in v0.3 — v0.2 produced these silently:**
an `LD` opening a parallel block was split into a new network, so ANB/ORB rungs
lost a branch · every coil of an MPS/MRD/MPP or series-output rung got the
*last* condition · `ANDP`/`ORP` became plain contacts · `PLF` used an `R_TRIG` ·
`RST T`/`RST C` wrote to the FB output · timers/counters used as words read `.Q`
(BOOL) · `bInitFirstScan` was declared but never set · the PLCopen file carried
`PROGRAM … VAR … END_VAR` inside the ST body (schema-valid, not importable).

**Not yet proven — stated plainly, not buried:**

- 🔴 **Real-export validation.** The parser is tested against a crafted
  hazard-dense sample, **not yet against a real GX Developer export** from a live
  JMR/DSI shutdown. Until it parses a real dump with zero unparsed lines and all
  unknown mnemonics triaged, treat parser coverage as *provisional*.
- 🔴 **IDE import.** The PLCopen project is XSD-valid and grammar-checked, but
  **has not been imported into Automation Builder, CODESYS, TwinCAT or Machine
  Expert.** The CODESYS GVL encoding (`…/plcopenxml/globalvars` addData +
  `qualified_only`) follows the CODESYS export format and has not been
  confirmed by an import. If GVLs do not appear after import, the per-object
  `.st` files in the bundle are the fallback. First action for whoever has an
  IDE: import `build/jmr/plcopen/*.xml`, build, and record the result in
  `feature_list.json` (`codesys-ide-import-validation`).
- 🟡 **Grammar ≠ compiler.** `blark` proves the ST parses; it does not
  type-check. Only the IDE does.
- 🟡 **SCL emitter is a Phase-3 seed.** It emits, but `IEC_TIMER` instance DBs
  are still manual. Not a production Siemens path yet — honestly labeled as such.
- 🟡 **MCP client on mcp 2.x.** The server is tested over stdio with raw JSON-RPC
  (protocol `2025-06-18`), not yet driven by Claude Code against an mcp 2.x install.

Anyone claiming this tool is "compile-ready" or "certified equal to the vendor
toolchain" is misreading it. It is a **force multiplier for a competent
migration engineer**, whose value is exactly proportional to how ruthlessly it
refuses to guess.

---

## Roadmap

- **Phase 1 (this repo):** MELSEC A/AnS → AC500 V3 and the CODESYS family
  (v0.3: target profiles, multi-POU projects, build bundles). Driven by the
  DSI/JMR 4-panel retrofit program.
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
bash scripts/verify.sh             # every tests/test_*.py + server/CLI import → VERIFY OK
python -m pytest -q                # same tests under pytest
ROSETTA_REQUIRE_OPTIONAL=1 bash scripts/verify.sh   # as CI: a missing lxml/blark fails instead of SKIP
```

CI (`.github/workflows/verify.yml`) runs both on Python 3.10–3.12 with mcp 2.x,
plus Python 3.10 with mcp 1.14.0, the oldest supported 1.x.

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
