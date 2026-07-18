# Error-code catalog — provenance rules

Fault codes and remedies are a **low-trust domain**: a wrong remedy in a
customer document is worse than no remedy. This catalog therefore runs on
three hard rules:

1. **No fabrication.** Every entry names its provenance (which manual,
   which document). An entry nobody can trace does not get added.
2. **`verified: false` until proven.** Entries seeded from general
   knowledge stay unverified until a human checks them against the vendor
   manual for the **installed firmware revision** (codes shift between FW
   generations). `plc_errorcode_lookup` repeats this status in every answer.
3. **Unknown codes are refused, not guessed.** If a code is not in the
   catalog, the lookup tool says so and points to the authoritative source
   (Drive Composer Pro, the firmware manual) — it never invents a cause or
   remedy.

## Adding a catalog / entries

- One JSON file per source family (`acs880.json`, `ac500.json`, ...),
  conforming to [`schema.json`](./schema.json).
- `class` uses the Meldeklassen taxonomy: `FAULT` (Störung), `WARNING`
  (Warnung), `EVENT` (Meldung). See `docs/library/ALARM-PHILOSOPHY.md`.
- Texts are bilingual EN/DE (`text_en`, `text_de`).
- After verifying an entry against the manual: set `verified: true` and
  update `provenance` with the exact document ID + revision you checked.
