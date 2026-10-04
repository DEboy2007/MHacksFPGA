// uart_tx.v - sends one byte over a serial line (8N1).
//
// Pulse `start` with `data` while `busy` is low. The byte is loaded into a
// 10-bit shift register as {stop, data, start} and shifted out one bit every
// CLKS_PER_BIT clocks.
module uart_tx #(parameter CLKS_PER_BIT = 104) (
    input  wire       clk,
    input  wire       rst,
    input  wire [7:0] data,
    input  wire       start,
    output reg        tx,       // serial line out (idles high)
    output wire       busy
);
    reg [9:0]  shift;
    reg [3:0]  bits_left;       // 0 = idle
    reg [15:0] cnt;

    assign busy = (bits_left != 0);

    always @(posedge clk) begin
        if (rst) begin
            tx        <= 1'b1;
            bits_left <= 0;
        end else if (!busy) begin
            tx <= 1'b1;
            if (start) begin
                shift     <= {1'b1, data, 1'b0};
                bits_left <= 4'd10;
                cnt       <= 0;
                tx        <= 1'b0;                    // start bit goes out now
            end
        end else if (cnt == CLKS_PER_BIT - 1) begin
            cnt       <= 0;
            shift     <= {1'b1, shift[9:1]};
            bits_left <= bits_left - 1;
            tx        <= (bits_left == 4'd1) ? 1'b1 : shift[1];   // next bit
        end else begin
            cnt <= cnt + 1;
        end
    end
endmodule
