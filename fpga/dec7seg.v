// dec7seg.v - decimal displays.
//
// dec7seg:  one decimal digit (0-9), or a dash, on a seven-segment display.
// bin2dec2: splits a number 0-99 into its tens and ones digits.
// Segments are active LOW on this board: 0 = lit. Bit 0 = a ... bit 6 = g.
module dec7seg (
    input  wire [3:0] digit,
    input  wire       dash,     // 1 = show "-" (used for a pulled quote)
    output reg  [6:0] seg
);
    always @(*) begin
        case (digit)            //  gfedcba
            4'd0: seg = 7'b1000000;
            4'd1: seg = 7'b1111001;
            4'd2: seg = 7'b0100100;
            4'd3: seg = 7'b0110000;
            4'd4: seg = 7'b0011001;
            4'd5: seg = 7'b0010010;
            4'd6: seg = 7'b0000010;
            4'd7: seg = 7'b1111000;
            4'd8: seg = 7'b0000000;
            4'd9: seg = 7'b0010000;
            default: seg = 7'b1111111;      // blank
        endcase
        if (dash) seg = 7'b0111111;         // only the middle segment
    end
endmodule

// There is no divider on this chip, so "divide by ten" is done by comparing:
// the tens digit is the largest t with 10*t <= value. Prices are 0-99, so
// nine comparisons cover it. ones = value - 10*tens, and 10*t = 8*t + 2*t.
module bin2dec2 (
    input  wire [6:0] value,    // 0-99
    output reg  [3:0] tens,
    output wire [3:0] ones
);
    always @(*) begin
        if      (value >= 7'd90) tens = 4'd9;
        else if (value >= 7'd80) tens = 4'd8;
        else if (value >= 7'd70) tens = 4'd7;
        else if (value >= 7'd60) tens = 4'd6;
        else if (value >= 7'd50) tens = 4'd5;
        else if (value >= 7'd40) tens = 4'd4;
        else if (value >= 7'd30) tens = 4'd3;
        else if (value >= 7'd20) tens = 4'd2;
        else if (value >= 7'd10) tens = 4'd1;
        else                     tens = 4'd0;
    end
    wire [6:0] ten_times = ({3'b000, tens} << 3) + ({3'b000, tens} << 1);
    wire [6:0] rest = value - ten_times;
    assign ones = rest[3:0];
endmodule
