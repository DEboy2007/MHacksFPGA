// lmsr_core_tb.v - M2 check: replay every golden vector through the core and
// compare all outputs bit-for-bit with golden/vectors.hex.
`timescale 1ns/1ps
module lmsr_core_tb;
    localparam NVEC = 6057;

    reg clk = 0;
    always #41.667 clk = ~clk;            // 12 MHz

    reg         rst = 1, req_valid = 0;
    reg  [7:0]  cmd;
    reg  [15:0] arg;
    wire        resp_valid, busy, kill;
    wire [7:0]  status;
    wire [6:0]  bid_px, ask_px;
    wire signed [15:0] d;
    wire [31:0] fills;
    wire [1:0]  lbm6;
    wire [3:0]  ls, hs;

    lmsr_core dut (.clk(clk), .rst(rst), .req_valid(req_valid), .cmd(cmd), .arg(arg),
                   .resp_valid(resp_valid), .status(status), .bid_px(bid_px),
                   .ask_px(ask_px), .d(d), .fills(fills), .lbm6(lbm6), .ls(ls),
                   .hs(hs), .kill(kill), .busy(busy));

    // cmd[79:72] arg[71:56] status[55:48] bid[47:40] ask[39:32] d[31:16] seq[15:0]
    reg [79:0] vec [0:NVEC-1];
    reg [79:0] v;
    integer i, cycles, latency, errors;

    initial begin
        $readmemh("../golden/vectors.hex", vec);
        errors = 0; latency = -1;
        repeat (4) @(posedge clk);
        rst <= 0;
        @(posedge clk);
        while (!resp_valid) @(posedge clk); // the restart notice, sent unasked
        if (status !== 8'h41 || bid_px !== 7'd49 || ask_px !== 7'd51) begin
            $display("FAIL: restart notice is status %h quote %0d/%0d, expected 41 49/51",
                     status, bid_px, ask_px);
            errors = errors + 1;
        end
        @(posedge clk);

        for (i = 0; i < NVEC; i = i + 1) begin
            v = vec[i];
            cmd <= v[79:72]; arg <= v[71:56]; req_valid <= 1;
            @(posedge clk);
            req_valid <= 0;
            cycles = 0;
            while (!resp_valid) begin @(posedge clk); cycles = cycles + 1; end

            if (latency < 0) latency = cycles;
            if (cycles !== latency) begin
                $display("FAIL vec %0d: took %0d cycles, earlier ones took %0d", i, cycles, latency);
                errors = errors + 1;
            end
            if ({status, 1'b0, bid_px, 1'b0, ask_px, d, fills[15:0]} !== v[55:0]) begin
                $display("FAIL vec %0d (cmd %0d arg %0d): got status=%h bid=%0d ask=%0d d=%0d seq=%0d, want status=%h bid=%0d ask=%0d d=%0d seq=%0d",
                         i, v[79:72], v[71:56], status, bid_px, ask_px, d, fills[15:0],
                         v[55:48], v[47:40], v[39:32], $signed(v[31:16]), v[15:0]);
                errors = errors + 1;
                if (errors > 10) $fatal(1, "too many errors");
            end
            @(posedge clk);
        end

        if (errors != 0) $fatal(1, "%0d errors", errors);
        $display("PASS: %0d vectors match; every request took %0d clocks (req_valid to resp_valid)",
                 NVEC, latency);
        $finish;
    end
endmodule
