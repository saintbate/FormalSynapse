// Hand-written SVA for ben-marshall/uart uart_tx (pinned in .github/workflows/gate.yml).
// Port-only. The harness holds resetn low at step 0, so |-> is safe here too; |=> is kept
// because txd_reg lags the FSM by one cycle (start bit shows up at ##1).
//
// Parameters. At the shipped 9600 baud / 50 MHz one bit is 5208 cycles and nothing about frame
// timing is observable in a BMC bound anyone can afford, so every counter mutant survives
// (kill 0/8 at those parameters). The CI job therefore elaborates the DUT with
//   BIT_RATE=25000000 PAYLOAD_BITS=2        (fsyn gate --param ..., yosys chparam)
// which gives CYCLES_PER_BIT=2 (the RTL counts 0..CYCLES_PER_BIT, so 3 clocks per bit) and a
// 4-bit frame. PAYLOAD_BITS has to shrink with the divider: the RTL sizes bit_counter with
// COUNT_REG_LEN = 1+$clog2(CYCLES_PER_BIT), so at CYCLES_PER_BIT < 8 an 8-bit payload can never
// reach payload_done and the frame never ends. That is a real property of this DUT, found by
// the frame-completion assert below, not a harness artefact.
//
// The first three properties hold at any parameters. The frame properties are pinned to the
// values above and FAIL at other parameters; the gate report carries the parameter set so a
// PASS is never read as a statement about 9600 baud. The frame is 12..14 clocks after accept
// because cycle_counter is not cleared in IDLE, so the first start bit is up to two clocks
// short depending on where the previous frame left the counter.
`ifdef FORMAL
property p_uart_tx_idle_high;
    @(posedge clk) disable iff (!resetn)
    !uart_tx_busy |=> uart_txd;
endproperty
a_uart_tx_idle_high: assert property (p_uart_tx_idle_high)
    else $error("Assertion Failed: idle line not high at cycle %0t", $time);

property p_uart_tx_en_busy;
    @(posedge clk) disable iff (!resetn)
    (!uart_tx_busy && uart_tx_en) |=> uart_tx_busy;
endproperty
a_uart_tx_en_busy: assert property (p_uart_tx_en_busy)
    else $error("Assertion Failed: enable did not start a frame at cycle %0t", $time);

property p_uart_tx_start_bit;
    @(posedge clk) disable iff (!resetn)
    (!uart_tx_busy && uart_tx_en) |=> ##1 !uart_txd;
endproperty
a_uart_tx_start_bit: assert property (p_uart_tx_start_bit)
    else $error("Assertion Failed: start bit missing at cycle %0t", $time);

// ---- frame timing; valid for BIT_RATE=25000000 PAYLOAD_BITS=2 (CLK_HZ=50000000) only ----

// A frame (start + 2 data + stop, 3 clocks each) keeps the transmitter busy for at least 12
// clocks after the accepting edge.
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

c_uart_tx_idle: cover property (@(posedge clk) disable iff (!resetn) !uart_tx_busy);
c_uart_tx_start: cover property (@(posedge clk) disable iff (!resetn) !uart_tx_busy && uart_tx_en);
`endif
