// lmsr_core.v - the market maker itself: state, request handling and quoting.
// Follows docs/spec.md sections 3 and 4 exactly; golden/lmsr_mm.py is the
// reference. No multipliers or dividers: only adds, shifts and one table.
//
// One request takes a fixed 7 clocks from req_valid to resp_valid:
//
//   IDLE   apply the request (update d / config) using the quote we hold
//   RD_M   send table address for k-      (k- , k0, k+ = d-s, d, d+s scaled)
//   RD_0   send address for k0;  G[k-] arrives -> save H(k-)
//   RD_P   send address for k+;  G[k0] arrives -> save H(k0)
//   GET_P                        G[k+] arrives -> save H(k+)
//   DIFF   ask_diff = H(k+) - H(k0),  bid_diff = H(k0) - H(k-)
//   ROUND  shift, round to cents, add spread, apply pull rules -> new quote
//
// There is one block RAM table with one read port, so the three lookups
// happen on three consecutive clocks. Each step's result is stored in a
// register, which keeps the logic between two clock edges short.
module lmsr_core #(parameter HEX_FILE = "../tables/softplus_tail.hex") (
    input  wire        clk,
    input  wire        rst,
    // request (only looked at when busy is low)
    input  wire        req_valid,
    input  wire [7:0]  cmd,
    input  wire [15:0] arg,
    // reply: valid for one clock, values stay until the next reply
    output reg         resp_valid,
    output reg  [7:0]  status,
    output reg  [6:0]  bid_px,       // 0 = pulled
    output reg  [6:0]  ask_px,       // 0 = pulled
    output reg  signed [15:0] d,     // net YES shares sold
    output reg  [31:0] fills,
    // current config, for the board UI
    output reg  [1:0]  lbm6,         // LB - 6, so b = 64 << lbm6
    output reg  [3:0]  ls,           // quote size s = 1 << ls
    output reg  [3:0]  hs,           // extra half-spread, cents
    output reg         kill,
    output wire        busy,
    input  wire        ui_enable,
    input  wire [1:0]  ui_lbm6,
    input  wire [3:0]  ui_ls,
    input  wire [3:0]  ui_hs,
    input  wire        ui_kill
);
    localparam CMD_BUY = 8'd1, CMD_SELL = 8'd2, CMD_CONFIG = 8'd3,
               CMD_RESET = 8'd4, CMD_QUERY = 8'd5;

    localparam S_IDLE = 3'd0, S_RD_M = 3'd1, S_RD_0 = 3'd2, S_RD_P = 3'd3,
               S_GET_P = 3'd4, S_DIFF = 3'd5, S_ROUND = 3'd6;
    reg [2:0] state;
    reg       silent;      // first quote after reset: compute it, send nothing
    reg       ok_r;        // was the request accepted
    reg [2:0] cmd_r;

    assign busy = (state != S_IDLE);
    wire [1:0] lbm6_eff = ui_enable ? ui_lbm6 : lbm6;
    wire [3:0] ls_eff   = ui_enable ? ui_ls   : ls;
    wire [3:0] hs_eff   = ui_enable ? ui_hs   : hs;
    wire       kill_eff = kill | (ui_enable & ui_kill);

    // ---- table indices (plain wiring from the registers above) -------------
    wire [16:0] s      = 17'd1 << ls_eff;
    wire [1:0]  kshift = 2'd2 - lbm6_eff;                  // 8 - LB
    wire signed [19:0] s_ext = {3'b000, s};
    wire signed [19:0] d_ext = {{4{d[15]}}, d};           // sign-extended
    wire signed [19:0] k0 =  d_ext          <<< kshift;
    wire signed [19:0] kp = (d_ext + s_ext) <<< kshift;
    wire signed [19:0] km = (d_ext - s_ext) <<< kshift;

    // Pull rule 2: both lookups of a side must be inside the table.
    wire ask_in = (k0 >= -20'sd2047) && (kp <= 20'sd2047);
    wire bid_in = (km >= -20'sd2047) && (k0 <= 20'sd2047);

    // Which index is being looked up / has just arrived.
    reg signed [19:0] k_addr, k_data;
    always @(*) begin
        case (state)
            S_RD_M:  k_addr = km;
            S_RD_0:  k_addr = k0;
            default: k_addr = kp;
        endcase
        case (state)
            S_RD_0:  k_data = km;
            S_RD_P:  k_data = k0;
            default: k_data = kp;
        endcase
    end

    wire [19:0] k_abs = k_addr[19] ? -k_addr : k_addr;
    wire [23:0] g;
    softplus_rom #(.HEX_FILE(HEX_FILE)) rom
        (.clk(clk), .addr(k_abs[10:0]), .data(g));

    // H(k) = G[|k|] + (k > 0 ? k * 25600 : 0), and 25600 = 2^14 + 2^13 + 2^10.
    wire [10:0] kd  = k_data[10:0];
    wire [26:0] lin = k_data[19] ? 27'd0
                    : ({16'd0, kd} << 14) + ({16'd0, kd} << 13) + ({16'd0, kd} << 10);
    wire [26:0] h   = {3'b000, g} + lin;

    reg [26:0] hm, h0, hp;            // H(k-), H(k0), H(k+)
    reg [26:0] ask_diff, bid_diff;

    // ---- rounding (used in S_ROUND) ----------------------------------------
    // Multiply by b/s = shift left by LB - LS. In-range results fit in 23 bits.
    wire [3:0]  up     = {2'b00, lbm6_eff} + 4'd6 - ls_eff;
    wire [34:0] ask_sh = {8'd0, ask_diff} << up;
    wire [34:0] bid_sh = {8'd0, bid_diff} << up;
    wire [24:0] ask_ceil = {1'b0, ask_sh[23:0]} + 25'd65535;       // ceil
    wire signed [9:0] ask_c = {1'b0, ask_ceil[24:16]} + {6'd0, hs_eff};
    wire signed [9:0] bid_c = {2'b00, bid_sh[23:16]} - {6'd0, hs_eff}; // floor
    wire ask_live = !kill_eff && ask_in && (ask_c >= 10'sd1) && (ask_c <= 10'sd99);
    wire bid_live = !kill_eff && bid_in && (bid_c >= 10'sd1) && (bid_c <= 10'sd99);

    // ---- request checks (used in S_IDLE) -----------------------------------
    wire qty_ok = (arg != 16'd0) && ({1'b0, arg} <= s);
    wire cfg_ok = (arg[15:11] == 5'd0) && (arg[1:0] != 2'd3)
                  && (arg[5:2] <= {2'b00, arg[1:0]} + 4'd6);

    always @(posedge clk) begin
        resp_valid <= 1'b0;
        if (rst) begin
            state  <= S_RD_M;         // work out the very first quote
            silent <= 1'b1;
            d      <= 16'sd0;
            fills  <= 32'd0;
            lbm6   <= 2'd2;           // b = 256
            ls     <= 4'd3;           // s = 8
            hs     <= 4'd0;
            kill   <= 1'b0;
            bid_px <= 7'd0;
            ask_px <= 7'd0;
            status <= 8'd0;
        end else case (state)
            S_IDLE: if (req_valid) begin
                cmd_r <= cmd[2:0];
                ok_r  <= 1'b0;
                case (cmd)
                    CMD_BUY: if (!kill_eff && ask_px != 0 && qty_ok) begin
                        d     <= d + $signed(arg);
                        fills <= fills + 1;
                        ok_r  <= 1'b1;
                    end
                    CMD_SELL: if (!kill_eff && bid_px != 0 && qty_ok) begin
                        d     <= d - $signed(arg);
                        fills <= fills + 1;
                        ok_r  <= 1'b1;
                    end
                    CMD_CONFIG: if (cfg_ok) begin
                        lbm6 <= arg[1:0];
                        ls   <= arg[5:2];
                        hs   <= arg[9:6];
                        kill <= arg[10];
                        ok_r <= 1'b1;
                    end
                    CMD_RESET: begin
                        d     <= 16'sd0;
                        fills <= 32'd0;
                        ok_r  <= 1'b1;
                    end
                    default: ok_r <= (cmd == CMD_QUERY);
                endcase
                state <= S_RD_M;
            end
            S_RD_M:  state <= S_RD_0;
            S_RD_0:  begin hm <= h; state <= S_RD_P;  end
            S_RD_P:  begin h0 <= h; state <= S_GET_P; end
            S_GET_P: begin hp <= h; state <= S_DIFF;  end
            S_DIFF: begin
                ask_diff <= hp - h0;
                bid_diff <= h0 - hm;
                state    <= S_ROUND;
            end
            S_ROUND: begin
                ask_px     <= ask_live ? ask_c[6:0] : 7'd0;
                bid_px     <= bid_live ? bid_c[6:0] : 7'd0;
                status     <= {1'b0, lbm6_eff, kill_eff, cmd_r, ok_r};
                resp_valid <= !silent;
                silent     <= 1'b0;
                state      <= S_IDLE;
            end
            default: state <= S_IDLE;
        endcase
    end
endmodule
