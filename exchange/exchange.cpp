// exchange - replays a trader-intent file (from gen/) against a market maker.
//
// For each event it looks at the market maker's current quote, decides whether
// the trader trades, sends the fill, and reads back the new quote. It tracks
// the market maker's inventory and P&L and writes:
//   --quotes FILE  the first 8 bytes of every reply (everything except the
//                  latency field): must be identical across FPGA, C++, Python
//   --log FILE     CSV, one row per event, for the dashboard and latency plots
//
// The market maker is reached through one Transport: a serial port (FPGA) or
// a child process's stdin/stdout (C++ and Python).
//
//   exchange --events ev.bin --mm serial:/dev/cu.usbmodem2103
//   exchange --events ev.bin --mm "cmd:mm_cpp/mm_cpp" --rate 400
//
// --demo (FPGA only): wait for KEY0 on the board, replay the stream at a pace
// you can watch on the hex displays, then wait for KEY0 again. Pressing KEY0
// in the middle of a run starts it over.
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <sys/wait.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

static uint64_t now_ns() { return clock_gettime_nsec_np(CLOCK_UPTIME_RAW); }

[[noreturn]] static void die(const std::string& msg) {
    fprintf(stderr, "exchange: %s\n", msg.c_str());
    exit(1);
}

// ---- transport ---------------------------------------------------------------

class Transport {
public:
    virtual ~Transport() = default;
    void write_all(const uint8_t* p, size_t n) {
        while (n > 0) {
            ssize_t w = ::write(wfd, p, n);
            if (w < 0) { if (errno == EINTR || errno == EAGAIN) continue; die("write failed"); }
            p += w; n -= static_cast<size_t>(w);
        }
    }
    // Reads exactly n bytes; false on timeout or end of stream.
    bool read_exact(uint8_t* p, size_t n, int timeout_ms) {
        const uint64_t deadline = now_ns() + static_cast<uint64_t>(timeout_ms) * 1000000ull;
        while (n > 0) {
            const int64_t left = static_cast<int64_t>(deadline - now_ns()) / 1000000;
            if (left <= 0) return false;
            pollfd pfd{rfd, POLLIN, 0};
            if (poll(&pfd, 1, static_cast<int>(left)) <= 0) return false;
            ssize_t r = ::read(rfd, p, n);
            if (r <= 0) { if (r < 0 && (errno == EINTR || errno == EAGAIN)) continue; return false; }
            p += r; n -= static_cast<size_t>(r);
        }
        return true;
    }
    // Throw away whatever is waiting, until the line has been quiet for 50 ms.
    void drain() {
        uint8_t junk[64];
        while (read_exact(junk, 1, 50)) {}
    }
protected:
    int rfd = -1, wfd = -1;
};

class SerialTransport : public Transport {
public:
    explicit SerialTransport(const std::string& path) {
        int fd = open(path.c_str(), O_RDWR | O_NOCTTY | O_NONBLOCK);
        if (fd < 0) die("cannot open " + path + ": " + strerror(errno));
        termios t{};
        if (tcgetattr(fd, &t) != 0) die("tcgetattr failed");
        cfmakeraw(&t);
        cfsetspeed(&t, B115200);
        t.c_cflag |= CLOCAL | CREAD;
        t.c_cc[VMIN] = 0; t.c_cc[VTIME] = 0;
        if (tcsetattr(fd, TCSANOW, &t) != 0) die("tcsetattr failed");
        usleep(200000);
        tcflush(fd, TCIOFLUSH);
        rfd = wfd = fd;
    }
    ~SerialTransport() override { close(rfd); }
};

