# spi_master — 8-bit MSB-first SPI master

## Interface

| Signal  | Dir | Width | Description |
|---------|-----|-------|-------------|
| clk     | in  | 1     | Clock |
| rst_n   | in  | 1     | Active-low async reset |
| start   | in  | 1     | Pulse to begin a transfer (ignored while busy) |
| data_in | in  | 8     | Byte to shift out |
| sclk    | out | 1     | SPI clock (clk/2 while busy) |
| mosi    | out | 1     | MSB of the shift register |
| cs_n    | out | 1     | Active-low chip select (`!busy`) |
| busy    | out | 1     | Transfer in progress |
| done    | out | 1     | One-cycle pulse at the end of a transfer |

## Requirements

1. Reset: idle (`busy == 0`, `cs_n == 1`, `done == 0`, `sclk == 0`).
2. `start && !busy` captures `data_in` and asserts `busy` next cycle.
3. While busy, `cs_n` is 0 and `sclk` is the phase bit.
4. MOSI is the current MSB of the shifter (MSB-first).
5. `done` is a single-cycle pulse that coincides with `busy` falling.
6. `done` implies not busy next cycle... actually done is same cycle as busy going 0.

## Toolchain note

Concurrent SVA is lowered to immediate assertions; see `formalsynapse.sva_lower`.
