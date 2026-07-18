(* =====================================================================
   FB_AlarmPoint — single alarm point state machine
   dummvinci-rosetta-plc engineering library

   STANDARD BASIS
     IEC 62682 / ISA-18.2 alarm states:
       0 = NORM        normal, inactive
       1 = UNACK_ALM   active, unacknowledged
       2 = ACK_ALM     active, acknowledged
       3 = UNACK_RTN   returned to normal, still unacknowledged
     Meldeklassen mapping (German practice):
       eClass 2 = FAULT   (Störung)   — trips/stops the function
       eClass 1 = WARNING (Warnung)   — degraded, action required
       eClass 0 = EVENT   (Meldung)   — information, logged only

   USAGE
     One instance per alarm point. Call every task cycle:
       fbAlm(xIn := <raw condition>, xAck := <ack cmd>, xReset := <reset>);
     Wire xAppeared/xDisappeared/xAckEdge into FB_EventLogger for the
     historian, and xActive/xUnacked/eClass arrays into FB_AlarmManager.

   DISCLAIMER — draft engineering input, review + FAT mandatory.
   NOT for safety-instrumented functions (see SAFETY.md / H11): e-stop,
   overload, interlock chains belong on a certified safety CPU.
   ===================================================================== *)

FUNCTION_BLOCK FB_AlarmPoint
VAR_INPUT
    xIn        : BOOL;   (* raw alarm condition, TRUE = abnormal *)
    xAck       : BOOL;   (* acknowledge command (operator/group)  *)
    xReset     : BOOL;   (* reset for latched alarms              *)
    xLatching  : BOOL;   (* TRUE: stays active until xReset       *)
    tOnDelay   : TIME;   (* debounce before alarm becomes active  *)
    tOffDelay  : TIME;   (* delay before return-to-normal         *)
    iPriority  : INT;    (* 1=low .. 4=critical (per philosophy)  *)
    eClass     : INT;    (* 2=FAULT, 1=WARNING, 0=EVENT           *)
    diCode     : DINT;   (* error code for catalog/historian      *)
END_VAR
VAR_OUTPUT
    xActive       : BOOL;  (* alarm condition present (qualified) *)
    xUnacked      : BOOL;  (* requires operator acknowledgement   *)
    eState        : INT;   (* 0 NORM / 1 UNACK_ALM / 2 ACK_ALM / 3 UNACK_RTN *)
    xAppeared     : BOOL;  (* one-cycle pulse: alarm appeared     *)
    xDisappeared  : BOOL;  (* one-cycle pulse: alarm cleared      *)
    xAckEdge      : BOOL;  (* one-cycle pulse: acknowledged       *)
END_VAR
VAR
    tonQualify  : TON;
    tonClear    : TON;
    xQualified  : BOOL;
    xLatched    : BOOL;
    trigApp     : R_TRIG;
    trigDis     : F_TRIG;
    trigAck     : R_TRIG;
END_VAR

(* --- qualification: debounce both directions ---------------------- *)
tonQualify(IN := xIn, PT := tOnDelay);
tonClear(IN := NOT xIn, PT := tOffDelay);

IF tonQualify.Q THEN
    xQualified := TRUE;
END_IF;
IF tonClear.Q THEN
    xQualified := FALSE;
END_IF;

(* --- latching ------------------------------------------------------ *)
IF xLatching AND xQualified THEN
    xLatched := TRUE;
END_IF;
IF xReset AND NOT xQualified THEN
    xLatched := FALSE;
END_IF;

xActive := xQualified OR (xLatching AND xLatched);

(* --- ack edge: evaluate BEFORE the state machine clears xUnacked --- *)
trigAck(CLK := xAck AND xUnacked);
xAckEdge := trigAck.Q;

(* --- IEC 62682 state machine --------------------------------------- *)
CASE eState OF
    0: (* NORM *)
        IF xActive THEN
            eState := 1;
            xUnacked := TRUE;
        END_IF;
    1: (* UNACK_ALM *)
        IF xAck THEN
            eState := 2;
            xUnacked := FALSE;
        ELSIF NOT xActive THEN
            eState := 3;
        END_IF;
    2: (* ACK_ALM *)
        IF NOT xActive THEN
            eState := 0;
        END_IF;
    3: (* UNACK_RTN *)
        IF xAck THEN
            eState := 0;
            xUnacked := FALSE;
        ELSIF xActive THEN
            eState := 1;
        END_IF;
END_CASE;

(* --- edges for the historian ---------------------------------------- *)
trigApp(CLK := xActive);
xAppeared := trigApp.Q;
trigDis(CLK := xActive);
xDisappeared := trigDis.Q;

END_FUNCTION_BLOCK
