// mm_cpp - the C++ market maker. Speaks the binary protocol of docs/spec.md
// section 6 on stdin/stdout, one request at a time.
//
// The latency field is nanoseconds from the read() that returned the last
// request byte to the quote being ready, taken before the reply is written.
#include <cstdio>
#include <cstring>
#include <time.h>
#include <unistd.h>
#include "lmsr.hpp"

using namespace lmsr;

static bool read_exact(uint8_t* buf, int n) {
    int got = 0;
    while (got < n) {
        ssize_t r = read(STDIN_FILENO, buf + got, n - got);
        if (r <= 0) return false;
        got += static_cast<int>(r);
    }
    return true;
}

int main(int argc, char** argv) {
    const char* table = argc > 1 ? argv[1] : "tables/softplus_tail.hex";
    try {
        MarketMaker mm(load_table(table));
        uint8_t in[IN_LEN], out[OUT_LEN];
        for (;;) {
            // Wait for the sync byte, then take the rest of the frame.
            do { if (!read_exact(in, 1)) return 0; } while (in[0] != SYNC_IN);
            if (!read_exact(in + 1, IN_LEN - 1)) return 0;
            const uint64_t t0 = clock_gettime_nsec_np(CLOCK_UPTIME_RAW);

            Reply r;
            if (xor8(in, IN_LEN) != 0) continue;
            if (!mm.handle(in[1], static_cast<uint16_t>(in[2] | (in[3] << 8)), r)) continue;
            out[0] = SYNC_OUT;
            out[1] = r.status;
            out[2] = r.bid_px;
            out[3] = r.ask_px;
            out[4] = static_cast<uint8_t>(r.d);
            out[5] = static_cast<uint8_t>(static_cast<uint16_t>(r.d) >> 8);
            out[6] = static_cast<uint8_t>(r.seq);
            out[7] = static_cast<uint8_t>(r.seq >> 8);

            const uint64_t ns = clock_gettime_nsec_np(CLOCK_UPTIME_RAW) - t0;
            const uint32_t lat = ns > 0xFFFFFFFFull ? 0xFFFFFFFFu : static_cast<uint32_t>(ns);
            out[8]  = static_cast<uint8_t>(lat);
            out[9]  = static_cast<uint8_t>(lat >> 8);
            out[10] = static_cast<uint8_t>(lat >> 16);
            out[11] = static_cast<uint8_t>(lat >> 24);
            out[12] = xor8(out, OUT_LEN - 1);
            if (write(STDOUT_FILENO, out, OUT_LEN) != OUT_LEN) return 1;
        }
    } catch (const std::exception& e) {
        fprintf(stderr, "mm_cpp: %s\n", e.what());
        return 1;
    }
}
