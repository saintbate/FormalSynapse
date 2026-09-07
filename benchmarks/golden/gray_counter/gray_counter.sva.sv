`ifdef FORMAL
property p_gray_counter_enc;
    @(posedge clk) disable iff (!rst_n)
    1'b1 |-> (gray == (bin ^ (bin >> 1)));
endproperty
a_gray_counter_enc: assert property (p_gray_counter_enc)
    else $error("Assertion Failed: enc violated at cycle %0t", $time);

property p_gray_counter_inc;
    @(posedge clk) disable iff (!rst_n)
    en |=> bin == $past(bin) + 4'd1;
endproperty
a_gray_counter_inc: assert property (p_gray_counter_inc)
    else $error("Assertion Failed: inc violated at cycle %0t", $time);

property p_gray_counter_onebit;
    @(posedge clk) disable iff (!rst_n)
    en |=> $countones(gray ^ $past(gray)) == 3'd1;
endproperty
a_gray_counter_onebit: assert property (p_gray_counter_onebit)
    else $error("Assertion Failed: onebit violated at cycle %0t", $time);

// Spec 1: reset leaves bin == 0 and gray == 0.
property p_gray_counter_reset;
    @(posedge clk) disable iff (!rst_n)
    $rose(rst_n) |-> (bin == 4'd0 && gray == 4'd0);
endproperty
a_gray_counter_reset: assert property (p_gray_counter_reset)
    else $error("Assertion Failed: reset value violated at cycle %0t", $time);

c_gray_counter_en: cover property (@(posedge clk) disable iff (!rst_n) en);
`endif
