<div align="center">

# 🗿 dummvinci-rosetta-plc

**A Rosetta Stone for PLC dialects.**
*Deterministic cross-vendor migration engineering, exposed as an MCP server.*

Mitsubishi MELSEC → ABB AC500 V3 draft migration · Siemens SCL seed

`by DummVinci — for PTTS retrofit engineering`

</div>

---

## The thesis

A PLC migration does **not** fail on syntax. It fails on **meaning.**

A simple contact and coil can map to `Y0 := X0;`. That does not establish
that an entire program can be translated faithfully. The hard cases live in
the **semantic** layer:

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

**The tool automates the supported mechanical subset and exposes unsupported
or ambiguous semantics for engineering review.** Where meaning does not survive translation, the tool does not
paper over the gap. It *marks* it, at the exact step, with the severity and the
engineering question that must be answered by a human before the code is
trusted.

A Rosetta Stone does not *interpret* the text for you. It gives you the faithful
correspondence and leaves the meaning to the scholar. That is precisely the
contract here.

---

## What it is — and is not

| ✅ Supported draft operations | 🚩 Flagged for engineer decision (never guessed) |
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
BLOCKER** and refuses ST, PLCopen XML, and SCL emission for that program. Analysis,
cross-reference, and reporting remain available for the certified hand-off.
Detection is deliberately biased toward false positives: a
spurious halt costs a review; a missed safety function costs more than money.

