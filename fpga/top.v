// top.v - the FPGA market maker: UART in -> request -> LMSR core -> reply -> UART out.
//
//   PICO[0] --> uart_rx --> frame_rx --> lmsr_core --> frame_tx --> uart_tx --> PICO[1]
//
// HEX7-6: bid, HEX5-4: ask (hex for now, 00 = pulled), HEX3-0: accepted fills.
// LEDR[0]: kill switch active. LEDG[0]: reply being sent. LEDG[1]: feed paused.
//
// KEY0: restart (same as power-on: d = 0, fills = 0, default config).
// KEY1: kill switch on/off.   KEY3: pause the order feed.   KEY2: resume it.
// Every button press makes the board send a "notice" to the laptop, so the
// program feeding it orders (exchange --demo) can restart, pause or resume.
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

    // The buttons are not in step with the clock, so each goes through two
    // flip-flops before anything uses it. A third remembers the previous
    // value, so "pressed now but not a clock ago" marks the moment of a press.
    reg [3:0] key_s1 = 0, key_s2 = 0, key_s3 = 0;      // 1 = pressed
    always @(posedge clk) begin
        key_s1 <= ~KEY;
        key_s2 <= key_s1;
        key_s3 <= key_s2;
    end
    wire [3:0] key_press = key_s2 & ~key_s3;

    // KEY0 restarts everything while held.
    wire rst = por_rst | key_s2[0];

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
    // ---- who gets the core next ----
    // Normally a request goes straight in. But the core handles one thing at
    // a time and there is one serial line out, so a button event must wait
    // until the core is idle and the previous reply has been handed over; and
    // a request that arrives while a notice is going out waits its turn in
    // the pend_* registers. The host only ever has one request outstanding,
    // so one waiting slot is enough.
    reg         pend_req, pend_kill, pend_pause, pend_resume, paused;
    reg  [7:0]  pend_cmd;
    reg  [15:0] pend_arg;
    wire        ftx_busy;
    wire        can_start = !core_busy && !ftx_busy && !resp_valid;

    reg         core_req;
    reg  [7:0]  core_cmd;
    reg  [15:0] core_arg;
    always @(*) begin
        core_req = 1'b0;
        core_cmd = cmd;
        core_arg = arg;
        if (can_start) begin
            if (pend_req) begin
                core_req = 1'b1; core_cmd = pend_cmd; core_arg = pend_arg;
            end else if (req_valid) begin
                core_req = 1'b1;
            end else if (pend_kill) begin
                core_req = 1'b1; core_cmd = 8'h81;
            end else if (pend_pause) begin
                core_req = 1'b1; core_cmd = 8'h82;
            end else if (pend_resume) begin
                core_req = 1'b1; core_cmd = 8'h83;
            end
        end
    end

    always @(posedge clk) begin
        if (rst) begin
            {pend_req, pend_kill, pend_pause, pend_resume, paused} <= 5'd0;
        end else begin
            // whatever was just handed to the core is no longer waiting
            if (can_start) begin
                if (pend_req)         pend_req    <= 1'b0;
                else if (req_valid)   ;
                else if (pend_kill)   pend_kill   <= 1'b0;
                else if (pend_pause)  pend_pause  <= 1'b0;
                else if (pend_resume) pend_resume <= 1'b0;
            end
            // new arrivals (these win if both happen on the same clock)
            if (req_valid && !(can_start && !pend_req)) begin
                pend_req <= 1'b1;
                pend_cmd <= cmd;
                pend_arg <= arg;
            end
            if (key_press[1]) pend_kill <= 1'b1;
            if (key_press[3]) begin pend_pause  <= 1'b1; paused <= 1'b1; end
            if (key_press[2]) begin pend_resume <= 1'b1; paused <= 1'b0; end
        end
    end

    lmsr_core #(.HEX_FILE(HEX_FILE)) core_i
        (.clk(clk), .rst(rst), .req_valid(core_req), .cmd(core_cmd), .arg(core_arg),
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
    wire       tx_start, tx_busy, tx;
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
    assign LEDG = {2'd0, paused, ftx_busy};
endmodule
