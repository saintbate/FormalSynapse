# rr_arbiter — 4-way round-robin scheduler

## Interface

| Signal | Dir | Width | Description |
|--------|-----|-------|-------------|
| clk    | in  | 1     | Clock |
| rst_n  | in  | 1     | Active-low async reset |
| req    | in  | 4     | Request vector |
| grant  | out | 4     | Registered grant |

## Requirements

1. Reset: `grant == 0`, pointer at 0.
2. `grant` is `$onehot0`.
3. A grant bit is set only if that request was high last cycle.
4. If any request is high, a grant is issued next cycle.
5. Fairness: a persistently asserted `req[i]` is granted within 4 cycles (`##[1:4]`).

## Toolchain note

Concurrent SVA is lowered to immediate assertions. The fairness property uses a bounded
`|=> ##[1:4]` window, which the lowerer expands to a disjunction of `$past` terms.
