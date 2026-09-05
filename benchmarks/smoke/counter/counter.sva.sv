`ifdef FORMAL
property p_counter_inc;
    @(posedge clk) disable iff (!rst_n)
    en |=> count == $past(count) + 4'd1;
endproperty
a_counter_inc: assert property (p_counter_inc)
    else $error("Assertion Failed: inc violated at cycle %0t", $time);

property p_counter_hold;
    @(posedge clk) disable iff (!rst_n)
    !en |=> count == $past(count);
endproperty
a_counter_hold: assert property (p_counter_hold)
    else $error("Assertion Failed: hold violated at cycle %0t", $time);

c_counter_en: cover property (@(posedge clk) disable iff (!rst_n) en);
c_counter_wrap: cover property (@(posedge clk) disable iff (!rst_n) en && count == 4'hF);
`endif
