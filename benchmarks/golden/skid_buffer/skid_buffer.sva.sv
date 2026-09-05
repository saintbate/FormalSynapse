`ifdef FORMAL
property p_skid_buffer_ready;
    @(posedge clk) disable iff (!rst_n)
    1'b1 |-> (s_ready == !buf_valid);
endproperty
a_skid_buffer_ready: assert property (p_skid_buffer_ready)
    else $error("Assertion Failed: ready violated at cycle %0t", $time);

property p_skid_buffer_valid;
    @(posedge clk) disable iff (!rst_n)
    1'b1 |-> (m_valid == (buf_valid || s_valid));
endproperty
a_skid_buffer_valid: assert property (p_skid_buffer_valid)
    else $error("Assertion Failed: valid violated at cycle %0t", $time);

property p_skid_buffer_stable;
    @(posedge clk) disable iff (!rst_n)
    (m_valid && !m_ready) |=> $stable(m_data) && m_valid;
endproperty
a_skid_buffer_stable: assert property (p_skid_buffer_stable)
    else $error("Assertion Failed: stable violated at cycle %0t", $time);

property p_skid_buffer_capture;
    @(posedge clk) disable iff (!rst_n)
    (s_valid && s_ready && !m_ready) |=> buf_valid && (m_data == $past(s_data));
endproperty
a_skid_buffer_capture: assert property (p_skid_buffer_capture)
    else $error("Assertion Failed: capture violated at cycle %0t", $time);

c_skid_buffer_accept: cover property (@(posedge clk) disable iff (!rst_n) s_valid && s_ready);
c_skid_buffer_stall: cover property (@(posedge clk) disable iff (!rst_n) m_valid && !m_ready);
`endif
