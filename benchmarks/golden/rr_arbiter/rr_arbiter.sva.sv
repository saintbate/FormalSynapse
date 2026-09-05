`ifdef FORMAL
property p_rr_arbiter_onehot0;
    @(posedge clk) disable iff (!rst_n)
    1'b1 |-> $onehot0(grant);
endproperty
a_rr_arbiter_onehot0: assert property (p_rr_arbiter_onehot0)
    else $error("Assertion Failed: onehot0 violated at cycle %0t", $time);

property p_rr_arbiter_implies_req;
    @(posedge clk) disable iff (!rst_n)
    1'b1 |=> (grant & ~$past(req)) == 4'd0;
endproperty
a_rr_arbiter_implies_req: assert property (p_rr_arbiter_implies_req)
    else $error("Assertion Failed: implies_req violated at cycle %0t", $time);

property p_rr_arbiter_any;
    @(posedge clk) disable iff (!rst_n)
    (req != 4'd0) |=> (grant != 4'd0);
endproperty
a_rr_arbiter_any: assert property (p_rr_arbiter_any)
    else $error("Assertion Failed: any violated at cycle %0t", $time);

property p_rr_arbiter_fair0;
    @(posedge clk) disable iff (!rst_n)
    req[0] |=> ##[0:3] (grant[0] || !req[0]);
endproperty
a_rr_arbiter_fair0: assert property (p_rr_arbiter_fair0)
    else $error("Assertion Failed: fair0 violated at cycle %0t", $time);

property p_rr_arbiter_fair1;
    @(posedge clk) disable iff (!rst_n)
    req[1] |=> ##[0:3] (grant[1] || !req[1]);
endproperty
a_rr_arbiter_fair1: assert property (p_rr_arbiter_fair1)
    else $error("Assertion Failed: fair1 violated at cycle %0t", $time);

property p_rr_arbiter_fair2;
    @(posedge clk) disable iff (!rst_n)
    req[2] |=> ##[0:3] (grant[2] || !req[2]);
endproperty
a_rr_arbiter_fair2: assert property (p_rr_arbiter_fair2)
    else $error("Assertion Failed: fair2 violated at cycle %0t", $time);

property p_rr_arbiter_fair3;
    @(posedge clk) disable iff (!rst_n)
    req[3] |=> ##[0:3] (grant[3] || !req[3]);
endproperty
a_rr_arbiter_fair3: assert property (p_rr_arbiter_fair3)
    else $error("Assertion Failed: fair3 violated at cycle %0t", $time);

c_rr_arbiter_req: cover property (@(posedge clk) disable iff (!rst_n) req != 4'd0);
`endif
