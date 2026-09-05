# priority_arbiter — 4-way fixed priority (bit 3 highest)

## Interface

| Signal | Dir | Width | Description |
|--------|-----|-------|-------------|
| clk    | in  | 1     | Clock |
| rst_n  | in  | 1     | Active-low async reset |
| req    | in  | 4     | Request vector |
| grant  | out | 4     | Registered grant (one-cycle latency) |

## Requirements

1. Reset clears `grant`.
2. `grant` is one-hot or zero (`$onehot0`).
3. A grant bit is set only if the corresponding request was high last cycle.
4. Higher request bits mask lower grants: if `req[i]` was high, `grant[j]` for `j < i` is 0.
5. If any request was high, exactly one grant is issued next cycle.

## Toolchain note

Concurrent SVA is lowered to immediate assertions; see `formalsynapse.sva_lower`.
