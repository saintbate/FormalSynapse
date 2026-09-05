`ifdef FORMAL
property p_shift_register_load;
    @(posedge clk) disable iff (!rst_n)
    load |=> q == $past(pdata);
endproperty
a_shift_register_load: assert property (p_shift_register_load)
    else $error("Assertion Failed: load violated at cycle %0t", $time);

property p_shift_register_shift;
    @(posedge clk) disable iff (!rst_n)
    (!load && shift) |=> q == {$past(sin), $past(q[7:1])};
endproperty
a_shift_register_shift: assert property (p_shift_register_shift)
    else $error("Assertion Failed: shift violated at cycle %0t", $time);

property p_shift_register_hold;
    @(posedge clk) disable iff (!rst_n)
    (!load && !shift) |=> q == $past(q);
endproperty
a_shift_register_hold: assert property (p_shift_register_hold)
    else $error("Assertion Failed: hold violated at cycle %0t", $time);

c_shift_register_load: cover property (@(posedge clk) disable iff (!rst_n) load);
c_shift_register_shift: cover property (@(posedge clk) disable iff (!rst_n) !load && shift);
`endif
