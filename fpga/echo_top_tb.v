// echo_top_tb.v - M1 simulation: bytes sent at a true 115200 baud come back.
`timescale 1ns/1ps
module echo_top_tb;
    localparam real BIT_NS = 8680.556;    // the host's 115200 baud, not our 104 clocks

    reg clk = 0;
    always #41.667 clk = ~clk;

    reg  host_tx = 1;
    wire [1:0] pico;
    assign pico[0] = host_tx;
    wire [6:0] HEX0, HEX1, HEX2, HEX3, HEX4, HEX5, HEX6, HEX7;
    wire [3:0] LEDG;
    echo_top dut (.CLOCK_12(clk), .PICO(pico), .HEX0(HEX0), .HEX1(HEX1), .HEX2(HEX2),
                  .HEX3(HEX3), .HEX4(HEX4), .HEX5(HEX5), .HEX6(HEX6), .HEX7(HEX7),
                  .LEDG(LEDG));

    task send_byte(input [7:0] b);
        integer k;
        begin
            host_tx = 0; #(BIT_NS);
            for (k = 0; k < 8; k = k + 1) begin host_tx = b[k]; #(BIT_NS); end
            host_tx = 1; #(BIT_NS);
        end
    endtask

    task recv_byte(output [7:0] b);
        integer k;
        begin
            @(negedge pico[1]);
            #(BIT_NS * 1.5);
            for (k = 0; k < 8; k = k + 1) begin b[k] = pico[1]; #(BIT_NS); end
        end
    endtask

    reg [7:0] sent [0:5];
    reg [7:0] got;
    integer i, j;
    initial begin
        sent[0] = 8'h00; sent[1] = 8'hFF; sent[2] = 8'hA5;
        sent[3] = 8'h5A; sent[4] = 8'h01; sent[5] = 8'h80;
        #100000;
        fork
            for (i = 0; i < 6; i = i + 1) send_byte(sent[i]);   // back to back
            for (j = 0; j < 6; j = j + 1) begin
                recv_byte(got);
                if (got !== sent[j]) $fatal(1, "byte %0d: sent %h, got %h", j, sent[j], got);
            end
        join
        if (dut.count !== 8'd6 || dut.last !== 8'h80) $fatal(1, "display registers wrong");
        if (HEX0 !== 7'b1000000 || HEX1 !== 7'b0000000) $fatal(1, "HEX1-0 should show 80");
        $display("PASS: 6 bytes echoed at 115200 baud");
        $finish;
    end
endmodule
