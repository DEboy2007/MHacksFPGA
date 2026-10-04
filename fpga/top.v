// top.v - the FPGA market maker: UART in -> request -> LMSR core -> reply -> UART out.
//
//   PICO[0] --> uart_rx --> frame_rx --> lmsr_core --> frame_tx --> uart_tx --> PICO[1]
//
// HEX7-6: bid, HEX5-4: ask (hex for now, 00 = pulled), HEX3-0: accepted fills.
// LEDR[0]: kill switch active. LEDG[0]: reply being sent.
// KEY0: restart (same as power-on: d = 0, fills = 0, default config).
module top #(
    parameter CLKS_PER_BIT = 104,       // 12 MHz / 115200 baud
    parameter TIMEOUT      = 120000,    // 10 ms
    parameter HEX_FILE     = "../tables/softplus_tail.hex"
) (
    input  wire       CLOCK_12,
    inout  wire [1:0] PICO,             // PICO[0] = serial in, PICO[1] = serial out
    input  wire [3:0] KEY,              // push buttons, 0 = pressed
    output wire [6:0] HEX0, HEX1, HEX2, HEX3, HEX4, HEX5, HEX6, HEX7,
    output wire [6:0] LEDR,
    output wire [3:0] LEDG
);
    wire clk = CLOCK_12;
    wire por_rst;
    por por_i (.clk(clk), .rst(por_rst));

    // KEY0 restarts everything while held. The button is not in step with the
    // clock, so it goes through two flip-flops before anything uses it.
    reg [1:0] key0_sync = 2'b00;
    always @(posedge clk) key0_sync <= {key0_sync[0], ~KEY[0]};
    wire rst = por_rst | key0_sync[1];

    // ---- bytes in -> request ----
    wire [7:0] rx_data;
    wire       rx_valid;
    uart_rx #(.CLKS_PER_BIT(CLKS_PER_BIT)) rx_i
        (.clk(clk), .rst(rst), .rx(PICO[0]), .data(rx_data), .valid(rx_valid));

    wire        req_valid;
    wire [7:0]  cmd;
    wire [15:0] arg;
    frame_rx #(.TIMEOUT(TIMEOUT)) frx_i
        (.clk(clk), .rst(rst), .byte_in(rx_data), .byte_valid(rx_valid),
         .req_valid(req_valid), .cmd(cmd), .arg(arg));

    // ---- the market maker ----
    wire        resp_valid, kill, core_busy;
    wire [7:0]  status;
    wire [6:0]  bid_px, ask_px;
    wire signed [15:0] d;
    wire [31:0] fills;
    wire [1:0]  lbm6;
    wire [3:0]  ls, hs;
    lmsr_core #(.HEX_FILE(HEX_FILE)) core_i
        (.clk(clk), .rst(rst), .req_valid(req_valid), .cmd(cmd), .arg(arg),
         .resp_valid(resp_valid), .status(status), .bid_px(bid_px), .ask_px(ask_px),
         .d(d), .fills(fills), .lbm6(lbm6), .ls(ls), .hs(hs), .kill(kill),
         .busy(core_busy));

    // ---- compute latency (spec section 8) ----
    // Clocks from uart_rx delivering the request's last byte to the reply
    // being ready. The counter restarts on every received byte, so when the
    // reply appears it holds the time since the last one.
    reg [31:0] lat;
    always @(posedge clk) begin
        if (rst || rx_valid) lat <= 32'd1;
        else                 lat <= lat + 1;
    end

    // ---- reply -> bytes out ----
    wire [7:0] tx_data;
    wire       tx_start, tx_busy, ftx_busy, tx;
    frame_tx ftx_i
        (.clk(clk), .rst(rst), .send(resp_valid), .status(status),
         .bid_px({1'b0, bid_px}), .ask_px({1'b0, ask_px}), .d(d),
         .seq(fills[15:0]), .latency(lat),
         .tx_data(tx_data), .tx_start(tx_start), .tx_busy(tx_busy), .busy(ftx_busy));
    uart_tx #(.CLKS_PER_BIT(CLKS_PER_BIT)) tx_i
        (.clk(clk), .rst(rst), .data(tx_data), .start(tx_start), .tx(tx), .busy(tx_busy));
    assign PICO[1] = tx;

    // ---- board display ----
    hex7seg h7 (.value({1'b0, bid_px[6:4]}), .blank(1'b0), .seg(HEX7));
    hex7seg h6 (.value(bid_px[3:0]),         .blank(1'b0), .seg(HEX6));
    hex7seg h5 (.value({1'b0, ask_px[6:4]}), .blank(1'b0), .seg(HEX5));
    hex7seg h4 (.value(ask_px[3:0]),         .blank(1'b0), .seg(HEX4));
    hex7seg h3 (.value(fills[15:12]),        .blank(1'b0), .seg(HEX3));
    hex7seg h2 (.value(fills[11:8]),         .blank(1'b0), .seg(HEX2));
    hex7seg h1 (.value(fills[7:4]),          .blank(1'b0), .seg(HEX1));
    hex7seg h0 (.value(fills[3:0]),          .blank(1'b0), .seg(HEX0));
    assign LEDR = {6'd0, kill};
    assign LEDG = {3'd0, ftx_busy};
endmodule
