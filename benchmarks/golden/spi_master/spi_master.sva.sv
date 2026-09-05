`ifdef FORMAL
property p_spi_master_start;
    @(posedge clk) disable iff (!rst_n)
    (start && !busy) |=> busy && (mosi == $past(data_in[7]));
endproperty
a_spi_master_start: assert property (p_spi_master_start)
    else $error("Assertion Failed: start violated at cycle %0t", $time);

property p_spi_master_cs;
    @(posedge clk) disable iff (!rst_n)
    1'b1 |-> (cs_n == !busy);
endproperty
a_spi_master_cs: assert property (p_spi_master_cs)
    else $error("Assertion Failed: cs violated at cycle %0t", $time);

property p_spi_master_sclk_idle;
    @(posedge clk) disable iff (!rst_n)
    !busy |-> !sclk;
endproperty
a_spi_master_sclk_idle: assert property (p_spi_master_sclk_idle)
    else $error("Assertion Failed: sclk_idle violated at cycle %0t", $time);

property p_spi_master_done_pulse;
    @(posedge clk) disable iff (!rst_n)
    done |=> !done;
endproperty
a_spi_master_done_pulse: assert property (p_spi_master_done_pulse)
    else $error("Assertion Failed: done_pulse violated at cycle %0t", $time);

property p_spi_master_done_idle;
    @(posedge clk) disable iff (!rst_n)
    done |-> !busy;
endproperty
a_spi_master_done_idle: assert property (p_spi_master_done_idle)
    else $error("Assertion Failed: done_idle violated at cycle %0t", $time);

c_spi_master_start: cover property (@(posedge clk) disable iff (!rst_n) start && !busy);
c_spi_master_done: cover property (@(posedge clk) disable iff (!rst_n) done);
`endif
