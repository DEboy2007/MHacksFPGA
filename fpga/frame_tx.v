// frame_tx.v - sends one 13-byte reply (docs/spec.md section 6).
//
// When `send` pulses, the 12 payload bytes are copied into one wide shift
// register. Each time the UART is free we hand it the bottom byte and shift
// the rest down. The 13th byte is the xor of the 12 before it.
module frame_tx (
    input  wire        clk,
    input  wire        rst,
    input  wire        send,
    input  wire [7:0]  status,
    input  wire [7:0]  bid_px,
    input  wire [7:0]  ask_px,
    input  wire [15:0] d,
    input  wire [15:0] seq,
    input  wire [31:0] latency,
    // to uart_tx
    output reg  [7:0]  tx_data,
    output reg         tx_start,
    input  wire        tx_busy,
    output wire        busy
);
    reg [95:0] shift;      // byte 0 in the low 8 bits
    reg [3:0]  left;       // bytes still to hand over, counting the checksum
    reg [7:0]  xsum;

    assign busy = (left != 0);

    always @(posedge clk) begin
        tx_start <= 1'b0;
        if (rst) begin
            left <= 0;
        end else if (!busy) begin
            if (send) begin
                shift <= {latency, seq, d, ask_px, bid_px, status, 8'h5A};
                left  <= 4'd13;
                xsum  <= 8'd0;
            end
        end else if (!tx_busy && !tx_start) begin   // !tx_start: busy rises a clock late
            tx_data  <= (left == 4'd1) ? xsum : shift[7:0];
            tx_start <= 1'b1;
            xsum     <= xsum ^ shift[7:0];
            shift    <= {8'd0, shift[95:8]};
            left     <= left - 1;
        end
    end
endmodule
