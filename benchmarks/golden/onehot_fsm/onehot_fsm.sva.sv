`ifdef FORMAL
property p_onehot_fsm_onehot;
    @(posedge clk) disable iff (!rst_n)
    1'b1 |-> $onehot(state);
endproperty
a_onehot_fsm_onehot: assert property (p_onehot_fsm_onehot)
    else $error("Assertion Failed: onehot violated at cycle %0t", $time);

property p_onehot_fsm_idle;
    @(posedge clk) disable iff (!rst_n)
    (state == 4'b0001) |=> (state == 4'b0001) || (state == 4'b0010);
endproperty
a_onehot_fsm_idle: assert property (p_onehot_fsm_idle)
    else $error("Assertion Failed: idle violated at cycle %0t", $time);

property p_onehot_fsm_reqst;
    @(posedge clk) disable iff (!rst_n)
    (state == 4'b0010) |=> (state == 4'b0010) || (state == 4'b0100);
endproperty
a_onehot_fsm_reqst: assert property (p_onehot_fsm_reqst)
    else $error("Assertion Failed: reqst violated at cycle %0t", $time);

property p_onehot_fsm_ack;
    @(posedge clk) disable iff (!rst_n)
    (state == 4'b0100) |=> (state == 4'b1000);
endproperty
a_onehot_fsm_ack: assert property (p_onehot_fsm_ack)
    else $error("Assertion Failed: ack violated at cycle %0t", $time);

property p_onehot_fsm_done_st;
    @(posedge clk) disable iff (!rst_n)
    (state == 4'b1000) |=> (state == 4'b0001);
endproperty
a_onehot_fsm_done_st: assert property (p_onehot_fsm_done_st)
    else $error("Assertion Failed: done_st violated at cycle %0t", $time);

property p_onehot_fsm_req;
    @(posedge clk) disable iff (!rst_n)
    1'b1 |-> (req == (state == 4'b0010));
endproperty
a_onehot_fsm_req: assert property (p_onehot_fsm_req)
    else $error("Assertion Failed: req violated at cycle %0t", $time);

// done is the S_DONE decode (the req property above covers S_REQ).
property p_onehot_fsm_done;
    @(posedge clk) disable iff (!rst_n)
    1'b1 |-> (done == (state == 4'b1000));
endproperty
a_onehot_fsm_done: assert property (p_onehot_fsm_done)
    else $error("Assertion Failed: done decode violated at cycle %0t", $time);

// Spec 1: reset enters IDLE.
property p_onehot_fsm_reset;
    @(posedge clk) disable iff (!rst_n)
    $rose(rst_n) |-> (state == 4'b0001);
endproperty
a_onehot_fsm_reset: assert property (p_onehot_fsm_reset)
    else $error("Assertion Failed: reset state violated at cycle %0t", $time);

c_onehot_fsm_start: cover property (@(posedge clk) disable iff (!rst_n) start && (state == 4'b0001));
c_onehot_fsm_done: cover property (@(posedge clk) disable iff (!rst_n) done);
`endif
