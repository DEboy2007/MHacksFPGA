module blinky #(parameter CLK_HZ = 12_000_000) (
    input  wire       CLOCK_12,         // board clock
    input  wire [3:0] SW,          // switches SW0–SW3
    output wire [3:0] LEDG,   // green LEDs copy the switches
    output reg        LEDR      // red LED blinks once a second
);
    reg [31:0] count = 0;

    always @(posedge CLOCK_12) begin            // on every clock tick...
        if (count == CLK_HZ/2 - 1) begin   // ...after half a second's worth of ticks
            count   <= 0;
            LEDR <= ~LEDR;           // flip the LED
        end else begin
            count   <= count + 1;
        end
    end

    assign LEDG = SW;                 // plain wires, no clock involved
    initial LEDR = 0;
endmodule