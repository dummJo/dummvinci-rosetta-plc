[SYMBOLS]
M500   3-R RESET (HMI)
L1000  3-R RESET latch
X69    4X READY input
X8B    LSW-11 vibration
X87    LSW-7 discharger home
Y0BA   6Z RUN command
Y0C1   VS-1 BRAKE release
T99    reset one-shot cutoff
T40    LS3T charge timer
D0     CHARGE TIME preset
R5119  memory signature
R5100  batch counter

[PROGRAM]
; step 0 - global reset one-shot
0     LD    M500
1     ANI   T99
2     OUT   L1000
3     LD    L1000
4     AND   M500
5     OUT   T99 K1
; memory-valid signature check
95    LD    M9039
96    AND=  R5119 K1374
98    OUT   L2045
; speed setpoint clamp (every scan)
113   LD    M9036
114   MOV   K1000 R5001
117   MOV   K1450 R5002
; charge timer with register preset
2150  LD    M37
2151  ORI   X82
2152  OUT   T40 D0
; disabled rung - always-OFF in series [VERIFY]
2238  LD    M5
2239  AND   M3
2240  AND   X87
2241  ANI   M13
2242  AND   M9037
2243  OUT   M142
; MC zone example
2131  LD    M43
2132  MC    N0 L1904
2137  LD    X8F
2138  AND   M37
2139  OR    M48
2140  OUT   M46
2159  MCR   N0
; accumulator divide pair (timer elapsed display)
3488  LD    M9036
3489  /     T40 K10 A0
3492  MOV   A0 D80
; OPCN-1 fieldbus init (no translation path)
3333  LD    M9036
3334  ANI   X3F
3335  TO    H2 H0 D140 K5
; batch counter increment
2450  LD    M623
2451  AND   M7
2452  D+P   K1 R5100
; brake release with emergency interlock
1403  LD    M109
1404  ANI   M88
1405  OR    M13
1406  OUT   Y0C1
; counter example - basket wash counter
5000  LD    M623
5001  OUT   C10 D279
5002  LD    L1000
5003  RST   C10
9999  END
