// pll48.v - makes a 48 MHz clock from the board's 12 MHz one.
//
// A PLL (phase-locked loop) is a clock multiplier built into the chip. This
// one runs an internal oscillator at 768 MHz (12 MHz x 64) and divides it by
// 16 to give 48 MHz. `locked` goes high once the output is stable; the design
// stays in reset until then. Settings come from the `icepll -i 12 -o 48` tool.
module pll48 (
    input  wire clk_12,
    output wire clk_48,
    output wire locked
);
    // The _PAD kind of PLL takes its input straight from the clock pin, which
    // is how this board is wired (the 12 MHz clock arrives on a PLL pin).
    SB_PLL40_PAD #(
        .FEEDBACK_PATH("SIMPLE"),
        .DIVR(4'b0000),          // 12 MHz / 1
        .DIVF(7'b0111111),       // x 64  -> 768 MHz
        .DIVQ(3'b100),           // / 16  -> 48 MHz
        .FILTER_RANGE(3'b001)
    ) pll (
        .PACKAGEPIN(clk_12),
        .PLLOUTGLOBAL(clk_48),
        .LOCK(locked),
        .RESETB(1'b1),
        .BYPASS(1'b0),
        // unused features, tied off
        .PLLOUTCORE(),
        .EXTFEEDBACK(1'b0),
        .DYNAMICDELAY(8'd0),
        .LATCHINPUTVALUE(1'b0),
        .SDI(1'b0), .SDO(), .SCLK(1'b0)
    );
endmodule
