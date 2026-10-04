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
// you can watch on the hex displays, then wait for KEY0 again. During a run:
// KEY0 starts over, KEY1 is the kill switch, KEY3 pauses, KEY2 resumes.
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

class LineSource {
public:
    explicit LineSource(const std::string& cmd) {
        int pipefd[2];
        if (pipe(pipefd) != 0) die("source pipe failed");
        pid = fork();
        if (pid < 0) die("source fork failed");
        if (pid == 0) {
            dup2(pipefd[1], STDOUT_FILENO);
            close(pipefd[0]); close(pipefd[1]);
            execl("/bin/sh", "sh", "-c", ("exec " + cmd).c_str(), static_cast<char*>(nullptr));
            _exit(127);
        }
        close(pipefd[1]);
        in = fdopen(pipefd[0], "r");
        if (!in) die("source fdopen failed");
    }
    ~LineSource() {
        if (in) fclose(in);
        int st; waitpid(pid, &st, 0);
    }
    bool read_line(std::string& line) {
        char* buf = nullptr;
        size_t cap = 0;
        ssize_t n = getline(&buf, &cap, in);
        if (n < 0) { free(buf); return false; }
        line.assign(buf, static_cast<size_t>(n));
        free(buf);
        return true;
    }
private:
    FILE* in = nullptr;
    pid_t pid = -1;
};

// ---- protocol ----------------------------------------------------------------

enum : uint8_t { CMD_BUY = 1, CMD_SELL = 2, CMD_CONFIG = 3, CMD_RESET = 4,
                 CMD_QUERY = 5, CMD_REFERENCE = 6 };

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
    Session(Transport& t, FILE* quotes, double rate) : tr(t), qf(quotes) { set_rate(rate); }

    Quote request(uint8_t cmd, uint16_t arg) {
        // Pacing: requests leave on a fixed schedule so that every market
        // maker sees the same arrival rate. (In demo mode the pace is per
        // event instead; see pace_event.)
        if (gap_ns && !demo) {
            while (now_ns() < next_slot) { /* spin: sleeping would add its own jitter */ }
            next_slot = now_ns() + gap_ns;
        }
        uint8_t out[5] = {0xA5, cmd, static_cast<uint8_t>(arg), static_cast<uint8_t>(arg >> 8), 0};
        out[4] = out[0] ^ out[1] ^ out[2] ^ out[3];
        uint8_t in[13];
        const uint64_t t0 = now_ns();
        tr.write_all(out, sizeof out);
        for (;;) {
            if (!tr.read_exact(in, sizeof in, demo ? 500 : 2000)) {
                if (demo) throw Restarted{false};          // KEY0 is being held down
                die("no reply from market maker (request " + std::to_string(count) + ")");
            }
            if (in[0] != 0x5A || xor_all(in) != 0) die("corrupt reply (request " + std::to_string(count) + ")");
            if (!(in[1] & 0x80)) break;                    // our reply
            if (!demo) die("a button was pressed on the board during the run");
            on_notice(in);                                 // a button; our reply is still coming
        }
        last = parse(in);
        last.rt_ns = now_ns() - t0;
        if (qf && record) fwrite(in, 1, 8, qf);
        ++count;
        return last;
    }

    // Demo mode: wait for the next event's time slot. Button notices are
    // handled while waiting, and a pause holds us here until resume.
    void pace_event(size_t next_event) {
        bool announced = false;
        for (;;) {
            if (paused) {
                if (!announced) {
                    printf("-- paused before order #%zu (KEY2 resumes) --\n", next_event);
                    fflush(stdout);
                    announced = true;
                }
                poll_notices(200);
                if (!paused) { printf("-- resumed --\n"); next_slot = now_ns() + gap_ns; }
                continue;
            }
            const int64_t left_ms = (static_cast<int64_t>(next_slot) - static_cast<int64_t>(now_ns())) / 1000000;
            if (left_ms <= 0) break;
            poll_notices(static_cast<int>(left_ms));
        }
        next_slot = now_ns() + gap_ns;
    }

    // Block until KEY0 is released (restart notice).
    void wait_for_restart() {
        for (;;) {
            try { poll_notices(1000); } catch (const Restarted&) { return; }
        }
    }
    void set_rate(double rate) { gap_ns = rate > 0 ? static_cast<uint64_t>(1e9 / rate) : 0; }

    Quote last;                 // the market maker's latest quote, from replies and notices
    uint64_t count = 0;
    bool record = true;
    bool demo = false;
    bool paused = false;

