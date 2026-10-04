// echo_top.v - milestone M1: send every received byte straight back.
// HEX1-HEX0 show the last byte (hex), HEX3-HEX2 count bytes received.
module echo_top #(parameter CLKS_PER_BIT = 104) (
    input  wire       CLOCK_12,
    inout  wire [1:0] PICO,     // PICO[0] = serial in, PICO[1] = serial out
    output wire [6:0] HEX0, HEX1, HEX2, HEX3, HEX4, HEX5, HEX6, HEX7,
    output wire [3:0] LEDG
);
    wire rst;
    por por_i (.clk(CLOCK_12), .rst(rst));

    wire [7:0] rx_data;
    wire       rx_valid, tx, tx_busy;
    uart_rx #(.CLKS_PER_BIT(CLKS_PER_BIT)) rx_i
        (.clk(CLOCK_12), .rst(rst), .rx(PICO[0]), .data(rx_data), .valid(rx_valid));
    uart_tx #(.CLKS_PER_BIT(CLKS_PER_BIT)) tx_i
        (.clk(CLOCK_12), .rst(rst), .data(rx_data), .start(rx_valid), .tx(tx), .busy(tx_busy));
    assign PICO[1] = tx;

    reg [7:0] last, count;
    always @(posedge CLOCK_12) begin
        if (rst) begin
            last  <= 0;
            count <= 0;
        end else if (rx_valid) begin
            last  <= rx_data;
            count <= count + 1;
        end
    end

    hex7seg h0 (.value(last[3:0]),  .blank(1'b0), .seg(HEX0));
    hex7seg h1 (.value(last[7:4]),  .blank(1'b0), .seg(HEX1));
    hex7seg h2 (.value(count[3:0]), .blank(1'b0), .seg(HEX2));
    hex7seg h3 (.value(count[7:4]), .blank(1'b0), .seg(HEX3));
    assign {HEX7, HEX6, HEX5, HEX4} = {28{1'b1}};     // off
    assign LEDG = {3'b000, tx_busy};
endmodule
