# Alarm Philosophy — the concepts behind the library

> Read this BEFORE designing any alarm/event/fault handling. The FB
> patterns in `src/plc_mcp/library/patterns/` implement these concepts;
> this document is why they look the way they do.

## 1. The three message classes (Meldeklassen)

German machine-building practice (Siemens/VDMA convention, mirrored across
DIN-space vendors) separates every annunciation into three classes. The
library encodes them as `eClass` / catalog `class`:

| Class | DE | Meaning | Operator contract |
|---|---|---|---|
| **FAULT** (2) | Störung | Function has stopped / tripped | Must acknowledge; machine (part) will not run until cause cleared |
| **WARNING** (1) | Warnung | Degraded, still running | Must act soon; escalates to fault if ignored |
| **EVENT** (0) | Meldung / Hinweis | State information | Logged; no action required |

Rule of thumb: **if nothing stops and nobody must act, it is an EVENT, not
an alarm.** Alarm inflation is the root cause of alarm floods.

## 2. Alarm states — IEC 62682 / ISA-18.2

Every alarm point is a small state machine, not a boolean:

```
            condition TRUE                    ack
   NORM ────────────────────► UNACK_ALM ────────────► ACK_ALM
    ▲                            │                        │
    │           condition FALSE  │                        │ condition FALSE
    │ ack                        ▼                        │
    └──────────────────────── UNACK_RTN ◄────────────────┘? (no: ACK_ALM→NORM)
```

- `NORM → UNACK_ALM`: condition became true (after on-delay).
- `UNACK_ALM → ACK_ALM`: operator acknowledged, condition still present.
- `UNACK_ALM → UNACK_RTN`: condition cleared before acknowledge — the
  operator must still SEE that it happened (fleeting alarms hide root
  causes).
- `ACK_ALM → NORM`: condition cleared after acknowledge.
- `UNACK_RTN → NORM`: acknowledged after the fact.

`FB_AlarmPoint` implements exactly this; `xUnacked` drives the horn.

## 3. Priorities

Assign 1–4 by consequence + required response time (IEC 62682 principle:
priority reflects urgency, not organizational importance):

| Priority | Response | Example |
|---|---|---|
| 4 critical | immediate | bearing temp trip imminent |
| 3 high | minutes | cooling degraded |
| 2 medium | this shift | filter differential high |
| 1 low | planned | maintenance counter elapsed |

Guideline distribution (EEMUA 191 / ISA-18.2): ~80% low/medium, ~15%
high, ~5% critical. If half your alarms are critical, none are.

## 4. First-out (Erstwertmeldung)

When a trip cascades (drive trips → flow stops → pressure drops → 12
alarms), the review meeting needs the ONE that started it.
`FB_AlarmManager.iFirstOut` / `FB_FaultLatch.xFirstOut` capture the point
that went active while nothing else was — record it in the historian and
never clear it until the group is healthy again.

## 5. Alarm quality rules

- **Debounce both edges** (`tOnDelay`/`tOffDelay`) — a chattering alarm is
  worse than none; it trains operators to ignore the panel.
- **Latching** for conditions that self-clear but must be investigated
  (e.g. overcurrent that recovered): hold until explicit reset.
- **No alarm without an action.** Every FAULT/WARNING entry in the catalog
  carries a `remedy` — if you cannot write the remedy, it is an EVENT.
- **Hysteresis on analog alarms** — alarm at 80 °C, clear at 75 °C.
- **Flood control**: monitor alarms/10 min per operator; target ≤ 1–2
  steady-state (EEMUA 191 benchmark). Shelving (temporarily parking a
  known-nuisance alarm, visible on a shelf list) beats disabling.

## 6. Diagnostic classes for field devices — NAMUR NE 107

For instrument/device health (sensors, actuators, drives as devices), map
diagnostics to the four NE 107 categories before class assignment:

| NE 107 | Symbol | Library mapping |
|---|---|---|
| Failure | ✖ | FAULT |
| Function check | 🔧 | EVENT (expected during test) |
| Out of specification | ? | WARNING |
| Maintenance required | 🛢 | WARNING (priority 1–2) |

## 7. Display & logging conventions — VDI/VDE 3699

Process control display practice (German convention, useful everywhere):
unacknowledged = blinking, acknowledged = steady, cleared-unacked =
distinct (e.g. framed); the event list is chronological and NEVER hides
UNACK_RTN entries. The historian (`FB_EventLogger`) records
APPEARED / DISAPPEARED / ACKED as separate stamped events precisely so an
HMI can reconstruct this.

## 8. The safety boundary — unchanged

E-stop chains, overload/load-moment cutouts, interlocks: **not alarm
points.** They are safety functions (H11) — engineered on a certified
safety CPU, and only their STATUS is mirrored into the alarm system for
annunciation. The alarm system may never be IN the safety loop.

---

*Standards referenced: IEC 62682 / ISA-18.2 (alarm management), EEMUA 191
(benchmarks), NAMUR NE 107 (device diagnostics), VDI/VDE 3699 (display),
IEC 60204-1 (machine indication practice). Statements here paraphrase
engineering practice — consult the standard text for contractual work.*
