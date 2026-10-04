`timescale 1ns/1ps
module lmsr_ui_tb;
    reg clk = 0, rst = 1, req_valid = 0;
    always #5 clk = ~clk;
    reg [7:0] cmd = 0;
    reg [15:0] arg = 0;
    reg [3:0] ui_hs = 0;
    wire resp_valid, busy, kill;
    wire [7:0] status;
    wire [6:0] bid_px, ask_px;
    wire signed [15:0] d;
    wire [31:0] fills;
    wire [1:0] lbm6;
    wire [3:0] ls, hs;

    lmsr_core dut (
        .clk(clk), .rst(rst), .req_valid(req_valid), .cmd(cmd), .arg(arg),
        .resp_valid(resp_valid), .status(status), .bid_px(bid_px),
        .ask_px(ask_px), .d(d), .fills(fills), .lbm6(lbm6), .ls(ls),
        .hs(hs), .kill(kill), .busy(busy),
        .ui_enable(1'b1), .ui_lbm6(2'd0), .ui_ls(4'd0), .ui_hs(ui_hs)
    );

    task query;
        begin
            @(negedge clk); cmd = 8'd5; arg = 0; req_valid = 1;
            @(negedge clk); req_valid = 0;
            wait (resp_valid);
        end
    endtask

    initial begin
        repeat (3) @(posedge clk);
        rst = 0;
        wait (!busy);
        query;
        // switches say b = 64 (status bits 6:5 = 0), size 1, no extra spread
        if (bid_px != 7'd49 || ask_px != 7'd51 || status[6:5] != 2'd0) $fatal(1, "UI quote failed");
        ui_hs = 4'd3;                         // 3 cents of extra spread each side
        query;
        if (bid_px != 7'd46 || ask_px != 7'd54) $fatal(1, "UI spread failed");
        $display("PASS: board switches override the quote config");
        $finish;
    end
endmodule
