# skid_buffer — valid/ready pipeline register

## Interface

| Signal  | Dir | Width | Description |
|---------|-----|-------|-------------|
| clk     | in  | 1     | Clock |
| rst_n   | in  | 1     | Active-low async reset |
| s_valid | in  | 1     | Slave valid |
| s_ready | out | 1     | Slave ready (`!buf_valid`) |
| s_data  | in  | 8     | Slave data |
| m_valid | out | 1     | Master valid |
| m_ready | in  | 1     | Master ready |
| m_data  | out | 8     | Master data |

## Requirements

1. Reset: buffer empty, `s_ready == 1`, `m_valid == s_valid`.
2. `s_ready` is high iff the skid register is empty.
3. A beat is accepted when `s_valid && s_ready`.
4. When a beat is accepted and the master is not ready, data is captured and `m_valid` stays high.
5. While `m_valid && !m_ready`, `m_data` is stable.
6. No silent drop: accepted data is presented on `m_data` until taken.

## Toolchain note

Concurrent SVA is lowered to immediate assertions; see `formalsynapse.sva_lower`.
