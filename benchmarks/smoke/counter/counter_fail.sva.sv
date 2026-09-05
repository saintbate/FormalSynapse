`ifdef FORMAL
// Deliberately false: claims enable never increments the counter.
property p_counter_never_inc;
    @(posedge clk) disable iff (!rst_n)
    en |=> count == $past(count);
endproperty
a_counter_never_inc: assert property (p_counter_never_inc)
    else $error("Assertion Failed: never_inc (expected FAIL) at cycle %0t", $time);
`endif
