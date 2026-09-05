`ifdef FORMAL
property p_sync_fifo_count_wr;
    @(posedge clk) disable iff (!rst_n)
    (wr_en && !full && !(rd_en && !empty)) |=> count == $past(count) + 3'd1;
endproperty
a_sync_fifo_count_wr: assert property (p_sync_fifo_count_wr)
    else $error("Assertion Failed: count_wr violated at cycle %0t", $time);

property p_sync_fifo_count_rd;
    @(posedge clk) disable iff (!rst_n)
    (rd_en && !empty && !(wr_en && !full)) |=> count == $past(count) - 3'd1;
endproperty
a_sync_fifo_count_rd: assert property (p_sync_fifo_count_rd)
    else $error("Assertion Failed: count_rd violated at cycle %0t", $time);

property p_sync_fifo_count_both;
    @(posedge clk) disable iff (!rst_n)
    (wr_en && !full && rd_en && !empty) |=> count == $past(count);
endproperty
a_sync_fifo_count_both: assert property (p_sync_fifo_count_both)
    else $error("Assertion Failed: count_both violated at cycle %0t", $time);

property p_sync_fifo_full;
    @(posedge clk) disable iff (!rst_n)
    1'b1 |-> (full == (count == 3'd4));
endproperty
a_sync_fifo_full: assert property (p_sync_fifo_full)
    else $error("Assertion Failed: full violated at cycle %0t", $time);

property p_sync_fifo_empty;
    @(posedge clk) disable iff (!rst_n)
    1'b1 |-> (empty == (count == 3'd0));
endproperty
a_sync_fifo_empty: assert property (p_sync_fifo_empty)
    else $error("Assertion Failed: empty violated at cycle %0t", $time);

property p_sync_fifo_order;
    @(posedge clk) disable iff (!rst_n)
    (wr_en && empty) |=> !empty && (rd_data == $past(wr_data));
endproperty
a_sync_fifo_order: assert property (p_sync_fifo_order)
    else $error("Assertion Failed: order violated at cycle %0t", $time);

c_sync_fifo_wr: cover property (@(posedge clk) disable iff (!rst_n) wr_en && !full);
c_sync_fifo_rd: cover property (@(posedge clk) disable iff (!rst_n) rd_en && !empty);
c_sync_fifo_full: cover property (@(posedge clk) disable iff (!rst_n) full);
`endif
