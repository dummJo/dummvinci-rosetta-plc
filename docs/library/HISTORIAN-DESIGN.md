# Event-Log Historian Design — sequence of events done honestly

> Companion to `FB_EventLogger.st`. Read before promising anyone a
> "high-end historian" — half of historian quality is knowing what a PLC
> ring buffer can and cannot claim.

## 1. What the in-PLC historian is for

Breakdown forensics and acceptance evidence: *what happened, in what
order, stamped, surviving a power cycle.* Every FB_AlarmPoint edge
(APPEARED / DISAPPEARED / ACKED) becomes one immutable record:

```
seq | timestamp (DT + ms) | code | class | edge | process value
```

`seq` is monotonic — gaps prove records were overwritten (`xWrapped`),
which is itself information. Never silently re-use sequence numbers.

## 2. Timestamp discipline (where "German standard" is won or lost)

- **Stamp at source, in the detecting task.** A stamp applied two tasks
  later measures your scheduler, not the plant.
- **Resolution honesty:** event ORDER within one task cycle is not
  resolvable — a 10 ms task gives ±10 ms. True SoE (1 ms class, e.g.
  breaker protection) needs SoE-capable input hardware, not ST code.
  State the achieved resolution in the FAT protocol; do not imply better.
- **Time sync:** the CPU RTC must be NTP-disciplined if events will ever
  be correlated across devices (drive log vs PLC log vs SCADA). Log time
  changes themselves as EVENTs — a backwards-jumping timestamp without an
  explanatory record destroys the log's evidentiary value.
- DST rule: log in UTC (or fixed offset), display local. Duplicated
  02:00–03:00 wall-clock hours have ruined more than one incident review.

## 3. Buffer + persistence

- Ring buffer in RETAIN memory (`VAR RETAIN`) — survives power cycles.
  RETAIN is a hard CPU budget (AC500 V3: limited kB): size
  `ARRAY[0..N]` accordingly; 256 × ~32 B ≈ 8 kB is a sane default.
- Wrap counter + overflow flag are outputs, not internals: the HMI must
  show when history was lost.
- Export paths, in order of preference: HMI/SCADA reads the array cyclically
  and appends to its own database → CSV to SD card (platform file
  functions — TODO marker in the FB) → nothing (buffer only). The PLC is
  the *staging* store, never the archive.

## 4. When the PLC historian is NOT enough

Hand off to an external historian/SCADA database when any of these hold:
retention beyond days, cross-device correlation, reporting/KPIs
(availability, MTBF), > a few events/second sustained, or contractual
audit trails. The in-PLC log then remains as the black-box recorder that
catches what the network connection missed.

## 5. Error codes + remedies

Every FAULT/WARNING event carries `diCode`, which keys into the
error-code catalog (`plc_errorcode_lookup`): class, bilingual text, cause,
remedy, provenance. The catalog's no-fabrication rules
(`src/plc_mcp/library/catalog/README.md`) apply — an event log that
displays an unverified remedy as gospel is a liability, so the lookup
always carries the `verified` status through to the display layer.

## 6. Acceptance checklist (FAT)

- [ ] Every alarm point produces APPEARED and DISAPPEARED records with
      correct code/class
- [ ] ACK records carry the correct point and time
- [ ] Power cycle: buffer intact, seq continues without reuse
- [ ] Buffer wrap: oldest overwritten, `xWrapped` indicated on HMI
- [ ] RTC sync verified; time-set event logged
- [ ] Stated timestamp resolution matches the detecting task's cycle time
