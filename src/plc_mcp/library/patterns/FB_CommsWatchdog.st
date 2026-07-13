(* =====================================================================
   FB_CommsWatchdog — communication supervision (DIAGNOSTICS ONLY)
   dummvinci-rosetta-plc engineering library

   Supervises a cyclic link (Modbus TCP slave, HMI, remote I/O) via a
   heartbeat counter that the remote increments. If the counter stops
   changing for tTimeout, the link is declared down — feed that into an
   FB_AlarmPoint (class WARNING or FAULT per the alarm philosophy).

   ⚠ THIS IS NOT A SAFETY WATCHDOG. A watchdog whose timeout must bring
   the machine to a safe state is a safety function — that belongs on a
   certified safety CPU (AC500-S) per SAFETY.md / hazard H11. This block
   only *reports* link health.

   USAGE (Modbus TCP to an ACS880 example)
     Map a drive register that changes continuously (e.g. a counter or
     the actual-speed word) into diHeartbeat:
       fbWd(diHeartbeat := <mapped register>, tTimeout := T#3s);

   DISCLAIMER — draft engineering input, review + FAT mandatory.
   ===================================================================== *)

FUNCTION_BLOCK FB_CommsWatchdog
VAR_INPUT
    diHeartbeat : DINT;   (* value the remote updates cyclically *)
    tTimeout    : TIME;   (* declare down after this long unchanged *)
END_VAR
VAR_OUTPUT
    xCommsOk  : BOOL;
    xTimeout  : BOOL;
    udiDrops  : UDINT;    (* number of times the link went down *)
END_VAR
VAR
    diPrev   : DINT;
    tonStale : TON;
    trigDown : R_TRIG;
END_VAR

tonStale(IN := (diHeartbeat = diPrev), PT := tTimeout);
diPrev := diHeartbeat;

xTimeout := tonStale.Q;
xCommsOk := NOT xTimeout;

trigDown(CLK := xTimeout);
IF trigDown.Q THEN
    udiDrops := udiDrops + 1;
END_IF;

END_FUNCTION_BLOCK
