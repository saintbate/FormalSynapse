// Hand-written SVA for ben-marshall/uart uart_tx (pinned in .github/workflows/gate.yml).
// Port-only, BMC depth 20. |=> so the first sample is after a clock (txd_reg init is 0).
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
