`ifdef FORMAL
property p_priority_arbiter_onehot0;
    @(posedge clk) disable iff (!rst_n)
    1'b1 |-> $onehot0(grant);
endproperty
a_priority_arbiter_onehot0: assert property (p_priority_arbiter_onehot0)
    else $error("Assertion Failed: onehot0 violated at cycle %0t", $time);

property p_priority_arbiter_implies_req;
    @(posedge clk) disable iff (!rst_n)
    1'b1 |=> (grant & ~$past(req)) == 4'd0;
endproperty
a_priority_arbiter_implies_req: assert property (p_priority_arbiter_implies_req)
    else $error("Assertion Failed: implies_req violated at cycle %0t", $time);

property p_priority_arbiter_hi3;
    @(posedge clk) disable iff (!rst_n)
    req[3] |=> grant == 4'b1000;
endproperty
a_priority_arbiter_hi3: assert property (p_priority_arbiter_hi3)
    else $error("Assertion Failed: hi3 violated at cycle %0t", $time);

property p_priority_arbiter_hi2;
    @(posedge clk) disable iff (!rst_n)
    (req[2] && !req[3]) |=> grant == 4'b0100;
endproperty
a_priority_arbiter_hi2: assert property (p_priority_arbiter_hi2)
    else $error("Assertion Failed: hi2 violated at cycle %0t", $time);

property p_priority_arbiter_any;
    @(posedge clk) disable iff (!rst_n)
    (req != 4'd0) |=> (grant != 4'd0);
endproperty
a_priority_arbiter_any: assert property (p_priority_arbiter_any)
    else $error("Assertion Failed: any violated at cycle %0t", $time);

c_priority_arbiter_req3: cover property (@(posedge clk) disable iff (!rst_n) req[3]);
c_priority_arbiter_req0: cover property (@(posedge clk) disable iff (!rst_n) req[0] && req[3:1] == 3'd0);
`endif
