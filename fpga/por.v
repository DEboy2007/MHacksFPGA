// por.v - power-on reset.
// iCE40 flip-flops all start at 0 when the bitstream loads. This counter runs
// for 16 clocks and holds `rst` high meanwhile, so every other module gets a
// clean synchronous reset and can set registers to non-zero defaults.
module por (
    input  wire clk,
    output wire rst
);
    reg [4:0] cnt = 0;
    assign rst = !cnt[4];
    always @(posedge clk) if (!cnt[4]) cnt <= cnt + 1;
endmodule
