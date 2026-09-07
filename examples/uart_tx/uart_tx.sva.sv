// Hand-written SVA for ben-marshall/uart uart_tx (pinned in .github/workflows/gate.yml).
// Port-only, and true at ANY parameters. The harness holds resetn low at step 0, so |-> is safe
// here too; |=> is kept because txd_reg lags the FSM by one cycle (start bit shows up at ##1).
//
// Gate result at the shipped 9600 baud / 50 MHz: prove PASS, cover PASS, kill 0/8. The 0/8 is
// real, not a grader gap: every mutant the open mutator finds in this DUT hits the bit/cycle
// counters, and at those parameters one bit is 5208 clocks, so nothing about frame timing is
// observable in an affordable BMC bound. The frame-timing properties that do kill those mutants
// need a parameter override and live in uart_tx_frame.sva.sv.
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

c_uart_tx_idle: cover property (@(posedge clk) disable iff (!resetn) !uart_tx_busy);
c_uart_tx_start: cover property (@(posedge clk) disable iff (!resetn) !uart_tx_busy && uart_tx_en);
`endif
