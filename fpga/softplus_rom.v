// softplus_rom.v - the lookup table G[j] from docs/spec.md section 2.
//
// Written this way (an array, $readmemh, and a registered read) the tools
// build it out of the FPGA's block RAMs instead of thousands of logic cells.
// Block RAM is "synchronous": you give it an address on one clock edge and
// the data appears after the NEXT edge. The quote core is built around that
// one-cycle delay.
module softplus_rom #(parameter HEX_FILE = "../tables/softplus_tail.hex") (
    input  wire        clk,
    input  wire [10:0] addr,
    output reg  [23:0] data
);
    reg [23:0] mem [0:2047];
    initial $readmemh(HEX_FILE, mem);
    always @(posedge clk) data <= mem[addr];
endmodule
