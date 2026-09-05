# gray_counter — 4-bit Gray-code counter

## Interface

| Signal | Dir | Width | Description |
|--------|-----|-------|-------------|
| clk    | in  | 1     | Clock |
| rst_n  | in  | 1     | Active-low async reset |
| en     | in  | 1     | Increment enable |
| bin    | out | 4     | Binary count |
| gray   | out | 4     | `bin ^ (bin >> 1)` |

## Requirements

1. Reset: `bin == 0`, `gray == 0`.
2. `en` increments `bin` next cycle (wraps).
3. `gray` is always the Gray encoding of `bin`.
4. When `en` is high, exactly one bit of `gray` changes (`$countones(gray ^ $past(gray)) == 1`).

## Toolchain note

Concurrent SVA is lowered to immediate assertions; see `formalsynapse.sva_lower`.
