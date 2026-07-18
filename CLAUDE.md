# dummvinci-rosetta-plc — Agent Instructions

## Start Here
- Read `feature_list.json` — current state, what passes, what does not.
- Read latest file in `docs/handoff/` if present.
- Run `bash scripts/verify.sh` before AND after any change.
- Task touches alarms / events / faults / historian / error codes?
  Read `docs/library/ALARM-PHILOSOPHY.md` + `docs/library/HISTORIAN-DESIGN.md`
  FIRST, then use the library patterns (`plc_lib_list`) — do not reinvent
  an alarm state machine ad hoc.

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
- **Catalog no-fabrication rule**: error-code entries require provenance;
  `verified: true` only after a human checked the entry against the vendor
  manual for the installed FW revision. `plc_errorcode_lookup` REFUSES
  unknown codes — never add guessing logic to "help".
- Library patterns are drafts by declaration: keep the usage headers and
  disclaimers intact; every pattern must stay `plc_lint_st`-clean.
- Do not mark work complete if `scripts/verify.sh` fails.

## End of Session
- feature_list.json updated for anything touched
- No half-implemented tool left unregistered/undocumented
- Handoff written to `docs/handoff/YYYY-MM-DD.md` if work remains
