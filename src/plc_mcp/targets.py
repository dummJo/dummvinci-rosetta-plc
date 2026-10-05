"""Target platform profiles for the emitters.

A profile captures ONLY the differences that change the generated artifact
mechanically: PLCopen namespace and GVL encoding, the CTU preset type of the
vendor's standard library, the retain keyword, I/O allocation style, and the
libraries the generated code needs. Everything a profile cannot know (exact
library versions, hardware retain support, I/O addresses) is written into
`notes` and the bundle's IMPORT.md as an engineer check — never assumed.

What "CODESYS-based" means here: the vendor IDE is built on the CODESYS V3.5
compiler and imports PLCopen XML. Allen-Bradley Logix (Studio 5000) is NOT in
that family — it needs an L5X emitter with different timer/counter semantics,
so it is listed as unsupported with the reason, never silently mapped.

No profile has been validated by a real IDE import yet; see feature_list.json
`codesys-ide-import-validation`.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

TC6_0200 = "http://www.plcopen.org/xml/tc6_0200"
TC6_0201 = "http://www.plcopen.org/xml/tc6_0201"


@dataclass(frozen=True)
class Library:
    name: str
    purpose: str
    required: bool = True      # needed by the generated code itself
    reference: str = ""        # where the name/version was taken from


@dataclass(frozen=True)
class Target:
    id: str
    vendor: str
    platform: str
    ide: str
    codesys_based: bool
    supported: bool
    plcopen_ns: str = TC6_0200
    # "codesys_adddata": GVLs as CODESYS globalvars addData (CODESYS family)
    # "tc6_resource":    GVLs as standard configuration/resource globalVars
    gvl_style: str = "codesys_adddata"
    # CODESYS-family tools accept GVL.var qualified access; strict IEC tools
    # need unqualified access plus VAR_EXTERNAL in every POU.
    qualified_access: bool = True
    io_alloc: str = "none"     # "none" | "twincat_star" (AT %I* / AT %Q*)
    ctu_pv_type: str = "WORD"  # CTU.PV / CTU.CV type in the vendor library
    retain_keyword: str = "RETAIN"
    libraries: tuple[Library, ...] = ()
    import_steps: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    unsupported_reason: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["libraries"] = [asdict(lib) for lib in self.libraries]
        return d


# ABB online library help for Automation Builder 2.9.0. Names below were
# taken from these public URLs via a search index (the pages themselves were
# not reachable from the build environment). Re-check in your AB release.
_AB290 = ("https://help.plc.abb.com/Libraries/AB290/ABB%20-%20AC500/"
          "Use%20Cases/")

_STD = Library("Standard", "IEC timers/counters/edges used by the generated "
               "code: TON, CTU, R_TRIG, F_TRIG")
_UTIL = Library("Util", "common in large projects: BCD_TO_INT/INT_TO_BCD, "
                "BLINK, HYSTERESIS, PID, RAMP — not needed by the draft "
                "itself", required=False)
_OSCAT = Library("OSCAT BASIC", "optional third-party (open source) utility "
                 "library widely used on CODESYS; check licence and the "
                 "build for your runtime", required=False)

_CODESYS_IMPORT = (
    "Project > Import PLCopenXML... and select plcopen/<project>.xml",
    "Library Manager: confirm the required libraries below are referenced",
    "Task Configuration: add PRG_<program> (the main POU of each program) "
    "to a cyclic task — task assignment is not imported",
    "I/O mapping: map GVL_IO variables to channels using "
    "analysis/<program>/iomap.csv as the worksheet",
    "Build (F11): the IDE is the compile authority, not this tool",
)

TARGETS: dict[str, Target] = {t.id: t for t in (
    Target(
        id="abb_ac500_v3", vendor="ABB", platform="AC500 V3 (PM56xx/PM58xx)",
        ide="Automation Builder 2.x (CODESYS V3.5 based)",
        codesys_based=True, supported=True,
        libraries=(
            _STD, _UTIL,
            Library("ABB_ModbusTcp_AC500 (namespace AC500_ModbusTcp)",
                    "Modbus TCP client/server (ModTcpMast, ModTcpMast2) — "
                    "candidate for rebuilding H6 legacy-fieldbus links and "
                    "drive comms on Ethernet. v1.1.10.3 in AB 2.9.0; use the "
                    "version your AB release ships", required=False,
                    reference=_AB290 + "Ethernet/ABB_ModbusTcp_AC500_Library/"
                    "index.html"),
            Library("ABB_Drives_AC500",
                    "function blocks for AC500 V3 <-> ABB drives "
                    "communication (e.g. ACS880/ACS580) — for the drive-comms "
                    "block the legacy program did over TO/FROM", required=False,
                    reference=_AB290 + "Drives/ABB_Drives_AC500_Library/"
                    "Function-Blocks/fld-Function-Blocks.html")),
        import_steps=_CODESYS_IMPORT,
        notes=(
            "Retain: VAR_GLOBAL RETAIN survives power-off on AC500 V3; "
            "confirm the CPU retain memory size covers GVL_RETAIN.",
            "Safety functions (H11) go to AC500-S / SM560-S, never into "
            "this standard application.",
        )),
    Target(
        id="codesys_v35", vendor="CODESYS GmbH (and OEMs)",
        platform="Any CODESYS V3.5 runtime",
        ide="CODESYS Development System V3.5",
        codesys_based=True, supported=True,
        libraries=(_STD, _UTIL, _OSCAT),
        import_steps=_CODESYS_IMPORT,
        notes=("Use this profile for CODESYS OEM platforms without a "
               "dedicated profile (e.g. Eaton XSoft-CODESYS-3, Festo, Lenze, "
               "Bosch Rexroth ctrlX PLC, Delta, Inovance) — verify the OEM "
               "library names in its Library Manager.",)),
    Target(
        id="beckhoff_twincat3", vendor="Beckhoff",
        platform="TwinCAT 3 PLC (CX / IPC)",
        ide="TwinCAT 3 XAE (CODESYS-derived)",
        codesys_based=True, supported=True,
        io_alloc="twincat_star", retain_keyword="PERSISTENT",
        libraries=(
            Library("Tc2_Standard", "IEC timers/counters/edges: TON, CTU, "
                    "R_TRIG, F_TRIG"),
            Library("Tc2_System", "system functions, common in large "
                    "projects", required=False),
            Library("Tc2_Utilities", "conversions, time, file helpers",
                    required=False)),
        import_steps=(
            "PLC project context menu > Import PLCopenXML... and select "
            "plcopen/<project>.xml",
            "References: confirm Tc2_Standard is referenced",
            "Add PRG_<program> to the PLC task",
            "I/O: GVL_IO variables are declared AT %I* / %Q*; link them to "
            "terminals in the I/O tree after the first build",
            "Build: TwinCAT is the compile authority",
        ),
        notes=(
            "GVL_RETAIN is VAR_GLOBAL PERSISTENT: written on orderly "
            "shutdown only — on power loss it needs a UPS. VAR RETAIN needs "
            "NOVRAM hardware support. Choose per IPC, do not assume.",
        )),
    Target(
        id="schneider_machine_expert", vendor="Schneider Electric",
        platform="Modicon M241/M251/M262 (and other Machine Expert PLCs)",
        ide="EcoStruxure Machine Expert (CODESYS V3.5 based)",
        codesys_based=True, supported=True,
        libraries=(_STD, _UTIL),
        import_steps=_CODESYS_IMPORT,
        notes=("Retain: check the controller's retain/persistent memory "
               "size against GVL_RETAIN.",)),
    Target(
        id="wago_codesys35", vendor="WAGO",
        platform="PFC100/PFC200/CC100 with CODESYS V3.5 or e!COCKPIT",
        ide="WAGO CODESYS V3.5 / e!COCKPIT",
        codesys_based=True, supported=True,
        libraries=(_STD, _UTIL),
        import_steps=_CODESYS_IMPORT,
        notes=("e!COCKPIT is CODESYS-based but its PLCopen import surface "
               "differs by version — confirm on a scratch project first.",)),
    Target(
        id="generic_plcopen", vendor="(vendor-neutral)",
        platform="Strict IEC 61131-3 PLCopen consumers (Beremiz / OpenPLC "
                 "Editor and similar)",
        ide="Any PLCopen TC6 importer",
        codesys_based=False, supported=True,
        plcopen_ns=TC6_0201, gvl_style="tc6_resource",
        qualified_access=False, ctu_pv_type="INT",
        libraries=(Library("IEC 61131-3 standard FBs", "TON, CTU, R_TRIG, "
                           "F_TRIG are part of the language standard"),),
        import_steps=("Import plcopen/<project>.xml; globals live in "
                      "configuration 'Config' / resource 'Resource' and "
                      "each POU declares VAR_EXTERNAL for what it uses",),
        notes=("CTU.PV is INT per IEC 61131-3 (CODESYS uses WORD).",)),
    Target(
        id="abb_ac500_v2", vendor="ABB", platform="AC500 V2 (PM5xx)",
        ide="Automation Builder with PS501 / CoDeSys 2.3",
        codesys_based=True, supported=False,
        unsupported_reason=(
            "CoDeSys 2.3 has no dependable PLCopen XML import; it needs a "
            ".EXP export emitter (not implemented). Port to AC500 V3 or add "
            "an .EXP emitter with its own sample and tests first.")),
    Target(
        id="rockwell_logix5000", vendor="Rockwell Automation (Allen-Bradley)",
        platform="ControlLogix / CompactLogix",
        ide="Studio 5000 Logix Designer",
        codesys_based=False, supported=False,
        unsupported_reason=(
            "Logix is not CODESYS-based and does not import PLCopen XML. It "
            "needs an L5X emitter: ST timers are TONR/FBD_TIMER with DINT "
            "millisecond presets, counters CTUD/FBD_COUNTER, one-shots "
            "OSRI/OSFI, and every tag is retentive (MELSEC non-latched M "
            "relays would need explicit first-scan clearing). Not "
            "implemented — build it when a project needs it, with a sample "
            "and tests (CLAUDE.md).")),
)}

DEFAULT_TARGET = "abb_ac500_v3"


def get_target(target_id: str | None, for_emit: bool = False) -> Target:
    tid = (target_id or DEFAULT_TARGET).strip().lower()
    if tid not in TARGETS:
        raise ValueError(f"unknown target '{target_id}'. Available: "
                         f"{', '.join(sorted(TARGETS))}")
    t = TARGETS[tid]
    if for_emit and not t.supported:
        family = "" if t.codesys_based else " (not CODESYS-based)"
        raise ValueError(f"target '{tid}'{family} is not supported for "
                         f"emission: {t.unsupported_reason}")
    return t


def list_targets() -> list[dict]:
    return [t.to_dict() for t in TARGETS.values()]
