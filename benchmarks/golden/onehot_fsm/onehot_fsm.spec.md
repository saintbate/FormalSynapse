# onehot_fsm — one-hot handshake protocol FSM

## Interface

| Signal | Dir | Width | Description |
|--------|-----|-------|-------------|
| clk    | in  | 1     | Clock |
| rst_n  | in  | 1     | Active-low async reset |
| start  | in  | 1     | Leave IDLE |
| ack_in | in  | 1     | Completes REQ |
| req    | out | 1     | High in REQ |
| done   | out | 1     | High in DONE (one cycle) |
| state  | out | 4     | One-hot state |

States: IDLE=`0001`, REQ=`0010`, ACK=`0100`, DONE=`1000`.

## Requirements

1. Reset enters IDLE.
2. `state` is always one-hot.
3. Legal transitions only: IDLE→{IDLE,REQ}, REQ→{REQ,ACK}, ACK→DONE, DONE→IDLE.
4. `req` iff REQ; `done` iff DONE.

## Toolchain note

Concurrent SVA is lowered to immediate assertions; see `formalsynapse.sva_lower`.
