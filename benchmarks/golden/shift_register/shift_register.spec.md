# shift_register — 8-bit right-shift with parallel load

## Interface

| Signal | Dir | Width | Description |
|--------|-----|-------|-------------|
| clk    | in  | 1     | Clock |
| rst_n  | in  | 1     | Active-low async reset |
| load   | in  | 1     | Parallel load (highest priority) |
| shift  | in  | 1     | Shift right, `sin` into MSB |
| sin    | in  | 1     | Serial input |
| pdata  | in  | 8     | Parallel data |
| q      | out | 8     | Register value |

## Requirements

1. Reset clears `q` to 0.
2. `load` writes `pdata` on the next cycle (overrides `shift`).
3. `shift && !load` does `q <= {sin, q[7:1]}`.
4. If neither `load` nor `shift`, `q` holds.

## Toolchain note

Concurrent SVA is lowered to immediate assertions; see `formalsynapse.sva_lower`.
