// Frame-timing SVA for ben-marshall/uart uart_tx. Valid ONLY for
//   BIT_RATE=25000000 PAYLOAD_BITS=2        (CLK_HZ=50000000)
// and gated with exactly that override:  fsyn gate ... --param BIT_RATE=25000000 --param PAYLOAD_BITS=2
// The report, gate.json and the CI step summary carry the parameter set, so a PASS here is never
// read as a statement about 9600 baud. At other parameters these asserts FAIL (checked: at the
// shipped values a_uart_tx_frame_done fails at step 16).
//
// Why an override at all: at 9600 baud one bit is 5208 clocks and no affordable BMC bound sees a
// frame, so every counter mutant survives the parameter-agnostic block (uart_tx.sva.sv, 0/8).
// Under the override CYCLES_PER_BIT=2, the RTL counts 0..CYCLES_PER_BIT so a bit is 3 clocks, and a
// start + 2 data + stop frame is 12 clocks. PAYLOAD_BITS has to shrink with the divider: the RTL
// sizes bit_counter with COUNT_REG_LEN = 1+$clog2(CYCLES_PER_BIT), so at CYCLES_PER_BIT < 8 an
// 8-bit payload can never reach payload_done and the frame never ends. That is a property of this
// DUT, found by a_uart_tx_frame_done, not a harness artefact.
//
// The frame is 12..14 clocks after accept rather than a fixed length because cycle_counter is not
// cleared in IDLE, so the first start bit is up to two clocks short depending on where the
// previous frame left the counter. Gate result at depth 30: prove PASS, cover PASS, kill 7/8; the
// survivor flips the data-latch condition (fsm_state == FSM_IDLE && uart_tx_en), which a port-only
// block that does not check data cannot see.
`ifdef FORMAL
// A frame keeps the transmitter busy for at least 12 clocks after the accepting edge.
property p_uart_tx_frame_min;
    @(posedge clk) disable iff (!resetn)
    (!uart_tx_busy && uart_tx_en) |=> ##11 uart_tx_busy;
endproperty
a_uart_tx_frame_min: assert property (p_uart_tx_frame_min)
    else $error("Assertion Failed: frame ended early at cycle %0t", $time);

// ... and is over, with the line back at the idle level, 14 clocks after it.
property p_uart_tx_frame_done;
    @(posedge clk) disable iff (!resetn)
    (!uart_tx_busy && uart_tx_en) |=> ##13 (!uart_tx_busy && uart_txd);
endproperty
a_uart_tx_frame_done: assert property (p_uart_tx_frame_done)
    else $error("Assertion Failed: frame did not complete at cycle %0t", $time);

c_uart_tx_frame_start: cover property (@(posedge clk) disable iff (!resetn) !uart_tx_busy && uart_tx_en);
`endif
