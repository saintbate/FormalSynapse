`ifdef FORMAL
property p_edge_detector_rose;
    @(posedge clk) disable iff (!rst_n)
    1'b1 |-> (pulse == (din && !din_q));
endproperty
a_edge_detector_rose: assert property (p_edge_detector_rose)
    else $error("Assertion Failed: rose violated at cycle %0t", $time);

property p_edge_detector_pulse;
    @(posedge clk) disable iff (!rst_n)
    $past(rst_n) && $rose(din) |-> pulse;
endproperty
a_edge_detector_pulse: assert property (p_edge_detector_pulse)
    else $error("Assertion Failed: pulse violated at cycle %0t", $time);

property p_edge_detector_stable;
    @(posedge clk) disable iff (!rst_n)
    $past(rst_n) && $stable(din) |-> !pulse;
endproperty
a_edge_detector_stable: assert property (p_edge_detector_stable)
    else $error("Assertion Failed: stable violated at cycle %0t", $time);

// Spec 1: reset clears the delay flop, so in the first cycle out of reset pulse == din.
property p_edge_detector_reset;
    @(posedge clk) disable iff (!rst_n)
    $rose(rst_n) |-> (!din_q && pulse == din);
endproperty
a_edge_detector_reset: assert property (p_edge_detector_reset)
    else $error("Assertion Failed: reset value violated at cycle %0t", $time);

c_edge_detector_rise: cover property (@(posedge clk) disable iff (!rst_n) din && !din_q);
`endif
