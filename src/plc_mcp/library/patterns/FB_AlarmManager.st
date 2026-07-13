(* =====================================================================
   FB_AlarmManager — aggregate N alarm points (IEC 62682 / ISA-18.2)
   dummvinci-rosetta-plc engineering library

   Aggregates the OUTPUT arrays of FB_AlarmPoint instances: per-class
   counts, unacked count, horn drive, and FIRST-OUT capture (which alarm
   started the trip cascade — the question every breakdown review asks).

   USAGE
     The application owns the FB_AlarmPoint instances and copies their
     outputs into the three arrays each cycle (or maps them directly):
       aActive[i]  := fbAlm_i.xActive;
       aUnacked[i] := fbAlm_i.xUnacked;
       aClass[i]   := fbAlm_i.eClass;
     then calls:
       fbMgr(nPoints := <used>, aActive := ..., aUnacked := ..., aClass := ...);
     Group acknowledge: wire fbMgr.xAckPulse into every point's xAck.
     Sizing: ARRAY[1..64] here — adjust bound + nPoints per project.

   DISCLAIMER — draft engineering input, review + FAT mandatory.
   NOT for safety-instrumented functions (SAFETY.md / H11).
   ===================================================================== *)

FUNCTION_BLOCK FB_AlarmManager
VAR_INPUT
    nPoints    : INT;    (* number of points actually used, <= 64 *)
    xGroupAck  : BOOL;   (* operator group-acknowledge command    *)
    xHornReset : BOOL;   (* silence horn without acknowledging    *)
END_VAR
VAR_IN_OUT
    aActive  : ARRAY[1..64] OF BOOL;
    aUnacked : ARRAY[1..64] OF BOOL;
    aClass   : ARRAY[1..64] OF INT;   (* 2 FAULT / 1 WARNING / 0 EVENT *)
END_VAR
VAR_OUTPUT
    nActive        : INT;
    nUnacked       : INT;
    nActiveFault   : INT;
    nActiveWarning : INT;
    iFirstOut      : INT;    (* index of first-out alarm, 0 = none *)
    xHorn          : BOOL;   (* unacked FAULT/WARNING present      *)
    xAckPulse      : BOOL;   (* wire into every point's xAck input *)
END_VAR
VAR
    i           : INT;
    aPrevActive : ARRAY[1..64] OF BOOL;
    nPrevActive : INT;
    xHornMuted  : BOOL;
    trigAck     : R_TRIG;
END_VAR

trigAck(CLK := xGroupAck);
xAckPulse := trigAck.Q;

nActive := 0;
nUnacked := 0;
nActiveFault := 0;
nActiveWarning := 0;

FOR i := 1 TO nPoints DO
    IF aActive[i] THEN
        nActive := nActive + 1;
        IF aClass[i] = 2 THEN
            nActiveFault := nActiveFault + 1;
        END_IF;
        IF aClass[i] = 1 THEN
            nActiveWarning := nActiveWarning + 1;
        END_IF;
    END_IF;
    IF aUnacked[i] THEN
        nUnacked := nUnacked + 1;
    END_IF;

    (* first-out: the point that went active while nothing was active *)
    IF aActive[i] AND NOT aPrevActive[i]
       AND (nPrevActive = 0) AND (iFirstOut = 0) THEN
        iFirstOut := i;
    END_IF;
    aPrevActive[i] := aActive[i];
END_FOR;

(* first-out clears only when the plant is alarm-free again *)
IF nActive = 0 THEN
    iFirstOut := 0;
END_IF;
nPrevActive := nActive;

(* horn: any unacked FAULT/WARNING; mute survives until all acked *)
IF xHornReset THEN
    xHornMuted := TRUE;
END_IF;
IF nUnacked = 0 THEN
    xHornMuted := FALSE;
END_IF;
xHorn := (nUnacked > 0) AND NOT xHornMuted;

END_FUNCTION_BLOCK
