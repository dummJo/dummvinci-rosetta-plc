[SYMBOLS]
X10    EMERGENCY STOP pushbutton CH-A
X11    E-STOP relay feedback CH-B
X20    OVERLOAD cutout (load moment LMI)
X21    ANTI-TWO-BLOCK switch A2B
X22    hoist OVERHOIST limit
M100   crane READY
M200   slew enable
Y30    hoist UP contactor
Y31    main hoist BRAKE release
D40    boom angle setpoint
T10    watchdog WDT timer

[PROGRAM]
; normal process rung — not safety
0     LD    M100
1     AND   M200
2     OUT   Y30
; emergency stop chain (SAFETY — must be flagged H11)
10    LD    X10
11    AND   X11
12    OUT   M100
; overload / load-moment cutout (SAFETY — H11)
20    LD    X20
21    ORI   X21
22    OUT   M201
; overhoist limit (SAFETY — H11)
30    LD    X22
31    OUT   M202
; watchdog (SAFETY — H11)
40    LD    M100
41    OUT   T10 K10
9999  END
