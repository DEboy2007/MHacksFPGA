// blinky_tb.v - testbench: a fake "board" that runs blinky in simulation
`timescale 1ns/1ns
module blinky_tb;
    reg        clk = 0;
    reg  [3:0] sw  = 4'b0000;
    wire [3:0] led_green;
    wire       led_red;

    // Pretend the clock is 10 Hz so the LED toggles every 5 ticks
    // (instead of waiting 6 million ticks).
    blinky #(.CLK_HZ(10)) dut (.CLOCK_12(clk), .SW(sw), .LEDG(led_green), .LEDR(led_red));

    always #5 clk = ~clk;                  // a tick every 10 ns

    initial begin
        $dumpfile("blinky.vcd");           // waveform file you can view
        $dumpvars(0, blinky_tb);
        $monitor("t=%0t  sw=%b  green=%b  red=%b", $time, sw, led_green, led_red);
        #120 sw = 4'b1010;                 // flip some switches
        #120 sw = 4'b0110;
        #120 $finish;
    end
endmodule