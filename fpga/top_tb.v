// top_tb.v - M3 simulation: talk to the whole design over its serial pins,
// exactly as the Mac will, and compare every reply byte with the golden model.
//
// Two copies of the design are tested one after the other:
//   [0] fast: 8 clocks per bit, so all 6057 vectors run in reasonable time,
//             followed by the bad-frame and timeout cases.
//   [1] real: 104 clocks per bit, driven at a true 115200 baud (which is not
//             exactly 104 clocks), for the first 60 vectors.
`timescale 1ns/1ps
module top_tb;
    localparam NVEC = 6057;
    localparam real CLK_NS = 83.334;

    reg clk = 0;
    always #41.667 clk = ~clk;

    reg  [1:0] host_tx = 2'b11;
    wire [1:0] pico_f, pico_r;
    assign pico_f[0] = host_tx[0];
    assign pico_r[0] = host_tx[1];
    wire [1:0] dut_tx = {pico_r[1], pico_f[1]};

    reg [3:0] key = 4'b1111;              // buttons are 0 when pressed
    reg [17:0] sw = 18'd0;
    wire [6:0] hex_f [0:7], hex_r [0:7];
    wire [6:0] ledr_f, ledr_r;
    wire [3:0] ledg_f, ledg_r;
    top #(.CLKS_PER_BIT(8), .TIMEOUT(400)) dut_fast
        (.CLOCK_12(clk), .PICO(pico_f), .KEY(key),
         .SW(sw),
         .HEX0(hex_f[0]), .HEX1(hex_f[1]), .HEX2(hex_f[2]), .HEX3(hex_f[3]),
         .HEX4(hex_f[4]), .HEX5(hex_f[5]), .HEX6(hex_f[6]), .HEX7(hex_f[7]),
         .LEDR(ledr_f), .LEDG(ledg_f));
    top dut_real
        (.CLOCK_12(clk), .PICO(pico_r), .KEY(4'b1111),
         .SW(sw),
         .HEX0(hex_r[0]), .HEX1(hex_r[1]), .HEX2(hex_r[2]), .HEX3(hex_r[3]),
         .HEX4(hex_r[4]), .HEX5(hex_r[5]), .HEX6(hex_r[6]), .HEX7(hex_r[7]),
         .LEDR(ledr_r), .LEDG(ledg_r));

    real bit_ns [0:1];

    task send_byte(input integer sel, input [7:0] b);
        integer k;
        begin
            host_tx[sel] = 0; #(bit_ns[sel]);
            for (k = 0; k < 8; k = k + 1) begin host_tx[sel] = b[k]; #(bit_ns[sel]); end
            host_tx[sel] = 1; #(bit_ns[sel]);
        end
    endtask

    task send_frame(input integer sel, input [7:0] c, input [15:0] a, input [7:0] xor_flip);
        begin
            send_byte(sel, 8'hA5);
            send_byte(sel, c);
            send_byte(sel, a[7:0]);
            send_byte(sel, a[15:8]);
            send_byte(sel, (8'hA5 ^ c ^ a[7:0] ^ a[15:8]) ^ xor_flip);
        end
    endtask

    // Waits up to 40 bit times for a start bit. got = 0 means nothing came.
    task recv_byte(input integer sel, output [7:0] b, output got);
        integer k, polls;
        begin
            polls = 0;
            while (dut_tx[sel] !== 1'b0 && polls < 320) begin
                #(bit_ns[sel] / 8); polls = polls + 1;
            end
            got = (dut_tx[sel] === 1'b0);
            if (got) begin
                #(bit_ns[sel] * 1.5);
                for (k = 0; k < 8; k = k + 1) begin b[k] = dut_tx[sel]; #(bit_ns[sel]); end
            end
        end
    endtask

    reg [79:0] vec [0:NVEC-1];
    integer errors [0:1];
    integer lat    [0:1];

    // Send one request, check the 13-byte reply against `want`
    // (status, bid, ask, d, seq). expect_reply = 0 checks that nothing comes back.
    task transact(input integer sel, input integer tag, input [7:0] c, input [15:0] a,
                  input [7:0] xor_flip, input expect_reply, input [55:0] want);
        reg [7:0]  r [0:12];
        reg [7:0]  x;
        reg        got;
        reg [31:0] l;
        integer    n;
        begin
            send_frame(sel, c, a, xor_flip);
            recv_byte(sel, r[0], got);
            if (!expect_reply) begin
                if (got) begin
                    $display("FAIL dut %0d #%0d: got a reply to a frame that should be dropped", sel, tag);
                    errors[sel] = errors[sel] + 1;
                end
            end else if (!got) begin
                $display("FAIL dut %0d #%0d: no reply", sel, tag);
                errors[sel] = errors[sel] + 1;
            end else begin
                for (n = 1; n < 13; n = n + 1) recv_byte(sel, r[n], got);
                x = 0;
                for (n = 0; n < 13; n = n + 1) x = x ^ r[n];
                l = {r[11], r[10], r[9], r[8]};
                if (lat[sel] < 0) lat[sel] = l;
                if (r[0] !== 8'h5A || x !== 8'h00 || l !== lat[sel] ||
                    {r[1], r[2], r[3], r[5], r[4], r[7], r[6]} !== want) begin
                    $display("FAIL dut %0d #%0d cmd %0d arg %0d: sync=%h xor=%h lat=%0d got=%h want=%h",
                             sel, tag, c, a, r[0], x, l,
                             {r[1], r[2], r[3], r[5], r[4], r[7], r[6]}, want);
                    errors[sel] = errors[sel] + 1;
                end
            end
            if (errors[sel] > 10) $fatal(1, "too many errors");
        end
    endtask

    integer i, j;
    reg [55:0] last;

    initial begin
        $readmemh("../golden/vectors.hex", vec);
        bit_ns[0] = 8 * CLK_NS;
        bit_ns[1] = 8680.556;
        errors[0] = 0; errors[1] = 0; lat[0] = -1; lat[1] = -1;
        #100000;
        // (One after the other, not in parallel: the tasks above keep their
        // variables between calls, so two callers at once would collide.)
        for (i = 0; i < NVEC; i = i + 1)
            transact(0, i, vec[i][79:72], vec[i][71:56], 8'h00, 1, vec[i][55:0]);
        // After the vectors a QUERY must return the same state, with
        // status = accepted | cmd 5 | LB = 8  -> 0x4B.
        last = {8'h4B, vec[NVEC-1][47:0]};
        transact(0, 9000, 8'd5, 16'd0, 8'h00, 1, last);
        transact(0, 9001, 8'd1, 16'd8, 8'h01, 0, 56'd0);    // bad checksum
        transact(0, 9002, 8'd6, 16'd0, 8'h00, 0, 56'd0);    // unknown command
        transact(0, 9003, 8'd0, 16'd0, 8'h00, 0, 56'd0);    // unknown command
        send_byte(0, 8'h33);                                 // junk before a frame
        transact(0, 9004, 8'd5, 16'd0, 8'h00, 1, last);
        send_byte(0, 8'hA5); send_byte(0, 8'h01);            // frame cut short...
        #(500 * CLK_NS);                                     // ...then a pause > TIMEOUT
        transact(0, 9005, 8'd5, 16'd0, 8'h00, 1, last);     // must resync

        // KEY0 restarts: afterwards the state is the power-on one (49/51, d=0).
        key[0] = 0; #(50 * CLK_NS); key[0] = 1; #(50 * CLK_NS);
        transact(0, 9006, 8'd5, 16'd0, 8'h00, 1, {8'h4B, 8'd49, 8'd51, 16'd0, 16'd0});

        for (j = 0; j < 60; j = j + 1)
            transact(1, j, vec[j][79:72], vec[j][71:56], 8'h00, 1, vec[j][55:0]);
        if (errors[0] + errors[1] != 0) $fatal(1, "%0d errors", errors[0] + errors[1]);
        $display("PASS: fast link %0d vectors + 6 framing cases + KEY0 restart; 115200-baud link 60 vectors", NVEC);
        $display("      latency field: %0d clocks (fast), %0d clocks (115200) on every reply", lat[0], lat[1]);
        $finish;
    end
endmodule
