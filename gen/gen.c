/* gen.c - seeded synthetic order flow for the LMSR market makers.
 *
 * Writes a binary event file (format: docs/spec.md section 11). Each event is
 * one trader arriving with an intent. Whether it becomes a fill is decided at
 * replay time by the exchange, against whatever the market maker is quoting:
 *
 *   noise    trader: wants to buy or sell `qty` regardless of price.
 *   informed trader: knows the hidden true probability and trades only when
 *                    the quote is mispriced against it.
 *
 * The truth is p0, optionally jumping to p1 at event `news_at` ("news").
 * The same file is replayed into the FPGA, C++ and Python market makers.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint64_t rng_state;

/* splitmix64: tiny, well-mixed, and identical on every platform. */
static uint64_t rng_next(void) {
    uint64_t z = (rng_state += 0x9E3779B97F4A7C15ULL);
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
    return z ^ (z >> 31);
}

/* Uniform integer in [0, n). */
static uint32_t rng_below(uint32_t n) { return (uint32_t)((rng_next() >> 32) * (uint64_t)n >> 32); }

static void put16(uint8_t *p, uint32_t v) { p[0] = v & 0xFF; p[1] = (v >> 8) & 0xFF; }
static void put32(uint8_t *p, uint32_t v) { put16(p, v); put16(p + 2, v >> 16); }
static void put64(uint8_t *p, uint64_t v) { put32(p, (uint32_t)v); put32(p + 4, (uint32_t)(v >> 32)); }

static void usage(void) {
    fprintf(stderr,
        "usage: gen -o FILE [-n events] [-seed N] [-p0 prob] [-news AT:prob]\n"
        "           [-informed frac] [-lb 6..8] [-ls 0..lb] [-hs 0..15]\n");
    exit(2);
}

int main(int argc, char **argv) {
    const char *out = NULL;
    uint32_t n = 5000, news_at = 0xFFFFFFFFu;
    uint64_t seed = 1;
    double p0 = 0.5, p1 = 0.5, informed = 0.25;
    int lb = 8, ls = 3, hs = 0;

    for (int i = 1; i < argc; i += 2) {
        const char *a = argv[i], *v = (i + 1 < argc) ? argv[i + 1] : NULL;
        if (!v) usage();
        if      (!strcmp(a, "-o"))        out = v;
        else if (!strcmp(a, "-n"))        n = (uint32_t)strtoul(v, NULL, 10);
        else if (!strcmp(a, "-seed"))     seed = strtoull(v, NULL, 10);
        else if (!strcmp(a, "-p0"))       p0 = p1 = atof(v);
        else if (!strcmp(a, "-informed")) informed = atof(v);
        else if (!strcmp(a, "-lb"))       lb = atoi(v);
        else if (!strcmp(a, "-ls"))       ls = atoi(v);
        else if (!strcmp(a, "-hs"))       hs = atoi(v);
        else if (!strcmp(a, "-news")) {
            if (sscanf(v, "%u:%lf", &news_at, &p1) != 2) usage();
        } else usage();
    }
    if (!out || lb < 6 || lb > 8 || ls < 0 || ls > lb || hs < 0 || hs > 15 ||
        p0 <= 0 || p0 >= 1 || p1 <= 0 || p1 >= 1 || informed < 0 || informed > 1) usage();

    rng_state = seed;
    uint32_t s = 1u << ls;
    uint32_t p0_bp = (uint32_t)(p0 * 10000 + 0.5), p1_bp = (uint32_t)(p1 * 10000 + 0.5);
    uint32_t informed_ppm = (uint32_t)(informed * 1000000 + 0.5);
    uint32_t final_bp = (news_at < n) ? p1_bp : p0_bp;
    uint8_t outcome = rng_below(10000) < final_bp;          /* 1 = YES happens */

    FILE *f = fopen(out, "wb");
    if (!f) { perror(out); return 1; }

    uint8_t hdr[32] = {0};
    memcpy(hdr, "LMEV", 4);
    put16(hdr + 4, 1);                                       /* version */
    put16(hdr + 6, (uint32_t)((lb - 6) | (ls << 2) | (hs << 6)));   /* CONFIG arg */
    put32(hdr + 8, n);
    put64(hdr + 12, seed);
    put16(hdr + 20, p0_bp);
    put16(hdr + 22, p1_bp);
    put32(hdr + 24, news_at);
    hdr[28] = outcome;
    fwrite(hdr, 1, sizeof hdr, f);

    for (uint32_t i = 0; i < n; i++) {
        uint8_t ev[8] = {0};
        int is_informed = rng_below(1000000) < informed_ppm;
        uint32_t side = 1 + rng_below(2);                    /* 1 = buy, 2 = sell */
        uint32_t qty  = 1 + rng_below(s);                    /* 1 .. s */
        ev[0] = (uint8_t)is_informed;
        ev[1] = is_informed ? 0 : (uint8_t)side;             /* informed: side decided at replay */
        put16(ev + 2, is_informed ? s : qty);
        put16(ev + 4, i >= news_at ? p1_bp : p0_bp);         /* truth, in 1/100 cent */
        fwrite(ev, 1, sizeof ev, f);
    }
    if (fclose(f) != 0) { perror(out); return 1; }
    fprintf(stderr, "gen: %u events, seed %llu, truth %.2f%s, informed %.0f%%, b=%d s=%u hs=%d, outcome %s -> %s\n",
            n, (unsigned long long)seed, p0, news_at < n ? " then news" : "", informed * 100,
            1 << lb, s, hs, outcome ? "YES" : "NO", out);
    return 0;
}
