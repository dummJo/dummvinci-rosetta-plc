(* =====================================================================
   FB_DriveDiagACS880 — ACS880 fault/warning capture for the historian
   dummvinci-rosetta-plc engineering library

   Captures the active fault/warning codes of an ABB ACS880 drive (read
   over Modbus TCP / PROFINET) and converts them to hex strings that key
   directly into the error-code catalog (plc_errorcode_lookup, source
   "acs880") for cause + remedy.

   REGISTER MAPPING (project-specific — TODO verify against YOUR comms
   mapping in Drive Composer Pro; typical parameters):
     04.01  Tripping fault code    -> uiFaultCode
     04.06  Active warning code    -> uiWarnCode
     Status word bit FAULTED       -> xDriveFaulted
     Status word bit WARNING       -> xDriveWarning

   USAGE
     fbDiag(uiFaultCode := <mapped 04.01>, uiWarnCode := <mapped 04.06>,
            xDriveFaulted := <SW bit>, xDriveWarning := <SW bit>);
     On xNewFault: log to FB_EventLogger with diCode := fbDiag.diFaultCode
     and look the hex string up in the catalog.

   DISCLAIMER — draft engineering input, review + FAT mandatory. Codes and
   remedies come from the catalog, which must be verified against the
   ACS880 firmware manual for the installed FW revision.
   ===================================================================== *)

FUNCTION_BLOCK FB_DriveDiagACS880
VAR_INPUT
    uiFaultCode   : UINT;   (* drive par 04.01 via comms *)
    uiWarnCode    : UINT;   (* drive par 04.06 via comms *)
    xDriveFaulted : BOOL;   (* status word: faulted      *)
    xDriveWarning : BOOL;   (* status word: warning      *)
END_VAR
VAR_OUTPUT
    xFault      : BOOL;
    xWarning    : BOOL;
    diFaultCode : DINT;         (* numeric, for FB_EventLogger *)
    sFaultHex   : STRING(7);    (* catalog key, e.g. '2310'    *)
    sWarnHex    : STRING(7);
    xNewFault   : BOOL;         (* one-cycle pulse             *)
    xNewWarning : BOOL;
END_VAR
VAR
    trigF : R_TRIG;
    trigW : R_TRIG;
    uiTmp : UINT;
    iNib  : INT;
    i     : INT;
    sHex  : STRING(7);
END_VAR

xFault := xDriveFaulted;
xWarning := xDriveWarning;
diFaultCode := TO_DINT(uiFaultCode);

(* --- UINT -> 4-digit hex string (ACS880 codes read as hex) --------- *)
sHex := '';
uiTmp := uiFaultCode;
FOR i := 1 TO 4 DO
    iNib := TO_INT(uiTmp MOD 16);
    sHex := CONCAT(MID('0123456789ABCDEF', 1, iNib + 1), sHex);
    uiTmp := uiTmp / 16;
END_FOR;
sFaultHex := sHex;

sHex := '';
uiTmp := uiWarnCode;
FOR i := 1 TO 4 DO
    iNib := TO_INT(uiTmp MOD 16);
    sHex := CONCAT(MID('0123456789ABCDEF', 1, iNib + 1), sHex);
    uiTmp := uiTmp / 16;
END_FOR;
sWarnHex := sHex;

trigF(CLK := xDriveFaulted);
xNewFault := trigF.Q;
trigW(CLK := xDriveWarning);
xNewWarning := trigW.Q;

END_FUNCTION_BLOCK
