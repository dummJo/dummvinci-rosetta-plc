# dummvinci-rosetta-plc — Agent Instructions

<!-- dv-harness:boot:start v0.1.0 -->
## Boot: load the hub first

This repo is part of the DummVinci ecosystem. The private hub repo `dummJo/dummvinci-md` is the single source for workstream status, working rules, the model roster, the MCP and plugin registry, and the repo registry.

Before the first task in a session:

1. Run skill `dv-boot` (plugin `dummvinci-harness@dummvinci`). Without the plugin, locate the hub (`$DUMMVINCI_MD`, `../dummvinci-md`, `~/dummvinci-md`; in a cloud session, attach `dummJo/dummvinci-md`) and read, in order: `CLAUDE.md`, `_harness/INIT.md`, `_harness/STATUS.yaml`, `_harness/REPOS.yaml`, `_harness/MODELS.md`, `_Global-Memory/PREFERENCES.md`, `_Global-Memory/FEEDBACK.md`, `_MCP/INDEX.md`.
2. Do not read the hub's `_Personal/` or pricing files from this repo, and never copy hub content into this repo's files, commits, PRs, or issues.
3. Multi-agent work: skill `dv-orchestrate`. Model choice: hub `_harness/MODELS.md`, never a model ID written in this repo. Status updates go to the hub's `_harness/STATUS.yaml`.

If the hub cannot be loaded, say so in the first reply and continue with local context only.
<!-- dv-harness:boot:end -->

## Start Here
- Read `feature_list.json` — current state, what passes, what does not.
- Read latest file in `docs/handoff/` if present.
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
- Do not mark work complete if `scripts/verify.sh` fails.

## End of Session
- feature_list.json updated for anything touched
- No half-implemented tool left unregistered/undocumented
- Handoff written to `docs/handoff/YYYY-MM-DD.md` if work remains
