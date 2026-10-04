// lmsr.hpp - C++ implementation of docs/spec.md: the same integers as the
// FPGA core (fpga/lmsr_core.v) and the Python golden model (golden/lmsr_mm.py).
#pragma once
#include <cstdint>
#include <fstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace lmsr {

constexpr int F = 16;                       // fractional bits of a cent
constexpr int LOG2N = 8;                    // table steps per unit of d/b
constexpr int ENTRIES = 2048;
constexpr int KMAX = ENTRIES - 1;
constexpr int64_t LIN = 100LL << (F - LOG2N);
constexpr int64_t ONE = 1LL << F;

constexpr uint8_t SYNC_IN = 0xA5, SYNC_OUT = 0x5A;
constexpr uint8_t CMD_BUY = 1, CMD_SELL = 2, CMD_CONFIG = 3, CMD_RESET = 4, CMD_QUERY = 5, CMD_REFERENCE = 6;
constexpr int IN_LEN = 5, OUT_LEN = 13;

inline std::vector<uint32_t> load_table(const std::string& path) {
    std::ifstream f(path);
    if (!f) throw std::runtime_error("cannot open table " + path);
    std::vector<uint32_t> g;
    std::string line;
    while (f >> line) g.push_back(static_cast<uint32_t>(std::stoul(line, nullptr, 16)));
    if (g.size() != ENTRIES) throw std::runtime_error("table must have 2048 entries");
    return g;
}

struct Reply {
    uint8_t  status;
    uint8_t  bid_px, ask_px;     // 0 = pulled
    int16_t  d;
    uint16_t seq;
};

class MarketMaker {
public:
    explicit MarketMaker(std::vector<uint32_t> table) : G(std::move(table)) { requote(); }

    // Apply one request. Returns false for an unknown command (no reply).
    bool handle(uint8_t cmd, uint16_t arg, Reply& out) {
        bool ok = false;
        const int s = 1 << ls;
        switch (cmd) {
        case CMD_BUY:
            if (ask_px && arg >= 1 && arg <= s) { d += arg; ++fills; ok = true; }
            break;
        case CMD_SELL:
            if (bid_px && arg >= 1 && arg <= s) { d -= arg; ++fills; ok = true; }
            break;
        case CMD_CONFIG: {
            const int nlb = 6 + (arg & 3), nls = (arg >> 2) & 15;
            if (nlb <= 8 && nls <= nlb && arg < (1 << 11)) {
                lb = nlb; ls = nls; hs = (arg >> 6) & 15; kill = (arg >> 10) & 1;
                ok = true;
            }
            break;
        }
        case CMD_RESET: d = 0; fills = 0; reference = 50; ok = true; break;
        case CMD_QUERY: ok = true; break;
        case CMD_REFERENCE:
            if (arg <= 100) { reference = arg; ok = true; }
            break;
        default: return false;
        }
        requote();
        out.status = static_cast<uint8_t>(ok | (cmd << 1) | (kill << 4) | ((lb - 6) << 5));
        out.bid_px = static_cast<uint8_t>(bid_px);
        out.ask_px = static_cast<uint8_t>(ask_px);
        out.d      = static_cast<int16_t>(d);
        out.seq    = static_cast<uint16_t>(fills);
        return true;
    }

    int bid() const { return bid_px; }
    int ask() const { return ask_px; }

private:
    // 100 * 2^F * softplus(k / 256), for -KMAX <= k <= KMAX.
    int64_t H(int k) const { return k >= 0 ? G[k] + k * LIN : G[-k]; }

    // Recompute the quote from the current state (spec section 3).
    void requote() {
        const int s = 1 << ls, up = lb - ls, scale = 1 << (LOG2N - lb);
        const int k0 = d * scale, kp = (d + s) * scale, km = (d - s) * scale;

        ask_px = 0;
        if (!kill && k0 >= -KMAX && kp <= KMAX) {
            const int64_t ask_fp = (H(kp) - H(k0)) << up;
            const int64_t ask = ((ask_fp + ONE - 1) >> F) + hs;      // ceil, then widen
            if (ask >= 1 && ask <= 99) ask_px = static_cast<int>(ask);
        }
        bid_px = 0;
        if (!kill && km >= -KMAX && k0 <= KMAX) {
            const int64_t bid_fp = (H(k0) - H(km)) << up;
            const int64_t bid = (bid_fp >> F) - hs;                  // floor, then widen
            if (bid >= 1 && bid <= 99) bid_px = static_cast<int>(bid);
        }
        // REFERENCE: move each live side by (reference - 50) cents and pull
        // it if that takes it outside 1..99.
        const int shift = reference - 50;
        if (bid_px) bid_px = (bid_px + shift >= 1 && bid_px + shift <= 99) ? bid_px + shift : 0;
        if (ask_px) ask_px = (ask_px + shift >= 1 && ask_px + shift <= 99) ? ask_px + shift : 0;
    }

    std::vector<uint32_t> G;
    int      lb = 8, ls = 3, hs = 0;
    bool     kill = false;
    int      d = 0;              // net YES shares sold
    uint32_t fills = 0;
    int      bid_px = 0, ask_px = 0;
    int      reference = 50;
};

inline uint8_t xor8(const uint8_t* p, int n) {
    uint8_t x = 0;
    for (int i = 0; i < n; i++) x ^= p[i];
    return x;
}

}  // namespace lmsr
