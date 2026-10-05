[SYMBOLS]
X0     PB START A
X1     PB START B
X14    PROX part present
Y20    CONVEYOR run
M10    BLOCK OR result
T5     DWELL timer
C3     PART counter
D10    DWELL elapsed display
D11    PART count display

[PROGRAM]
; Stack-machine regression sample. Every rung below exercises a construct a
; naive line-by-line converter gets silently wrong. Expected ST is asserted in
; tests/test_stack_semantics.py.
;
; parallel-branch block: (X0 OR X1) AND (X2 OR X3) -> Y20
0     LD    X0
1     OR    X1
2     LD    X2
3     OR    X3
4     ANB
5     OUT   Y20
; series-parallel block: (X4 AND X5) OR (X6 AND X7) -> M10
10    LD    X4
11    AND   X5
12    LD    X6
13    AND   X7
14    ORB
15    OUT   M10
; multi-output rung with MPS/MRD/MPP: each coil has its own condition
20    LD    X10
21    MPS
22    AND   X11
23    OUT   Y21
24    MRD
25    AND   X12
26    OUT   Y22
27    MPP
28    ANI   X13
29    OUT   Y23
; outputs in series: M21 follows M20 only, M23 needs M20 AND M22
30    LD    M20
31    OUT   M21
32    AND   M22
33    OUT   M23
; edge contacts: rising and falling, one edge memory per instruction
40    LDP   X14
41    OUT   M30
42    LD    M31
43    ANDF  X15
44    OUT   M32
; falling-edge pulse coil
50    LD    X16
51    PLF   M40
; timer current value used as a word (compare + move)
60    LD    M50
61    OUT   T5 K50
62    LD>   T5 K20
63    OUT   M51
64    LD    M52
65    MOV   T5 D10
; counter with separate reset rung, current value used as a word
70    LD    X17
71    OUT   C3 K5
72    LD    X18
73    RST   C3
74    LD    M53
75    MOV   C3 D11
; reset of a timer and of a data register
80    LD    X19
81    RST   T5
82    LD    X1A
83    RST   D12
; first-scan initialisation
90    LD    M9038
91    MOV   K0 D13
; 16-bit multiply: product is 32-bit and lands in D22:D23
92    LD    M54
93    *     D20 D21 D22
9999  END
