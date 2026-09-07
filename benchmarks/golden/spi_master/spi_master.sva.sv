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

// Spec 1: reset is idle.
property p_spi_master_reset;
    @(posedge clk) disable iff (!rst_n)
    $rose(rst_n) |-> (!busy && cs_n && !done && !sclk && !phase && bit_cnt == 4'd0);
endproperty
a_spi_master_reset: assert property (p_spi_master_reset)
    else $error("Assertion Failed: reset state violated at cycle %0t", $time);

// Spec 3: sclk is clk/2 while busy: the phase bit toggles every cycle.
property p_spi_master_sclk_toggle;
    @(posedge clk) disable iff (!rst_n)
    busy |=> (phase != $past(phase)) || !busy;
endproperty
a_spi_master_sclk_toggle: assert property (p_spi_master_sclk_toggle)
    else $error("Assertion Failed: sclk did not toggle at cycle %0t", $time);

// Spec 4: MSB-first: on every sclk high phase the shifter advances by one bit.
property p_spi_master_shift;
    @(posedge clk) disable iff (!rst_n)
    (busy && phase) |=> mosi == $past(shifter[6]);
endproperty
a_spi_master_shift: assert property (p_spi_master_shift)
    else $error("Assertion Failed: shift order violated at cycle %0t", $time);

// 8 bits at 2 clocks each: busy for 16 cycles after the accepting edge, then done with busy low.
property p_spi_master_frame_busy;
    @(posedge clk) disable iff (!rst_n)
    (start && !busy) |=> ##15 busy;
endproperty
a_spi_master_frame_busy: assert property (p_spi_master_frame_busy)
    else $error("Assertion Failed: frame ended early at cycle %0t", $time);

property p_spi_master_frame_done;
    @(posedge clk) disable iff (!rst_n)
    (start && !busy) |=> ##16 (done && !busy);
endproperty
a_spi_master_frame_done: assert property (p_spi_master_frame_done)
    else $error("Assertion Failed: frame did not complete at cycle %0t", $time);

c_spi_master_start: cover property (@(posedge clk) disable iff (!rst_n) start && !busy);
c_spi_master_done: cover property (@(posedge clk) disable iff (!rst_n) done);
`endif