class ChildTransport : public Transport {
public:
    explicit ChildTransport(const std::string& cmd) {
        int to_child[2], from_child[2];
        if (pipe(to_child) != 0 || pipe(from_child) != 0) die("pipe failed");
        pid = fork();
        if (pid < 0) die("fork failed");
        if (pid == 0) {
            dup2(to_child[0], STDIN_FILENO);
            dup2(from_child[1], STDOUT_FILENO);
            close(to_child[0]); close(to_child[1]); close(from_child[0]); close(from_child[1]);
            execl("/bin/sh", "sh", "-c", ("exec " + cmd).c_str(), static_cast<char*>(nullptr));
            _exit(127);
        }
        close(to_child[0]); close(from_child[1]);
        wfd = to_child[1]; rfd = from_child[0];
    }
    ~ChildTransport() override {
        close(wfd); close(rfd);
        int st; waitpid(pid, &st, 0);
    }
private:
    pid_t pid = -1;
};

// ---- protocol ----------------------------------------------------------------

enum : uint8_t { CMD_BUY = 1, CMD_SELL = 2, CMD_CONFIG = 3, CMD_RESET = 4, CMD_QUERY = 5 };

struct Quote {
    uint8_t  status = 0;
    int      bid = 0, ask = 0;      // cents; 0 = pulled
    int      d = 0;
    unsigned seq = 0;
    uint32_t latency = 0;           // MM-reported compute latency (clocks or ns)
    uint64_t rt_ns = 0;             // round trip measured here
};

// Thrown in --demo mode when the board was restarted (KEY0) during a run.
// `notice_seen` is false if we only know because the board stopped answering.
struct Restarted { bool notice_seen; };

class Session {
public:
    Session(Transport& t, FILE* quotes, double rate) : tr(t), qf(quotes) {
        if (rate > 0) gap_ns = static_cast<uint64_t>(1e9 / rate);
    }
    Quote request(uint8_t cmd, uint16_t arg) {
        // Pacing: requests leave on a fixed schedule so that every market
        // maker sees the same arrival rate.
        if (gap_ns) {
            while (now_ns() < next_send) { /* spin: sleeping would add its own jitter */ }
            next_send = now_ns() + gap_ns;
        }
        uint8_t out[5] = {0xA5, cmd, static_cast<uint8_t>(arg), static_cast<uint8_t>(arg >> 8), 0};
        out[4] = out[0] ^ out[1] ^ out[2] ^ out[3];
        uint8_t in[13];
        const uint64_t t0 = now_ns();
        tr.write_all(out, sizeof out);
        if (!tr.read_exact(in, sizeof in, demo ? 500 : 2000)) {
            if (demo) throw Restarted{false};          // KEY0 is being held down
            die("no reply from market maker (request " + std::to_string(count) + ")");
        }
        Quote q;
        q.rt_ns = now_ns() - t0;
        uint8_t x = 0;
        for (uint8_t b : in) x ^= b;
        if (in[0] != 0x5A || x != 0) die("corrupt reply (request " + std::to_string(count) + ")");
        if (is_notice(in)) {
            if (demo) throw Restarted{true};
            die("the board was restarted (KEY0) during the run");
        }
        if (qf && record) fwrite(in, 1, 8, qf);
        q.status = in[1]; q.bid = in[2]; q.ask = in[3];
        q.d = static_cast<int16_t>(in[4] | (in[5] << 8));
        q.seq = in[6] | (in[7] << 8);
        q.latency = in[8] | (in[9] << 8) | (in[10] << 16) | (static_cast<uint32_t>(in[11]) << 24);
        ++count;
        return q;
    }
    // A restart notice is a reply with cmd = 0: the board sends one, unasked,
    // when KEY0 is released (and at power-on).
    static bool is_notice(const uint8_t* in) { return ((in[1] >> 1) & 7) == 0; }

    // Block until the board sends a restart notice.
    void wait_for_notice() {
        uint8_t in[13];
        for (;;) {
            if (!tr.read_exact(in, 1, 1000) || in[0] != 0x5A) continue;
            if (!tr.read_exact(in + 1, 12, 200)) continue;
            uint8_t x = 0;
            for (uint8_t b : in) x ^= b;
            if (x == 0 && is_notice(in)) return;
        }
    }
    void set_rate(double rate) { gap_ns = rate > 0 ? static_cast<uint64_t>(1e9 / rate) : 0; }

