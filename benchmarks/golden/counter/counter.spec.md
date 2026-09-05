# counter — saturating up/down counter

## Interface

| Signal | Dir | Width | Description |
|--------|-----|-------|-------------|
| clk    | in  | 1     | Clock |
| rst_n  | in  | 1     | Active-low async reset |
| up     | in  | 1     | Increment request |
| down   | in  | 1     | Decrement request |
| count  | out | 4     | Saturating value |

## Requirements

1. On reset, `count` is 0.
2. When `up && !down` and `count != 15`, the next cycle `count` increments by 1.
3. When `down && !up` and `count != 0`, the next cycle `count` decrements by 1.
4. When both `up` and `down` are asserted, or neither is, `count` holds.
5. `count` never wraps: 15 stays 15 on increment, 0 stays 0 on decrement.

## Toolchain note

Concurrent SVA in `counter.sva.sv` is lowered to immediate assertions (`formalsynapse.sva_lower`)
because the OSS CAD Suite `yosys-slang` build rejects `assert property` (`SVA unsupported`).
