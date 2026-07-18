(* =====================================================================
   FB_EventLogger — sequence-of-events ring-buffer historian
   dummvinci-rosetta-plc engineering library

   In-PLC SoE log: every alarm appearance/clearance/acknowledgement is
   stamped and kept in a retained ring buffer for breakdown analysis and
   HMI display. Design rationale: docs/library/HISTORIAN-DESIGN.md
   (resolution limits, time sync, when to use an external historian).

   USAGE
     One instance per log. Feed one event per rising edge of xLog —
     typically chained from FB_AlarmPoint edges:
       IF fbAlm.xAppeared THEN
           fbLog(xLog := TRUE, diCode := fbAlm.diCode,
                 eClass := fbAlm.eClass, eEdge := 0, rValue := <pv>,
                 dtNow := <rtc>, uiMsOfSec := <ms>);
       END_IF
     eEdge: 0 = APPEARED, 1 = DISAPPEARED, 2 = ACKED.
     (* TODO platform binding: dtNow/uiMsOfSec must come from the CPU RTC
        — e.g. AC500 V3 SysTimeRtc / DT clock FBs. Timestamp AT SOURCE,
        in the same task that detects the edge. *)
     Buffer: ARRAY[0..255] here — size per project; RETAIN memory is a
     real CPU budget, verify against the CPU's retain capacity.

   TYPES (move to a DUT object on Automation Builder import)
   ===================================================================== *)

TYPE ST_Event :
STRUCT
    udiSeq     : UDINT;   (* monotonic sequence number         *)
    dtStamp    : DT;      (* RTC date-time                     *)
    uiMs       : UINT;    (* millisecond-of-second, if available *)
    diCode     : DINT;    (* error/alarm code (catalog key)    *)
    eClass     : INT;     (* 2 FAULT / 1 WARNING / 0 EVENT     *)
    eEdge      : INT;     (* 0 APPEARED / 1 DISAPPEARED / 2 ACKED *)
    rValue     : REAL;    (* process value at event time       *)
END_STRUCT
END_TYPE

FUNCTION_BLOCK FB_EventLogger
VAR_INPUT
    xLog       : BOOL;    (* rising edge stores one event  *)
    dtNow      : DT;      (* TODO platform: bind to CPU RTC *)
    uiMsOfSec  : UINT;
    diCode     : DINT;
    eClass     : INT;
    eEdge      : INT;
    rValue     : REAL;
END_VAR
VAR_OUTPUT
    uiHead     : UINT;    (* index of most recent event    *)
    udiTotal   : UDINT;   (* events logged since power-up of retain *)
    xWrapped   : BOOL;    (* buffer has overwritten old entries *)
END_VAR
VAR
    trigLog : R_TRIG;
END_VAR
VAR RETAIN
    aBuf   : ARRAY[0..255] OF ST_Event;
    uiNext : UINT;
    udiSeq : UDINT;
END_VAR

trigLog(CLK := xLog);
IF trigLog.Q THEN
    aBuf[uiNext].udiSeq  := udiSeq;
    aBuf[uiNext].dtStamp := dtNow;
    aBuf[uiNext].uiMs    := uiMsOfSec;
    aBuf[uiNext].diCode  := diCode;
    aBuf[uiNext].eClass  := eClass;
    aBuf[uiNext].eEdge   := eEdge;
    aBuf[uiNext].rValue  := rValue;
    uiHead := uiNext;
    uiNext := (uiNext + 1) MOD 256;
    udiSeq := udiSeq + 1;
    udiTotal := udiSeq;
    IF udiSeq > 256 THEN
        xWrapped := TRUE;
    END_IF;
END_IF;

END_FUNCTION_BLOCK
