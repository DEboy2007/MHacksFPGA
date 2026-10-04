// uart_rx.v - receives one byte at a time over a serial line (8N1).
//
// The line idles high. A byte is: one low "start" bit, 8 data bits (least
// significant first), one high "stop" bit. Each bit lasts CLKS_PER_BIT clock
// ticks (104 at 12 MHz; top.v passes 417 for its 48 MHz clock). We find the falling edge of the start
// bit, wait half a bit to land in the MIDDLE of it, then sample once per bit
// period so every sample is taken where the line is most stable.
module uart_rx #(parameter CLKS_PER_BIT = 104) (
    input  wire       clk,
    input  wire       rst,
    input  wire       rx,       // serial line in
    output reg  [7:0] data,     // the received byte
    output reg        valid     // high for exactly one clock when data is ready
);
    // rx changes whenever the sender likes, not in step with our clock. Passing
    // it through two flip-flops first ("synchronizer") stops a badly-timed
    // edge from confusing the logic below.
    reg rx_s1, rx_s2;
    always @(posedge clk) begin
        if (rst) {rx_s2, rx_s1} <= 2'b11;       // idle level
        else     {rx_s2, rx_s1} <= {rx_s1, rx};
    end

    localparam IDLE = 2'd0, START = 2'd1, DATA = 2'd2, STOP = 2'd3;
    reg [1:0]  state;
    reg [15:0] cnt;     // clock ticks within the current bit
    reg [2:0]  bit_i;   // which data bit we are on

    always @(posedge clk) begin
        valid <= 1'b0;
        if (rst) begin
            state <= IDLE;
        end else case (state)
            IDLE: begin
                cnt <= 0;
                if (!rx_s2) state <= START;
            end
            START: begin                              // wait to mid start bit
                if (cnt == CLKS_PER_BIT/2 - 1) begin
                    cnt   <= 0;
                    bit_i <= 0;
                    state <= rx_s2 ? IDLE : DATA;     // a glitch, not a start bit
                end else cnt <= cnt + 1;
            end
            DATA: begin                               // sample mid data bit
                if (cnt == CLKS_PER_BIT - 1) begin
                    cnt   <= 0;
                    data  <= {rx_s2, data[7:1]};      // LSB arrives first
                    bit_i <= bit_i + 1;
                    if (bit_i == 3'd7) state <= STOP;
                end else cnt <= cnt + 1;
            end
            STOP: begin                               // sample mid stop bit
                if (cnt == CLKS_PER_BIT - 1) begin
                    valid <= rx_s2;                   // stop bit must be high
                    state <= IDLE;
                end else cnt <= cnt + 1;
            end
        endcase
    end
endmodule
