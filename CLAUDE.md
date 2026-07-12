# dummvinci-rosetta-plc — Agent Instructions

## Start Here
- Read `feature_list.json` — current state, what passes, what does not.
- Read latest file in `docs/handoff/` if present.
- Run `bash scripts/verify.sh` before AND after any change.

## Hard Rules
- The server does mechanical work only. NEVER add logic that silently
  resolves semantic hazards (H1 always-OFF rungs, H4 MC/MCR, H6 TO/FROM).
  Flags exist so an engineer decides — that is the product.
- A feature moves to `"passes": true` only with recorded evidence
  (test name / command output), never on inspection alone.
- New source dialects or emitters require: sample input in `samples/`,
  tests in `tests/`, and an entry in `feature_list.json` — before code.
- Do not mark work complete if `scripts/verify.sh` fails.

## End of Session
- feature_list.json updated for anything touched
- No half-implemented tool left unregistered/undocumented
- Handoff written to `docs/handoff/YYYY-MM-DD.md` if work remains
