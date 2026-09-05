# edge_detector — rising-edge pulse

## Interface

| Signal | Dir | Width | Description |
|--------|-----|-------|-------------|
| clk    | in  | 1     | Clock |
| rst_n  | in  | 1     | Active-low async reset |
| din    | in  | 1     | Input |
| pulse  | out | 1     | Combinational `din && !din_q` |

## Requirements

1. Reset clears the delay flop; `pulse` is `din` (because `din_q == 0`).
2. `pulse` is high iff `din` rose relative to the registered previous value (`$rose(din)` after the flop updates... actually pulse is combinational of current din and past din).
3. `pulse` is a single-cycle pulse: it cannot stay high two consecutive cycles unless `din` fell and rose again (impossible without `din` going 0).
4. When `din` is stable, `pulse` is 0 after the first cycle of that level.

## Toolchain note

Concurrent SVA is lowered to immediate assertions; see `formalsynapse.sva_lower`.
`$rose` is rewritten to `din && !$past(din)` when the expression must be shifted.