    uint64_t count = 0;
    bool record = true;
    bool demo = false;
private:
    Transport& tr;
    FILE* qf;
    uint64_t gap_ns = 0, next_send = 0;
};

// ---- event file (docs/spec.md section 11) --------------------------------------

struct Event { uint8_t informed, side; uint16_t qty, truth_bp; };

struct EventFile {
    uint16_t config = 0;
    uint64_t seed = 0;
    bool outcome_yes = false;
    std::vector<Event> events;
};

static uint32_t le(const uint8_t* p, int n) {
    uint32_t v = 0;
    for (int i = n - 1; i >= 0; i--) v = (v << 8) | p[i];
    return v;
}

static EventFile load_events(const std::string& path) {
    FILE* f = fopen(path.c_str(), "rb");
    if (!f) die("cannot open " + path);
    uint8_t h[32];
    if (fread(h, 1, 32, f) != 32 || memcmp(h, "LMEV", 4) != 0 || le(h + 4, 2) != 1) die("bad event file " + path);
    EventFile ef;
    ef.config = static_cast<uint16_t>(le(h + 6, 2));
    ef.seed = le(h + 12, 4) | (static_cast<uint64_t>(le(h + 16, 4)) << 32);
    ef.outcome_yes = h[28] != 0;
    const uint32_t n = le(h + 8, 4);
    ef.events.resize(n);
    for (uint32_t i = 0; i < n; i++) {
        uint8_t e[8];
        if (fread(e, 1, 8, f) != 8) die("event file truncated");
        ef.events[i] = {e[0], e[1], static_cast<uint16_t>(le(e + 2, 2)), static_cast<uint16_t>(le(e + 4, 2))};
    }
    fclose(f);
    return ef;
}

// ---- one replay ------------------------------------------------------------

// Replay the whole event file once.
static void run(Session& ses, const EventFile& ef, FILE* lf, bool show) {
    // The reply to CONFIG still reflects whatever the market maker was doing
    // before we connected, so it is left out of the recorded quote stream.
    ses.record = false;
    Quote q = ses.request(CMD_CONFIG, ef.config);
    if (!(q.status & 1)) die("market maker rejected the config");
    ses.record = true;
    q = ses.request(CMD_RESET, 0);

    // All money in cents. `cash` is what the market maker has collected;
    // it has sold q.d net YES shares, so it owes 100 * d if YES happens.
    long long cash = 0;
    double mark = 50;                    // last mid price, for mark-to-market
    unsigned long fills = 0, informed_fills = 0;
    const uint64_t t_start = now_ns();

    for (size_t i = 0; i < ef.events.size(); i++) {
        const Event& e = ef.events[i];
        // 0 = no trade, 1 = trader buys YES at our ask, 2 = trader sells at our bid.
        int action = 0;
        if (e.informed) {
            if (q.ask && q.ask * 100 < e.truth_bp)      action = 1;   // YES is cheap
            else if (q.bid && q.bid * 100 > e.truth_bp) action = 2;   // YES is dear
        } else if ((e.side == 1 && q.ask) || (e.side == 2 && q.bid)) {
            action = e.side;
        }

        int px = 0;
        if (action) {
            px = (action == 1) ? q.ask : q.bid;
            const int d_before = q.d;
            q = ses.request(action == 1 ? CMD_BUY : CMD_SELL, e.qty);
            if (!(q.status & 1) || q.d != d_before + (action == 1 ? e.qty : -e.qty))
                die("market maker did not apply fill at event " + std::to_string(i));
            cash += (action == 1 ? 1LL : -1LL) * px * e.qty;
            ++fills;
            informed_fills += e.informed;
        }
        if (q.bid && q.ask) mark = (q.bid + q.ask) / 2.0;
        else if (q.bid)     mark = q.bid;
        else if (q.ask)     mark = q.ask;
        if (show && action)
            printf("#%-5zu %-8s %s %3d @ %2dc  ->  bid %2d  ask %2d  inventory %5d  fills %u\n",
                   i, e.informed ? "informed" : "noise", action == 1 ? "buys " : "sells", e.qty, px,
                   q.bid, q.ask, -q.d, q.seq);
        if (lf) fprintf(lf, "%zu,%d,%d,%d,%d,%d,%d,%d,%u,%u,%llu,%d,%lld,%.1f\n",
                        i, e.informed, action, action ? e.qty : 0, px, q.bid, q.ask, q.d, q.seq,
                        action ? q.latency : 0u, action ? static_cast<unsigned long long>(q.rt_ns) : 0ull,
                        e.truth_bp, cash, cash - q.d * mark);
    }
    const double secs = (now_ns() - t_start) / 1e9;

    const double truth = ef.events.empty() ? 50 : ef.events.back().truth_bp / 100.0;
    printf("events %zu, fills %lu (%lu informed), %.2f s, %.0f requests/s\n",
           ef.events.size(), fills, informed_fills, secs, fills / secs);
    printf("final quote: bid %d ask %d, d %d (inventory %d YES)\n", q.bid, q.ask, q.d, -q.d);
    printf("P&L in dollars: marked at own mid %.2f | at true probability %.2f | at resolution (%s) %.2f\n",
           (cash - q.d * mark) / 100.0, (cash - q.d * truth) / 100.0,
           ef.outcome_yes ? "YES" : "NO", (cash - (ef.outcome_yes ? 100LL * q.d : 0)) / 100.0);
}

