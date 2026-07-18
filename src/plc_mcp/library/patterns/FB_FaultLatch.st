(* =====================================================================
   FB_FaultLatch — lightweight fault latch with shared first-out capture
   dummvinci-rosetta-plc engineering library

   For simple trip chains where the full FB_AlarmPoint state machine is
   overkill: latch on fault, hold until reset, and capture whether THIS
   fault was the first of the cascade (shared group flag).

   USAGE
     One shared BOOL per fault group:
       VAR xGrpFirstOutTaken : BOOL; END_VAR
     Per fault:
       fbFault(xIn := <condition>, xReset := <reset cmd>,
               xGroupFirstOutTaken := xGrpFirstOutTaken);
     The application clears xGrpFirstOutTaken when the group is healthy
     again (all xLatched FALSE).

   DISCLAIMER — draft engineering input, review + FAT mandatory.
   NOT for safety-instrumented functions (SAFETY.md / H11).
   ===================================================================== *)

FUNCTION_BLOCK FB_FaultLatch
VAR_INPUT
    xIn    : BOOL;   (* fault condition, TRUE = faulted *)
    xReset : BOOL;   (* reset command                   *)
END_VAR
VAR_IN_OUT
    xGroupFirstOutTaken : BOOL;   (* shared across the fault group *)
END_VAR
VAR_OUTPUT
    xLatched  : BOOL;
    xFirstOut : BOOL;   (* TRUE if this fault started the cascade *)
END_VAR

IF xIn THEN
    IF NOT xLatched AND NOT xGroupFirstOutTaken THEN
        xFirstOut := TRUE;
        xGroupFirstOutTaken := TRUE;
    END_IF;
    xLatched := TRUE;
END_IF;

IF xReset AND NOT xIn THEN
    xLatched := FALSE;
    xFirstOut := FALSE;
END_IF;

END_FUNCTION_BLOCK
