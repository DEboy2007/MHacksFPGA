// top_tb.v - M3 simulation: talk to the whole design over its serial pins,
// exactly as the Mac will, and compare every reply byte with the golden model.
//
// Two copies of the design are tested one after the other:
//   [0] fast: 8 clocks per bit, so all 6971 vectors run in reasonable time,
//             followed by the bad-frame and timeout cases.
//   [1] real: 104 clocks per bit, driven at a true 115200 baud (which is not
//             exactly 104 clocks), for the first 60 vectors.
`timescale 1ns/1ps
module top_tb;
    localparam NVEC = 6971;
    localparam real CLK_NS = 83.334;
    // Restart notice: status 0xC1 = notice | LB 8 | type 0 | ok, quote 49/51.
    localparam [55:0] NOTICE = {8'hC1, 8'd49, 8'd51, 16'd0, 16'd0};

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
         .SW(18'd0),
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
        begin
            send_frame(sel, c, a, xor_flip);
            check_reply(sel, tag, expect_reply, 1, want);
        end
    endtask

    // Wait for a frame from the design and check it. check_lat = 0 skips the
    // "same latency as every other reply" check (used for restart notices).
    task check_reply(input integer sel, input integer tag, input expect_reply,
                     input check_lat, input [55:0] want);
        reg [7:0]  r [0:12];
        reg [7:0]  x;
        reg        got;
        reg [31:0] l;
        integer    n;
        begin
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
                if (check_lat && lat[sel] < 0) lat[sel] = l;
                if (r[0] !== 8'h5A || x !== 8'h00 || (check_lat && l !== lat[sel]) ||
                    {r[1], r[2], r[3], r[5], r[4], r[7], r[6]} !== want) begin
                    $display("FAIL dut %0d #%0d: sync=%h xor=%h lat=%0d got=%h want=%h",
                             sel, tag, r[0], x, l,
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
        // Both designs announce themselves once after power-on:
        check_reply(0, 8000, 1, 0, NOTICE);
        #(200 * 8680.556);                // let the 115200-baud link's notice finish too
        // (One after the other, not in parallel: the tasks above keep their
        // variables between calls, so two callers at once would collide.)
        for (i = 0; i < NVEC; i = i + 1)
            transact(0, i, vec[i][79:72], vec[i][71:56], 8'h00, 1, vec[i][55:0]);
        // After the vectors a QUERY must return the same state, with
        // status = accepted | cmd 5 | LB = 8  -> 0x4B.
        last = {8'h4B, vec[NVEC-1][47:0]};
        transact(0, 9000, 8'd5, 16'd0, 8'h00, 1, last);
        transact(0, 9001, 8'd1, 16'd8, 8'h01, 0, 56'd0);    // bad checksum
        transact(0, 9002, 8'd7, 16'd0, 8'h00, 0, 56'd0);    // unknown command
        transact(0, 9003, 8'd0, 16'd0, 8'h00, 0, 56'd0);    // unknown command
        send_byte(0, 8'h33);                                 // junk before a frame
        transact(0, 9004, 8'd5, 16'd0, 8'h00, 1, last);
        send_byte(0, 8'hA5); send_byte(0, 8'h01);            // frame cut short...
        #(500 * CLK_NS);                                     // ...then a pause > TIMEOUT
        transact(0, 9005, 8'd5, 16'd0, 8'h00, 1, last);     // must resync
        // REFERENCE 60 over the link: the 49/51 quote moves up 10 cents.
        transact(0, 9008, 8'd6, 16'd60, 8'h00, 1,
                 {8'h4D, 8'd59, 8'd61, vec[NVEC-1][31:0]});
        transact(0, 9009, 8'd5, 16'd0, 8'h00, 1,
                 {8'h4B, 8'd59, 8'd61, vec[NVEC-1][31:0]});

        // KEY0 restarts: on release the design sends a restart notice, and
        // the state is the power-on one (49/51, d=0).
        key[0] = 0; #(50 * CLK_NS); key[0] = 1;
        check_reply(0, 9006, 1, 0, NOTICE);
        transact(0, 9007, 8'd5, 16'd0, 8'h00, 1, {8'h4B, NOTICE[47:0]});

        // Decimal displays after a restart: "49" "51" "0000".
        if (hex_f[7] !== 7'b0011001 || hex_f[6] !== 7'b0010000 ||
            hex_f[5] !== 7'b0010010 || hex_f[4] !== 7'b1111001 ||
            hex_f[3] !== 7'b1000000 || hex_f[0] !== 7'b1000000) begin
            $display("FAIL: displays do not read 49 51 0000"); errors[0] = errors[0] + 1;
        end

        // Switches: SW17 up with SW9-6 = 3 gives b = 64, size 1, +3c spread.
        // The board requotes by itself and sends notice type 4 (status 0x89).
        fork
            begin sw[17] = 1; sw[9:6] = 4'd3; end
            check_reply(0, 9020, 1, 0, {8'h89, 8'd46, 8'd54, 16'd0, 16'd0});
        join
        fork
            begin sw = 18'd0; end                // back to the laptop's settings
            check_reply(0, 9021, 1, 0, {8'hC9, NOTICE[47:0]});
        join

        // KEY1 = kill switch: notice type 1, both sides pulled; again = back on.
        // (The notice starts a few clocks after the press, so we must already
        // be listening: press and listen run side by side.)
        fork
            begin key[1] = 0; #(20 * CLK_NS); key[1] = 1; end
            check_reply(0, 9010, 1, 0, {8'hD3, 8'd0, 8'd0, 16'd0, 16'd0});
        join
        if (hex_f[7] !== 7'b0111111 || hex_f[4] !== 7'b0111111) begin
            $display("FAIL: pulled quotes should show dashes"); errors[0] = errors[0] + 1;
        end
        transact(0, 9011, 8'd1, 16'd8, 8'h00, 1, {8'h52, 8'd0, 8'd0, 16'd0, 16'd0});  // buy rejected
        fork
            begin key[1] = 0; #(20 * CLK_NS); key[1] = 1; end
            check_reply(0, 9012, 1, 0, {8'hC3, NOTICE[47:0]});
        join
        // KEY3 = pause (type 2), KEY2 = resume (type 3).
        fork
            begin key[3] = 0; #(20 * CLK_NS); key[3] = 1; end
            check_reply(0, 9013, 1, 0, {8'hC5, NOTICE[47:0]});
        join
        if (ledg_f[1] !== 1'b1) begin $display("FAIL: paused LED off"); errors[0] = errors[0] + 1; end
        fork
            begin key[2] = 0; #(20 * CLK_NS); key[2] = 1; end
            check_reply(0, 9014, 1, 0, {8'hC7, NOTICE[47:0]});
        join
        if (ledg_f[1] !== 1'b0) begin $display("FAIL: paused LED on"); errors[0] = errors[0] + 1; end
        // A button pressed just as a request is being sent: the notice goes
        // out first, the request waits, and its reply follows. Nothing is lost.
        fork
            begin key[3] = 0; #(20 * CLK_NS); key[3] = 1; send_frame(0, 8'd1, 16'd8, 8'h00); end
            begin
                check_reply(0, 9015, 1, 0, {8'hC5, NOTICE[47:0]});
                check_reply(0, 9016, 1, 0, {8'h43, 8'd50, 8'd52, 16'd8, 16'd1});
            end
        join
        #(20 * CLK_NS);
        if (hex_f[0] !== 7'b1111001) begin       // one fill -> count reads 0001
            $display("FAIL: fill count display"); errors[0] = errors[0] + 1;
        end

        for (j = 0; j < 60; j = j + 1)
            transact(1, j, vec[j][79:72], vec[j][71:56], 8'h00, 1, vec[j][55:0]);
        if (errors[0] + errors[1] != 0) $fatal(1, "%0d errors", errors[0] + errors[1]);
        $display("PASS: fast link %0d vectors + framing, buttons, switches, displays; 115200-baud link 60 vectors", NVEC);
        $display("      latency field: %0d clocks (fast), %0d clocks (115200) on every reply", lat[0], lat[1]);
        $finish;
    end
endmodule