> **Marine / offshore cranes are not SIL 4.** Their safety functions (AOPS/MOPS
> overload, emergency lowering, e-stop) are typically **SIL 2**, governed by
> classification-society rules (DNV-ST-0378, EN 13852, ABS, Lloyd's, BV). If a
> spec says "SIL 4" for a crane, review the spec before engineering anything.

Full rationale and the certified hand-off path: **[`SAFETY.md`](./SAFETY.md).**

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

Use **Python 3.10 or newer**. Claude Code and Claude Desktop are separate
clients: a project `.mcp.json` registers the server with Claude Code, while
Desktop uses its own configuration file.

### 1. Install the server

Clone the repository if needed, then run the installer from its checkout.

**macOS / Linux:**

```bash
python3 scripts/install.py
```

**Windows (PowerShell or Command Prompt):**

```powershell
py -3 scripts/install.py
```

If the Windows Python launcher is unavailable, use `python scripts/install.py`
after checking that `python --version` reports 3.10 or newer.

The installer creates an isolated `.venv`, installs the package and its
dependencies, checks that the installed server imports **outside the checkout**,
and writes `.mcp.json` with the absolute path to that environment's interpreter.
There is no shell activation or `PYTHONPATH` requirement. Paths containing spaces
are supported; the JSON `command` contains the path without extra quotation marks.

Other MCP servers and settings are preserved. An existing, different Rosetta
entry stops installation before making changes; review it and use
`python3 scripts/install.py --replace` (Windows: `py -3 ...`) to replace that
entry. A changed existing config gets a private `.bak-*` copy. Configs and
backups may contain credentials for other servers; keep them out of Git.

Optional arguments:

```bash
python3 scripts/install.py --dry-run
python3 scripts/install.py --config /absolute/path/to/project/.mcp.json
```

The script finds its own repository, so it also works when invoked by absolute
path from another directory. Keep the checkout and its `.venv` at the same
location after registration; rerun the installer if you move them.

### 2. Connect Claude Code

Open Claude Code **in the project containing the generated `.mcp.json`**, approve
the project MCP server when prompted, and restart the session or reconnect it
with `/mcp`. Confirm `dummvinci-rosetta-plc` is connected and exposes nine tools.
On a trusted project whose approval was previously rejected, review
`claude mcp reset-project-choices` before reconnecting.

For registration outside this checkout, rerun the installer with
`--config /absolute/path/to/your/project/.mcp.json`, then open that project.
Do not paste `.mcp.json.example` unchanged: it deliberately contains a placeholder.

### 3. Connect Claude Desktop

In Claude Desktop, open **Settings → Developer → Edit Config** and merge the
Rosetta entry from the generated `.mcp.json` into its existing `mcpServers`
object. Preserve other entries. Desktop's usual config locations are:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

The installed server runs on Linux too, but Claude Desktop availability depends
on the client distribution; use Claude Code or another stdio MCP client there.
Fully quit and reopen Desktop after editing. The installer does not modify
Desktop settings automatically.

The generated entry has this shape (replace the illustrative path with the
installer's actual path):

```json
{
  "mcpServers": {
    "dummvinci-rosetta-plc": {
      "command": "/absolute/path/to/dummvinci-rosetta-plc/.venv/bin/python",
      "args": ["-m", "plc_mcp.server"]
    }
  }
}
```

On Windows the command ends in `.venv\\Scripts\\python.exe`; let the installer
encode the backslashes correctly. Do not use a shell command such as
`source .venv/bin/activate` or include quotes inside `command`.

### Troubleshooting installation

| Error / symptom | Check and correction |
|---|---|
| `python` not found / Microsoft Store opens | Install Python 3.10+; on Windows use `py -3`, on macOS/Linux use `python3`. |
| `No module named venv` / `ensurepip` unavailable | On Debian/Ubuntu install `python3-venv` for your Python version, then rerun. |
| `No module named plc_mcp` | Run the installer, then use its absolute `.venv` interpreter entry; a relative `PYTHONPATH=src` depends on the client's working directory. |
| `No module named mcp.server.fastmcp` with an older checkout | Update the checkout and rerun the installer; MCP SDK 2 renamed FastMCP. The current server supports its declared SDK range. |
| Config already has a different Rosetta entry | Inspect it; use `--replace` to back up and replace only that entry. |
| Server missing in Claude Code | Open the project containing `.mcp.json`, approve its project server, and reconnect via `/mcp`. |
| Server missing in Desktop | Merge the generated entry into Desktop's own config, validate JSON, then fully quit and restart Desktop. |
| `.venv` exists without this platform's interpreter | Check its contents and move it aside before rerunning; never reuse a Windows venv on macOS/Linux or vice versa. |
| Package download, proxy, certificate, or permissions error | Read the install output and fix that prerequisite; retain TLS verification. A failed install leaves the MCP config unchanged. |

The server speaks MCP over stdio and advertises nine tools. Automated integration
checks exercise `initialize` → `list_tools` → `call_tool`; a successful install
import check alone does not establish client connection or vendor-IDE validation.

---

## Typical workflow (JMR / DSI centrifugal retrofit)

MCP tool arguments use a nested `params` object. For example, call
`plc_parse_melsec` with:

```json
{
  "params": {
    "source": "0 LD X0\n1 OUT Y0\n2 END",
    "program_id": "ProcessExample"
  }
}
```

`source` is IL text by default. To read a local export, pass its absolute path
and set `source_is_path` to `true`; the file must be accessible to the machine
running the server. Keep the returned `program_id` for subsequent tools in the
same server session.

1. During shutdown, export the A2SH program from GX Developer as a **text list**.
   For a local process-only demonstration, use `samples/process_excerpt.il`.
2. `plc_parse_melsec` → parse summary + symbol table loaded.
3. `plc_flag_semantics` → the assumption register. **Resolve every BLOCKER with
   the site engineer before proceeding** — the five `M9037` disabled rungs live
   here. An H11 safety boundary requires a certified safety-toolchain hand-off;
   this server will refuse code emission for that program.
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

**Automated coverage (see `scripts/verify.sh` for the current checks):**
parser (counts, symbols, compare contacts) · xref · H1–H10 flagging · I/O map ·
ST emission incl. the `CTU` counter fix · PLCopen/SCL emission · report.

**Installation audit (0.2.1, 2026-10-12):** 171 tests and eight subtests pass
with regular wheel installs on Python 3.12/MCP 1.30 and Python 3.13/MCP 2.3;
the extracted source distribution passes the same verifier. The exact generated
Claude config was tested through a real stdio handshake from an unrelated
working directory. Linux/Windows CI is configured; Windows jobs were not run
on the local Linux host.

**Unsupported operations are visible:** malformed input and ambiguous condition
stacks refuse emission. Pulse aliases are not approximated as continuous
operations. Doubleword/paired-register and numeric timer/counter/index access
remain BLOCKER review TODOs without guessed executable assignments. The
nine-tool main pipeline does not include the unmerged library/AWL/build-bundle
features.

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
.venv/bin/python tests/test_pipeline.py
.venv/bin/python tests/test_install.py
.venv/bin/python -m pip install '.[test]'
PYTHON="$PWD/.venv/bin/python" bash scripts/verify.sh
```

On Windows use `.venv\Scripts\python.exe` for Python checks. The shell verification
script requires Bash (for example Git Bash); see its output for the complete
check list. Installer tests use temporary files and do not download packages or
edit the user's Claude config.

The original sample [`samples/centrifugal_excerpt.il`](./samples/centrifugal_excerpt.il)
deliberately reproduces the hazard patterns from the Tsukishima TSK-E-130-0402
dossier: reset one-shot, memory-signature check, register-preset timer, an
`M9037` disabled rung, an `MC` zone, an accumulator pair, an OPCN-1 `TO` block,
and a 32-bit batch counter. If a change breaks the tool's ability to see any of
these, a test goes red. Its emergency-stop comment now triggers H11; use the
process-only [`samples/process_excerpt.il`](./samples/process_excerpt.il) when
testing a successful analysis-and-emission pipeline. The original sample is
retained without removing its safety context.

---

<div align="center">

*Built for PT Prima Tekindo Tirta Sejahtera (PTTS) retrofit engineering.*
*The map is not the territory; the draft is not the commissioned machine.*

**🗿 dummvinci-rosetta-plc**

</div>
