# SAFETY.md — The safety boundary of dummvinci-rosetta-plc

> **Read this before pointing the tool at any program that contains a
> safety-instrumented function (SIF).**

## The one rule

**This tool does not generate safety code, and it must never be made to.**

`dummvinci-rosetta-plc` mechanically translates *standard* PLC logic and flags
everything it cannot translate faithfully. Safety-instrumented functions —
emergency stop, overload / load-moment protection, anti-two-block, overhoist,
guard interlocks, safe-torque-off, watchdogs, any function whose failure can
injure a person — are **out of scope by design**. When the tool detects one it
raises `H11-SAFETY-BOUNDARY` as a **BLOCKER**, stamps the emitted code with a
`SAFETY BOUNDARY HIT` banner, and stops. That is the correct behavior, not a
limitation to be worked around.

## Why an MCP / codegen tool cannot deliver "SIL-rated" code

This is not humility — it is IEC 61508 / IEC 61511.

1. **SIL is a property of the whole loop, not of code.** A Safety Integrity
   Level is assigned to a *safety function* end-to-end: sensor → logic solver →
   final element, plus proof-test interval and architecture (HFT, SFF). A code
   fragment has no SIL. You cannot "generate SIL 4 code" any more than you can
   buy a SIL 4 bolt.

2. **The toolchain itself must be certified.** SIL-rated logic must be built on
   a certified safety controller (e.g. **ABB AC500-S** with the **SM560-S**
   safety CPU and PROFIsafe I/O) using its certified engineering tool, whose
   compiler and libraries carry a TÜV certificate and a documented safety
   manual. A general-purpose translator — or an LLM driving one — is not part of
   any such certificate and cannot be retrofitted into one.

3. **The lifecycle is mandatory.** IEC 61508-3 requires a safety development
   lifecycle: hazard & risk analysis, safety requirements specification, a
   V-model with verification at every stage, FMEDA on the hardware, independent
   Functional Safety Assessment, and change management. Auto-translation
   short-circuits every one of those steps.

4. **"Compiles and downloads" ≠ "safe."** The failure mode this tool exists to
   prevent — code that is syntactically fine and semantically wrong — is exactly
   the failure mode that kills people on a crane. Near a SIF, the only safe
   automated behavior is to refuse.

## SIL 4 specifically

SIL 4 (PFDavg 10⁻⁵–10⁻⁴, risk reduction 10,000–100,000) is the highest level in
IEC 61508 and is exceptionally rare — effectively reserved for domains like
railway signalling and nuclear. **Marine and offshore cranes are not SIL 4.**
Their safety functions (overload/AOPS-MOPS, emergency lowering, e-stop) are
typically **SIL 2**, governed by classification-society rules — DNV
(DNV-ST-0378 / DNV-ST-0377), ABS, Lloyd's Register, Bureau Veritas — and
standards such as EN 13852 (offshore cranes) and EN 13849 / IEC 62061 for the
machine control system. If someone specifies "SIL 4" for a crane, that spec
itself needs review before any engineering starts.

## The correct hand-off path

When `H11-SAFETY-BOUNDARY` fires:

1. **Separate** the safety function from the standard control program. They live
   on different logic solvers (standard CPU vs. certified safety CPU).
2. **Engineer the SIF in the certified path:** ABB AC500-S / SM560-S + PROFIsafe
   F-Devices (or the platform mandated by the project), following the vendor
   safety manual and the IEC 61508/61511 lifecycle.
3. **Satisfy the classification society** for marine/offshore lifting appliances
   (DNV / ABS / Lloyd's / BV) — type approval, FMEA, and survey.
4. **Use this tool only for the non-safety remainder**, and even there the output
   is a draft: FAT and cold I/O checkout remain mandatory.

## What the tool detects (H11)

Conservative, symbol- and name-driven pattern matching for: emergency stop
(EMG/E-STOP/ESD), safety functions (SAFETY/STO/SIL/PROFIsafe/F-DI/F-DO),
crane-load safety (OVERLOAD/LMI/load-moment/MOPS/AOPS/anti-two-block/overhoist/
overspeed/overtravel/slack-rope/load-cell/deadman), guard interlocks
(light-curtain/muting/interlock/two-hand/enable-switch/safety-relay), and
watchdogs. Detection is intentionally biased toward **false positives over false
negatives**: a spurious halt on a non-safety rung costs a review; a missed SIF
costs more than money. Extend the patterns in
`src/plc_mcp/analysis/semantics.py` (`_SAFETY_PATTERNS`) as project vocabulary
grows — never narrow them to silence a flag.

---

*If in doubt, the boundary holds: the tool flags, a certified engineer decides.*
