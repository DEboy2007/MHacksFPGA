// frame_rx.v - turns received bytes into requests (docs/spec.md section 6).
//
// Request frame: 0xA5, cmd, arg low, arg high, xor-of-the-first-four.
// We wait for 0xA5, collect four more bytes, and raise req_valid for one
// clock if the checksum is right and cmd is one we know. Anything else is
// dropped silently. If a frame stalls for TIMEOUT clocks (10 ms) we give up
// on it and go back to waiting for 0xA5, so one lost byte cannot leave us
// permanently out of step.
module frame_rx #(parameter TIMEOUT = 120000) (
    input  wire        clk,
    input  wire        rst,
    input  wire [7:0]  byte_in,
    input  wire        byte_valid,
    output reg         req_valid,
    output reg  [7:0]  cmd,
    output reg  [15:0] arg
);
    reg [2:0]  idx;        // which byte of the frame comes next (0 = waiting)
    reg [7:0]  xsum;       // running xor of the bytes so far
    reg [19:0] idle;       // clocks since the last byte

    wire cmd_known = (cmd >= 8'd1) && (cmd <= 8'd6);

    always @(posedge clk) begin
        req_valid <= 1'b0;
        if (rst) begin
            idx  <= 0;
            idle <= 0;
        end else if (byte_valid) begin
            idle <= 0;
            xsum <= xsum ^ byte_in;
            case (idx)
                3'd0: if (byte_in == 8'hA5) begin
                          idx  <= 3'd1;
                          xsum <= 8'hA5;
                      end
                3'd1: begin cmd       <= byte_in; idx <= 3'd2; end
                3'd2: begin arg[7:0]  <= byte_in; idx <= 3'd3; end
                3'd3: begin arg[15:8] <= byte_in; idx <= 3'd4; end
                default: begin
                    req_valid <= (xsum == byte_in) && cmd_known;
                    idx       <= 3'd0;
                end
            endcase
        end else if (idx != 0) begin
            if (idle == TIMEOUT - 1) idx <= 0;
            else                     idle <= idle + 1;
        end
    end
endmodule
