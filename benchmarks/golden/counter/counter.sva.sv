`ifdef FORMAL
property p_counter_inc;
    @(posedge clk) disable iff (!rst_n)
    (up && !down && count != 4'hF) |=> count == $past(count) + 4'd1;
endproperty
a_counter_inc: assert property (p_counter_inc)
    else $error("Assertion Failed: inc violated at cycle %0t", $time);

property p_counter_dec;
    @(posedge clk) disable iff (!rst_n)
    (down && !up && count != 4'd0) |=> count == $past(count) - 4'd1;
endproperty
a_counter_dec: assert property (p_counter_dec)
    else $error("Assertion Failed: dec violated at cycle %0t", $time);

property p_counter_hold;
    @(posedge clk) disable iff (!rst_n)
    ((up && down) || (!up && !down)) |=> count == $past(count);
endproperty
a_counter_hold: assert property (p_counter_hold)
    else $error("Assertion Failed: hold violated at cycle %0t", $time);

property p_counter_sat_max;
    @(posedge clk) disable iff (!rst_n)
    (up && !down && count == 4'hF) |=> count == 4'hF;
endproperty
a_counter_sat_max: assert property (p_counter_sat_max)
    else $error("Assertion Failed: sat_max violated at cycle %0t", $time);

property p_counter_sat_min;
    @(posedge clk) disable iff (!rst_n)
    (down && !up && count == 4'd0) |=> count == 4'd0;
endproperty
a_counter_sat_min: assert property (p_counter_sat_min)
    else $error("Assertion Failed: sat_min violated at cycle %0t", $time);

c_counter_inc: cover property (@(posedge clk) disable iff (!rst_n) up && !down && count != 4'hF);
c_counter_dec: cover property (@(posedge clk) disable iff (!rst_n) down && !up && count != 4'd0);
c_counter_sat: cover property (@(posedge clk) disable iff (!rst_n) up && !down && count == 4'hF);
`endif