// ---- main ----------------------------------------------------------------------

int main(int argc, char** argv) {
    std::string events_path, mm, quotes_path, log_path;
    double rate = 0;
    bool demo = false;
    for (int i = 1; i < argc; i += 2) {
        std::string a = argv[i];
        if (a == "--demo") { demo = true; i--; continue; }
        if (i + 1 >= argc) die("missing value for " + a);
        std::string v = argv[i + 1];
        if      (a == "--events") events_path = v;
        else if (a == "--mm")     mm = v;
        else if (a == "--quotes") quotes_path = v;
        else if (a == "--log")    log_path = v;
        else if (a == "--rate")   rate = atof(v.c_str());
        else die("unknown option " + a);
    }
    if (events_path.empty() || mm.empty())
        die("usage: exchange --events FILE --mm serial:PORT|cmd:COMMAND [--quotes FILE] [--log FILE] [--rate REQ_PER_SEC] [--demo]");
    signal(SIGPIPE, SIG_IGN);

    const EventFile ef = load_events(events_path);
    std::unique_ptr<Transport> tr;
    if (mm.rfind("serial:", 0) == 0)   tr = std::make_unique<SerialTransport>(mm.substr(7));
    else if (mm.rfind("cmd:", 0) == 0) tr = std::make_unique<ChildTransport>(mm.substr(4));
    else die("--mm must start with serial: or cmd:");

    FILE* qf = quotes_path.empty() ? nullptr : fopen(quotes_path.c_str(), "wb");
    FILE* lf = log_path.empty() ? nullptr : fopen(log_path.c_str(), "w");
    if ((!quotes_path.empty() && !qf) || (!log_path.empty() && !lf)) die("cannot open output file");
    if (lf) fprintf(lf, "event,informed,action,qty,px,bid,ask,d,seq,mm_latency,rt_ns,truth_bp,cash,mtm\n");

    Session ses(*tr, qf, rate);
    if (!demo) {
        run(ses, ef, lf, false);
    } else {
        ses.demo = true;
        ses.set_rate(rate > 0 ? rate : 20);
        bool notice_seen = false;
        for (;;) {
            if (!notice_seen) {
                printf("\nPress KEY0 on the board to start the run.\n");
                fflush(stdout);
                ses.wait_for_notice();
            }
            tr->drain();
            printf("KEY0 pressed: replaying %zu events\n", ef.events.size());
            try {
                run(ses, ef, nullptr, true);
                notice_seen = false;
            } catch (const Restarted& r) {
                printf("\n-- restarted from the board --\n");
                notice_seen = r.notice_seen;
            }
        }
    }
    if (qf) fclose(qf);
    if (lf) fclose(lf);
    return 0;
}