private:
    static uint8_t xor_all(const uint8_t* in) {
        uint8_t x = 0;
        for (int i = 0; i < 13; i++) x ^= in[i];
        return x;
    }
    static Quote parse(const uint8_t* in) {
        Quote q;
        q.status = in[1]; q.bid = in[2]; q.ask = in[3];
        q.d = static_cast<int16_t>(in[4] | (in[5] << 8));
        q.seq = in[6] | (in[7] << 8);
        q.latency = in[8] | (in[9] << 8) | (in[10] << 16) | (static_cast<uint32_t>(in[11]) << 24);
        return q;
    }
    // Read one frame if one arrives within timeout_ms, and act on it if it
    // is a notice.
    void poll_notices(int timeout_ms) {
        uint8_t in[13];
        if (!tr.read_exact(in, 1, timeout_ms) || in[0] != 0x5A) return;
        if (!tr.read_exact(in + 1, 12, 200) || xor_all(in) != 0) return;
        if (in[1] & 0x80) on_notice(in);
    }
    // A notice is a frame the board sends unasked when a button is pressed:
    // status bit 7 set, type in the cmd bits (docs/spec.md section 10).
    void on_notice(const uint8_t* in) {
        const int type = (in[1] >> 1) & 7;
        if (type == 0) throw Restarted{true};              // KEY0
        last = parse(in);
        if (type == 1) printf("-- KEY1: kill switch %s --\n", (in[1] & 0x10) ? "ON, quotes pulled" : "off, quoting again");
        if (type == 2) paused = true;                      // KEY3
        if (type == 3) paused = false;                     // KEY2
        if (type == 4) printf("-- switches changed: now quoting bid %d ask %d --\n", last.bid, last.ask);
        fflush(stdout);
    }

    Transport& tr;
    FILE* qf;
    uint64_t gap_ns = 0, next_slot = 0;
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
        if (ses.demo) {
            ses.pace_event(i);
            q = ses.last;                 // a button may have changed the quote
        }

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
            const bool applied = (q.status & 1) && q.d == d_before + (action == 1 ? e.qty : -e.qty);
            if (!applied && !ses.demo) die("market maker did not apply fill at event " + std::to_string(i));
            if (!applied) action = 0;     // demo: the kill switch got there first
        }
        if (action) {
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

// Live source protocol:
//   BOOK <bid> <bid_size> <ask> <ask_size>
//   TRADE <price> <size>       (observation only; never a fill)
//   STALE / FRESH
static void run_live(Session& ses, LineSource& source) {
    bool fresh = false;
    std::string line;
    while (source.read_line(line)) {
        double bid, bid_size, ask, ask_size;
        char kind[16];
        if (sscanf(line.c_str(), "%15s", kind) != 1) continue;
        if (!strcmp(kind, "STALE")) { fresh = false; continue; }
        if (!strcmp(kind, "FRESH")) { fresh = true; continue; }
        if (!strcmp(kind, "TRADE")) continue;
        if (strcmp(kind, "BOOK") != 0 ||
            sscanf(line.c_str(), "%*s %lf %lf %lf %lf",
                   &bid, &bid_size, &ask, &ask_size) != 4) continue;
        if (bid < 0 || ask > 1 || bid > ask) { fresh = false; continue; }
        const int ref = std::max(0, std::min(100, static_cast<int>((bid + ask) * 50.0)));
        Quote q = ses.request(CMD_REFERENCE, static_cast<uint16_t>(ref));
        if (!fresh) continue;
        int action = 0;
        int qty = 0;
        if (q.ask && bid >= q.ask / 100.0) {
            action = 1;
            qty = std::min(8, static_cast<int>(bid_size));
        } else if (q.bid && ask <= q.bid / 100.0) {
            action = 2;
            qty = std::min(8, static_cast<int>(ask_size));
        }
        if (action && qty > 0) {
            const int before = q.d;
            q = ses.request(action == 1 ? CMD_BUY : CMD_SELL, static_cast<uint16_t>(qty));
            if (!(q.status & 1) || q.d == before) continue;
            printf("live fill %s %d @ %d cents, d=%d\n",
                   action == 1 ? "buy_yes" : "sell_yes", qty,
                   action == 1 ? q.ask : q.bid, q.d);
            fflush(stdout);
        }
    }
}

// ---- main ----------------------------------------------------------------------

int main(int argc, char** argv) {
    std::string events_path, mm, source_path, quotes_path, log_path;
    double rate = 0;
    bool demo = false;
    for (int i = 1; i < argc; i += 2) {
        std::string a = argv[i];
        if (a == "--demo") { demo = true; i--; continue; }
        if (i + 1 >= argc) die("missing value for " + a);
        std::string v = argv[i + 1];
        if      (a == "--events") events_path = v;
        else if (a == "--mm")     mm = v;
        else if (a == "--source") source_path = v;
        else if (a == "--quotes") quotes_path = v;
        else if (a == "--log")    log_path = v;
        else if (a == "--rate")   rate = atof(v.c_str());
        else die("unknown option " + a);
    }
    if (mm.empty() || (events_path.empty() == source_path.empty()))
        die("usage: exchange --events FILE | --source cmd:COMMAND --mm serial:PORT|cmd:COMMAND [--quotes FILE] [--log FILE] [--rate REQ_PER_SEC] [--demo]");
    signal(SIGPIPE, SIG_IGN);

    if (!source_path.empty() && source_path.rfind("cmd:", 0) != 0)
        die("--source must start with cmd:");
    std::unique_ptr<Transport> tr;
    if (mm.rfind("serial:", 0) == 0)   tr = std::make_unique<SerialTransport>(mm.substr(7));
    else if (mm.rfind("cmd:", 0) == 0) tr = std::make_unique<ChildTransport>(mm.substr(4));
    else die("--mm must start with serial: or cmd:");

    FILE* qf = quotes_path.empty() ? nullptr : fopen(quotes_path.c_str(), "wb");
    FILE* lf = log_path.empty() ? nullptr : fopen(log_path.c_str(), "w");
    if ((!quotes_path.empty() && !qf) || (!log_path.empty() && !lf)) die("cannot open output file");
    if (lf) fprintf(lf, "event,informed,action,qty,px,bid,ask,d,seq,mm_latency,rt_ns,truth_bp,cash,mtm\n");

    Session ses(*tr, qf, rate);
    if (!source_path.empty()) {
        LineSource source(source_path.substr(4));
        run_live(ses, source);
        if (qf) fclose(qf);
        if (lf) fclose(lf);
        return 0;
    }
    const EventFile ef = load_events(events_path);
    if (!demo) {
        run(ses, ef, lf, false);
    } else {
        ses.demo = true;
        ses.set_rate(rate > 0 ? rate : 20);
        bool notice_seen = false;
        for (;;) {
            if (!notice_seen) {
                printf("\nPress KEY0 on the board to start the run.\n"
                       "(KEY1 kill switch on/off, KEY3 pause, KEY2 resume, KEY0 start over)\n");
                fflush(stdout);
                ses.wait_for_restart();
            }
            tr->drain();
            ses.paused = false;
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
