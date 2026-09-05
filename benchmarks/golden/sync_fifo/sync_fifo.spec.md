# sync_fifo — depth-4 synchronous FIFO

## Interface

| Signal  | Dir | Width | Description |
|---------|-----|-------|-------------|
| clk     | in  | 1     | Clock |
| rst_n   | in  | 1     | Active-low async reset |
| wr_en   | in  | 1     | Write request (ignored when full) |
| rd_en   | in  | 1     | Read request (ignored when empty) |
| wr_data | in  | 8     | Write data |
| rd_data | out | 8     | Combinational read of `mem[rd_ptr]` |
| full    | out | 1     | `count == 4` |
| empty   | out | 1     | `count == 0` |
| count   | out | 3     | Occupancy |

## Requirements

1. Reset: pointers and `count` are 0; `empty` is 1; `full` is 0.
2. A write that is not full increments `count` unless a read also occurs.
3. A read that is not empty decrements `count` unless a write also occurs.
4. Simultaneous legal write+read keeps `count` unchanged.
5. `count` stays in `0..4`. `full` and `empty` match `count`.
6. A write into an empty FIFO is visible on `rd_data` the next cycle.

## Toolchain note

Memory is not read inside concurrent SVA (Yosys/slang limitation). Ordering is checked as
"write-to-empty then combinational head equals that write". Concurrent SVA is lowered to
immediate assertions.
