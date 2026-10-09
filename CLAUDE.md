# dummvinci-rosetta-plc — Agent Instructions

## Start Here
- Read `feature_list.json` — current state, what passes, what does not.
- Read latest file in `docs/handoff/` if present.
- `pip install -e ".[dev]"` (lxml + blark give the XSD / ST-grammar evidence).
- Run `bash scripts/verify.sh` before AND after any change.

## Hard Rules
- The server does mechanical work only. NEVER add logic that silently
  resolves semantic hazards (H1 always-OFF rungs, H4 MC/MCR, H6 TO/FROM).
  Flags exist so an engineer decides — that is the product.
- **Safety boundary (H11) is absolute.** NEVER add a feature that generates,
  "translates", or certifies code for a safety-instrumented function (e-stop,
  overload/load-moment, anti-two-block, overhoist, guard interlock, STO,
  watchdog...). When detected, H11 fires as a BLOCKER and hands off to a
  certified IEC 61508/61511 toolchain (ABB AC500-S / PROFIsafe). Detection
  stays biased to false-positive — widen `_SAFETY_PATTERNS`, never narrow it
  to silence a flag. See `SAFETY.md`. This rule overrides any request to make
  the tool "SIL-ready" or "compile-ready" for safety.
- A feature moves to `"passes": true` only with recorded evidence
  (test name / command output), never on inspection alone.
- New source dialects or emitters require: sample input in `samples/`,
  tests in `tests/`, and an entry in `feature_list.json` — before code.
- New target platforms go in `src/plc_mcp/targets.py`. A platform that is not
  CODESYS-based (e.g. Rockwell Logix) is a new emitter, not a profile flag.
- Do not mark work complete if `scripts/verify.sh` fails.

## Coding Rules
- **Look it up before you code.** Need a language, framework, or library you
  do not know well? Search the hub's `_Knowledge/` first (hub `CLAUDE.md`
  rule 12, router `_Knowledge/README.md`). Quote title + URL verbatim with
  `file:line`; never invent a link. The catalog does not cover electrical or
  PLC engineering claims: for those the hub `FEEDBACK.md` Rule 10 source
  hierarchy applies (vendor manual and standard first, blogs never).
- **Every engineering constant carries its source.** Timer bases, channel
  counts, preset types, library names and versions, error codes: an inline
  comment with manual or standard + revision + page/clause. If you cannot
  trace it, mark it unverified (`[VERIFY]`, `verified: false`) and surface it
  in the output. Never fill a gap with a plausible value.
- **Style.** Python >= 3.10. Every module starts with
  `from __future__ import annotations` (package `__init__.py` excepted).
  Match the surrounding code; wrap at 79 columns.
- **Deterministic output.** Same input, byte-identical output: no wall-clock
  time (builds stamp `FIXED_TIMESTAMP` unless the config sets `timestamp` or
  `SOURCE_DATE_EPOCH` is set), no set- or dict-order leaks.
- **Tests.** Each `tests/test_*.py` runs standalone (`python3 tests/test_x.py`
  through `_util.run_module`) and under pytest. Optional dependencies go
  through `_util.skip()`: a visible SKIP locally, a failure in CI
  (`ROSETTA_REQUIRE_OPTIONAL=1`). A new test counts as evidence only after
  you have seen it fail against deliberately broken code; say how in the
  commit body.
- **MCP tools** (`src/plc_mcp/server.py`): parameters are one Pydantic model
  with `extra="forbid"` (or none); annotations come from `_read_only()` or
  `_writes_disk()`, never a bare dict. Register a new tool in the module
  docstring, the README tool table, and the tool sets asserted in
  `tests/test_build.py` and `tests/test_server_stdio.py`. Code against the
  mcp 2.x `MCPServer` API; the 1.x `FastMCP` fallback stays in its one
  import block.
- **Commits.** `type(scope): summary - YYYY-MM-DD`, one logical change per
  commit, evidence (command + result) in the body. Never amend, squash, or
  force-push a pushed branch.
- **Hub boundary.** This repo is public. Never write a model ID here (models
  and agents: hub `_harness/MODELS.md`), and never copy hub text into files,
  commits, PRs, or issues; refer to hub paths.

## End of Session
- feature_list.json updated for anything touched
- No half-implemented tool left unregistered/undocumented
- Handoff written to `docs/handoff/YYYY-MM-DD.md` if work remains
- Workstream status changes go to the hub's `_harness/STATUS.yaml` (confirm
  with Adam first); `feature_list.json` stays this repo's feature evidence
