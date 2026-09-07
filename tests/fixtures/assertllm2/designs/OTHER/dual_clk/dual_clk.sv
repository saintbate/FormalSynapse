module dual_clk (
    input  logic clk,
    input  logic rst_n,
    output logic q
);
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            q <= 1'b0;
        else
            q <= 1'b1;
    end
    always @(negedge clk) begin
        q <= ~q;
    end
endmodule
